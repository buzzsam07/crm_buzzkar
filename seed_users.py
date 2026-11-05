"""
Run ONCE to create the tables and the starting accounts, then DELETE this file
(it contains the starting passwords). Everyone is forced to choose a new
password on first login.

    set DATABASE_URL=postgresql://...        (Windows)
    export DATABASE_URL=postgresql://...     (Mac/Linux)
    python seed_users.py
"""
import db

USERS = [
    # username,          display name, role,    starting password
    ("Samuel.Leon",      "Samuel",     "admin",  "Sam2008p!"),
    ("Aakash.Thapliyal", "Aakash",     "member", "Buzz@123"),
    ("Ameya.Shikhare",   "Ameya",      "member", "Buzz@123"),
]

db.init_db()
for username, name, role, pw in USERS:
    if db.get_user(username):
        print(f"- {username}: already exists, skipped")
        continue
    err = db.create_user(username, name, pw, role)
    print(f"- {username}: {'ERROR: ' + err if err else 'created'}")
print("Done. Now delete this file.")
