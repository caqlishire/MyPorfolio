"""Fill a LOCAL database with made-up sample punches so you can try the dashboard
without a clock. Never run this against your real (production) database.

  python seed_demo.py            # writes to ./timeclock.db
"""
import os
import random
from datetime import date, datetime, timedelta

import db

if os.environ.get("DATABASE_URL", "").startswith("postgres"):
    raise SystemExit("Refusing to put demo data into a Postgres database.")

random.seed(7)
engine = db.make_engine()
people = [("1", "Alex"), ("2", "Sam"), ("3", "Jordan"), ("4", "Taylor"), ("5", "Riley"), ("6", "")]
today = date.today()
punches = []
for back in range(20, -1, -1):
    day = today - timedelta(days=back)
    if day.weekday() == 6:
        continue
    for uid, _ in people:
        if random.random() < 0.12:
            continue
        start = datetime.combine(day, datetime.min.time()) + timedelta(hours=8, minutes=random.randint(-20, 25))
        lunch_out = start + timedelta(hours=4, minutes=random.randint(-15, 15))
        lunch_in = lunch_out + timedelta(minutes=random.randint(25, 45))
        end = lunch_in + timedelta(hours=4, minutes=random.randint(-30, 40))
        times = [start, lunch_out, lunch_in, end]
        if back == 0:  # today: only morning punches so some people show as "In"
            now = datetime.now()
            times = [t for t in times if t <= now] or [start]
        elif random.random() < 0.05:
            times = times[:3]  # a forgotten clock-out
        if random.random() < 0.1:
            times.append(times[0] + timedelta(seconds=20))  # accidental double scan
        punches += [{"user_id": uid, "ts": t.strftime("%Y-%m-%d %H:%M:%S")} for t in times]

with engine.begin() as conn:
    db.upsert_device(conn, engine, "DEMO-0001", "Demo clock", "demo", None)
    db.upsert_employees(conn, engine, [{"user_id": u, "name": n} for u, n in people])
    n = db.insert_punches(conn, engine, punches, "DEMO-0001")
print(f"Inserted {n} demo punches into {engine.url}")
