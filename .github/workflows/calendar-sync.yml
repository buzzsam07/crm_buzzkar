"""
Scheduled job: makes Google Calendar match the tickets (the safety net behind the instant sync).
Run by GitHub Actions every hour; can also be run by hand:  python3 sync_calendar.py
"""
import gcal

counts, errors = gcal.sync_all()
print("Sync finished:", counts or "nothing to do")
for e in errors:
    print("ERROR", e)
if errors:
    raise SystemExit(1)  # marks the GitHub run as failed so you get an email
