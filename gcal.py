"""
Google Calendar sync for the BuzzKar Organiser.

- One calendar per person ("Tickets - Aakash"), owned by a service account (the app's "robot").
- One-way: tickets -> calendar. Only OPEN tickets WITH a due date get an all-day event.
- Closing a ticket (or removing its due date) deletes its event; reassigning moves it.
"""
import base64
import json
import os
from datetime import timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import db

SCOPES = ["https://www.googleapis.com/auth/calendar"]
PRIORITY_COLOR = {"Urgent": "11", "High": "6", "Medium": "5", "Low": "10"}  # Google event colour ids


# ---------- config ----------
def _secret(name):
    """Read from Streamlit secrets (the app) or environment variables (GitHub Actions)."""
    try:
        import streamlit as st
        value = st.secrets.get(name)
        if value:
            return value
    except Exception:
        pass
    return os.environ.get(name)


def is_configured():
    return bool(_secret("GOOGLE_SERVICE_ACCOUNT_JSON"))


def _svc():
    info = json.loads(_secret("GOOGLE_SERVICE_ACCOUNT_JSON"))
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


# ---------- calendars ----------
def _ensure_shared(svc, cal_id, emails):
    """Share the calendar (read-only) with each email that doesn't have access yet."""
    items = svc.acl().list(calendarId=cal_id).execute().get("items", [])
    have = {r["scope"]["value"].lower() for r in items if r.get("scope", {}).get("type") == "user"}
    for email in {e.lower() for e in emails if e}:
        if email not in have:
            svc.acl().insert(
                calendarId=cal_id,
                body={"role": "reader", "scope": {"type": "user", "value": email}},
                sendNotifications=True,
            ).execute()


def ensure_calendar(svc, user):
    """Return the person's calendar id, creating + sharing it the first time. None if no email yet."""
    if not user or not user["email"]:
        return None
    if user["calendar_id"]:
        return user["calendar_id"]
    cal = svc.calendars().insert(
        body={"summary": f"Tickets - {user['name']}", "timeZone": "Asia/Kolkata"}
    ).execute()
    db.set_user_calendar(user["id"], cal["id"])
    _ensure_shared(svc, cal["id"], [user["email"], *db.admin_emails()])
    return cal["id"]


def setup_calendars(svc=None):
    """Admin button: create everyone's calendar and make sure it's shared. Returns notes to show."""
    svc = svc or _svc()
    users = db.list_users_full()
    admins = [u["email"] for u in users if u["role"] == "admin" and u["email"]]
    notes = []
    for u in users:
        if not u["email"]:
            notes.append(f"{u['name']}: no email yet, skipped.")
            continue
        cal_id = ensure_calendar(svc, u)
        _ensure_shared(svc, cal_id, [u["email"], *admins])
    return notes


def add_calendar_links():
    """(name, link) pairs people can open to add their calendar if the share email doesn't arrive."""
    out = []
    for u in db.list_users_full():
        if u["calendar_id"]:
            cid = base64.b64encode(u["calendar_id"].encode()).decode().rstrip("=")
            out.append((u["name"], f"https://calendar.google.com/calendar/u/0?cid={cid}"))
    return out


# ---------- events ----------
def _event_body(t):
    due = t["due_date"]
    lines = [
        f"Client: {t['client'] or '-'}",
        f"Category: {t['category'] or '-'}",
        f"Priority: {t['priority']}",
        f"Status: {t['status']}",
        f"Assigned to: {t['assignee']}",
    ]
    if t["description"]:
        lines += ["", t["description"]]
    app_url = _secret("APP_URL")
    if app_url:
        lines += ["", f"Open in organiser: {app_url}"]
    return {
        "summary": f"#{t['id']} · {t['title']} [{t['priority']}]",
        "description": "\n".join(lines),
        "start": {"date": due.isoformat()},
        "end": {"date": (due + timedelta(days=1)).isoformat()},  # all-day events end the next day
        "colorId": PRIORITY_COLOR.get(t["priority"], "5"),
        "transparency": "transparent",  # a task shouldn't mark the person as "busy"
    }


def _delete_event(svc, cal_id, event_id):
    try:
        svc.events().delete(calendarId=cal_id, eventId=event_id).execute()
    except HttpError as e:
        if e.resp.status not in (404, 410):  # already gone is fine
            raise


def sync_ticket(tid, svc=None):
    """Make the calendar match one ticket. Returns created / updated / deleted / skipped... / missing."""
    svc = svc or _svc()
    t = db.get_ticket(tid)
    if not t:
        return "missing"

    wanted = t["status"] != "Closed" and t["due_date"] is not None
    target = ensure_calendar(svc, db.get_user_by_name(t["assignee"])) if wanted else None
    old_cal, old_ev = t["gcal_calendar_id"], t["gcal_event_id"]

    # Remove the old event if the ticket no longer needs one, or its owner's calendar changed
    if old_ev and (not wanted or target != old_cal):
        _delete_event(svc, old_cal, old_ev)
        db.set_gcal(tid, None, None)
        old_cal = old_ev = None
        if not wanted:
            return "deleted"

    if not wanted:
        return "skipped"
    if not target:
        return "skipped (no calendar: add this person's email in the Admin tab)"

    body = _event_body(t)
    if old_ev:
        try:
            svc.events().update(calendarId=target, eventId=old_ev, body=body).execute()
            return "updated"
        except HttpError as e:
            if e.resp.status not in (404, 410):  # event was deleted by hand: recreate below
                raise
    event = svc.events().insert(calendarId=target, body=body).execute()
    db.set_gcal(tid, target, event["id"])
    return "created"


def safe_sync(tid):
    """For the app: never raises, so a Google hiccup can't stop a ticket from saving."""
    if not is_configured():
        return None
    try:
        return sync_ticket(tid)
    except Exception as e:
        return f"error: {e}"


def safe_remove(tid):
    """Delete a ticket's calendar event (used before deleting the ticket itself)."""
    if not is_configured():
        return
    try:
        t = db.get_ticket(tid)
        if t and t["gcal_event_id"]:
            _delete_event(_svc(), t["gcal_calendar_id"], t["gcal_event_id"])
    except Exception:
        pass


def sync_all():
    """Reconcile everything (admin button + the scheduled job). Returns (counts, errors)."""
    svc = _svc()
    counts, errors = {}, []
    for tid in db.tickets_needing_sync():
        try:
            result = sync_ticket(tid, svc).split(" ")[0]
        except Exception as e:
            result = "error"
            errors.append(f"#{tid}: {e}")
        counts[result] = counts.get(result, 0) + 1
    return counts, errors
