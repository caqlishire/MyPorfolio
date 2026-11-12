"""Turn raw punches into worked hours.

Rules (simple and predictable, documented in the README):
- Punches are grouped per employee per calendar day.
- Repeat scans within DOUBLE_SCAN_SECONDS of the previous punch are ignored.
- Punches are paired in order: 1st = in, 2nd = out, 3rd = in (back from break), 4th = out, ...
- An odd number of punches means a missing punch: the last unpaired punch adds no time
  and the day is flagged so the admin can fix it.
- Shifts that cross midnight are not paired across days.
"""
from collections import defaultdict
from datetime import datetime

DOUBLE_SCAN_SECONDS = 120
FMT = "%Y-%m-%d %H:%M:%S"


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, FMT)


def dedupe(times: list[datetime]) -> list[datetime]:
    out: list[datetime] = []
    for t in sorted(times):
        if out and (t - out[-1]).total_seconds() < DOUBLE_SCAN_SECONDS:
            continue
        out.append(t)
    return out


def day_summary(times: list[datetime]) -> dict:
    times = dedupe(times)
    worked = 0.0
    for i in range(0, len(times) - 1, 2):
        worked += (times[i + 1] - times[i]).total_seconds()
    return {
        "first_in": times[0].strftime("%H:%M") if times else None,
        "last_out": times[-1].strftime("%H:%M") if len(times) > 1 else None,
        "punch_count": len(times),
        "minutes": round(worked / 60),
        "missing_punch": len(times) % 2 == 1,
        "clocked_in": len(times) % 2 == 1,
        "punches": [t.strftime("%H:%M") for t in times],
    }


def build_report(punch_rows: list[dict], names: dict[str, str]) -> dict:
    """punch_rows: [{user_id, ts}] -> per-employee daily breakdown and totals."""
    by_user_day: dict[str, dict[str, list[datetime]]] = defaultdict(lambda: defaultdict(list))
    for r in punch_rows:
        t = _parse(r["ts"])
        by_user_day[r["user_id"]][t.strftime("%Y-%m-%d")].append(t)

    def label(u: str) -> str:
        return names.get(u) or f"Employee {u}"

    employees = []
    for user_id in sorted(by_user_day, key=lambda u: (label(u).lower(), u)):
        days = []
        for day in sorted(by_user_day[user_id]):
            s = day_summary(by_user_day[user_id][day])
            s["date"] = day
            days.append(s)
        employees.append({
            "user_id": user_id,
            "name": label(user_id),
            "days": days,
            "total_minutes": sum(d["minutes"] for d in days),
            "days_worked": len(days),
            "missing_punches": sum(1 for d in days if d["missing_punch"]),
        })
    return {
        "employees": employees,
        "total_minutes": sum(e["total_minutes"] for e in employees),
    }
