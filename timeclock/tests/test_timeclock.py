import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "agent")]
os.environ.update(ADMIN_PASSWORD="pw", AGENT_KEY="agent-secret", SECRET_KEY="s", COOKIE_SECURE="0",
                  DATABASE_URL="sqlite:///:memory:")

from fastapi.testclient import TestClient  # noqa: E402

import hours  # noqa: E402
import main  # noqa: E402
import sync_agent  # noqa: E402


def T(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


# ---------- hours ----------
def test_day_pairs_in_out_with_lunch():
    s = hours.day_summary([T("2026-01-05 08:00"), T("2026-01-05 12:00"), T("2026-01-05 12:30"), T("2026-01-05 17:00")])
    assert s["minutes"] == 8 * 60 + 30
    assert not s["missing_punch"]
    assert (s["first_in"], s["last_out"]) == ("08:00", "17:00")


def test_double_scan_ignored():
    s = hours.day_summary([T("2026-01-05 08:00"), datetime(2026, 1, 5, 8, 0, 40), T("2026-01-05 16:00")])
    assert s["punch_count"] == 2 and s["minutes"] == 480


def test_odd_punches_flagged_and_unpaired_ignored():
    s = hours.day_summary([T("2026-01-05 08:00"), T("2026-01-05 12:00"), T("2026-01-05 12:30")])
    assert s["missing_punch"] and s["minutes"] == 240


def test_report_groups_by_employee_and_day():
    rows = [{"user_id": "1", "ts": "2026-01-05 08:00:00"}, {"user_id": "1", "ts": "2026-01-05 16:00:00"},
            {"user_id": "1", "ts": "2026-01-06 08:00:00"}, {"user_id": "1", "ts": "2026-01-06 12:00:00"},
            {"user_id": "2", "ts": "2026-01-05 09:00:00"}]
    rep = hours.build_report(rows, {"1": "Alex"})
    alex, two = rep["employees"]
    assert alex["name"] == "Alex" and alex["total_minutes"] == 720 and alex["days_worked"] == 2
    assert two["name"] == "Employee 2" and two["missing_punches"] == 1


# ---------- API ----------
@pytest.fixture()
def client():
    return TestClient(main.create_app("sqlite://"))


SYNC = {"device": {"sn": "SN1", "name": "Clock", "firmware": "fw"},
        "users": [{"user_id": "1", "name": "Alex"}],
        "punches": [{"user_id": "1", "ts": "2026-01-05 08:00:00"}, {"user_id": "1", "ts": "2026-01-05 16:00:00"}]}


def login(c):
    assert c.post("/api/login", json={"password": "pw"}).status_code == 200


def test_dashboard_requires_login(client):
    assert client.get("/api/report").status_code == 401
    assert client.post("/api/login", json={"password": "nope"}).status_code == 401


def test_agent_requires_key(client):
    assert client.post("/api/agent/sync", json=SYNC).status_code == 401
    assert client.post("/api/agent/sync", json=SYNC, headers={"X-Agent-Key": "wrong"}).status_code == 401


def test_sync_is_idempotent_and_reported(client):
    h = {"X-Agent-Key": "agent-secret"}
    assert client.post("/api/agent/sync", json=SYNC, headers=h).json()["inserted"] == 2
    assert client.post("/api/agent/sync", json=SYNC, headers=h).json()["inserted"] == 0
    login(client)
    rep = client.get("/api/report?start=2026-01-05&end=2026-01-05").json()
    assert rep["total_minutes"] == 480 and rep["employees"][0]["name"] == "Alex"
    csv_text = client.get("/api/report.csv?start=2026-01-05&end=2026-01-05").text
    assert "Alex" in csv_text and "8.00" in csv_text
    assert client.get("/api/status").json()["devices"][0]["device_sn"] == "SN1"


def test_rename_survives_next_sync(client):
    h = {"X-Agent-Key": "agent-secret"}
    client.post("/api/agent/sync", json=SYNC, headers=h)
    login(client)
    assert client.put("/api/employees/1", json={"display_name": "Alex Smith"}).status_code == 200
    client.post("/api/agent/sync", json=SYNC, headers=h)
    emp = client.get("/api/employees").json()[0]
    assert emp["display_name"] == "Alex Smith" and emp["device_name"] == "Alex"


def test_bad_dates_rejected(client):
    login(client)
    assert client.get("/api/report?start=2026-02-01&end=2026-01-01").status_code == 400
    assert client.get("/api/report?start=nope").status_code == 400


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Time Clock" in r.text


# ---------- agent ----------
def test_agent_payload_filters_already_synced():
    raw = {"device": {"sn": "SN1", "name": "C", "firmware": "f"},
           "users": [SimpleNamespace(user_id=1, name="Alex ")],
           "records": [SimpleNamespace(user_id=1, timestamp=T("2026-01-05 08:00"), status=0, punch=0),
                       SimpleNamespace(user_id=1, timestamp=T("2026-01-05 16:00"), status=0, punch=1)]}
    base, punches = sync_agent.to_payload(raw, "2026-01-05 12:00:00")
    assert base["users"] == [{"user_id": "1", "name": "Alex"}]
    assert punches == [{"user_id": "1", "ts": "2026-01-05 16:00:00", "status": 0, "punch": 1}]
    _, all_p = sync_agent.to_payload(raw, "")
    assert len(all_p) == 2
