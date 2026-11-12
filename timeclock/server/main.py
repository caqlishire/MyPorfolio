"""Time clock dashboard server.

Environment variables:
  ADMIN_PASSWORD  password for the dashboard login (required)
  AGENT_KEY       shared secret the office sync program sends (required)
  SECRET_KEY      random string used to sign login cookies (required in production)
  DATABASE_URL    Postgres URL in production; defaults to a local SQLite file
"""
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import time
from datetime import date, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select

import db
from hours import build_report

STATIC = Path(__file__).parent / "static"
COOKIE = "tc_session"
SESSION_SECONDS = 14 * 24 * 3600


def create_app(database_url: str | None = None) -> FastAPI:
    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    agent_key = os.environ.get("AGENT_KEY", "")
    secret = (os.environ.get("SECRET_KEY") or secrets.token_hex(32)).encode()
    secure_cookie = os.environ.get("COOKIE_SECURE", "1") != "0"
    engine = db.make_engine(database_url)

    app = FastAPI(title="Time Clock", docs_url=None, redoc_url=None, openapi_url=None)

    # ---------- auth helpers ----------
    def sign(payload: dict) -> str:
        body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        sig = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{sig}"

    def verify(token: str | None) -> bool:
        if not token or "." not in token:
            return False
        body, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()):
            return False
        try:
            return json.loads(base64.urlsafe_b64decode(body))["exp"] > time.time()
        except Exception:
            return False

    def require_admin(request: Request):
        if not verify(request.cookies.get(COOKIE)):
            raise HTTPException(401, "Not logged in")

    def require_agent(x_agent_key: str = Header(default="")):
        if not agent_key or not hmac.compare_digest(x_agent_key, agent_key):
            raise HTTPException(401, "Bad agent key")

    # ---------- models ----------
    class Login(BaseModel):
        password: str

    class DeviceIn(BaseModel):
        sn: str = Field(min_length=1, max_length=64)
        name: str = ""
        firmware: str = ""
        error: str | None = None

    class UserIn(BaseModel):
        user_id: str = Field(min_length=1, max_length=32)
        name: str = ""

    class PunchIn(BaseModel):
        user_id: str = Field(min_length=1, max_length=32)
        ts: str = Field(pattern=r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        status: int = 0
        punch: int = 0

    class SyncIn(BaseModel):
        device: DeviceIn
        users: list[UserIn] = []
        punches: list[PunchIn] = Field(default=[], max_length=5000)

    class Rename(BaseModel):
        display_name: str = Field(max_length=128)

    # ---------- helpers ----------
    def names_map(conn) -> dict[str, str]:
        rows = conn.execute(select(db.employees)).mappings().all()
        return {r["user_id"]: (r["display_name"] or r["device_name"] or "") for r in rows}

    def parse_range(start: str | None, end: str | None) -> tuple[str, str]:
        try:
            s = date.fromisoformat(start) if start else date.today() - timedelta(days=6)
            e = date.fromisoformat(end) if end else date.today()
        except ValueError:
            raise HTTPException(400, "Dates must be YYYY-MM-DD")
        if e < s:
            raise HTTPException(400, "End date is before start date")
        if (e - s).days > 370:
            raise HTTPException(400, "Range too long (max about one year)")
        return f"{s} 00:00:00", f"{e} 23:59:59"

    def load_punches(conn, start: str, end: str, user_id: str | None = None):
        q = select(db.punches.c.user_id, db.punches.c.ts, db.punches.c.status, db.punches.c.punch).where(
            db.punches.c.ts >= start, db.punches.c.ts <= end
        )
        if user_id:
            q = q.where(db.punches.c.user_id == user_id)
        return [dict(r) for r in conn.execute(q.order_by(db.punches.c.ts)).mappings()]

    # ---------- routes: auth ----------
    @app.post("/api/login")
    def login(body: Login, response: Response):
        if not admin_password or not hmac.compare_digest(body.password, admin_password):
            time.sleep(1)  # slow down password guessing
            raise HTTPException(401, "Wrong password")
        response.set_cookie(
            COOKIE, sign({"exp": time.time() + SESSION_SECONDS}), max_age=SESSION_SECONDS,
            httponly=True, samesite="strict", secure=secure_cookie,
        )
        return {"ok": True}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.get("/api/me", dependencies=[Depends(require_admin)])
    def me():
        return {"ok": True}

    # ---------- routes: office sync program ----------
    @app.post("/api/agent/sync", dependencies=[Depends(require_agent)])
    def agent_sync(body: SyncIn):
        with engine.begin() as conn:
            db.upsert_device(conn, engine, body.device.sn, body.device.name, body.device.firmware, body.device.error)
            db.upsert_employees(conn, engine, [u.model_dump() for u in body.users])
            inserted = db.insert_punches(conn, engine, [p.model_dump() for p in body.punches], body.device.sn)
        return {"ok": True, "received": len(body.punches), "inserted": inserted}

    # ---------- routes: dashboard ----------
    @app.get("/api/status", dependencies=[Depends(require_admin)])
    def status():
        with engine.connect() as conn:
            devs = [dict(r) for r in conn.execute(select(db.devices)).mappings()]
        return {"devices": devs}

    @app.get("/api/employees", dependencies=[Depends(require_admin)])
    def list_employees():
        with engine.connect() as conn:
            rows = conn.execute(select(db.employees).order_by(db.employees.c.user_id)).mappings().all()
        return [dict(r) for r in rows]

    @app.put("/api/employees/{user_id}", dependencies=[Depends(require_admin)])
    def rename_employee(user_id: str, body: Rename):
        with engine.begin() as conn:
            res = conn.execute(
                db.employees.update().where(db.employees.c.user_id == user_id)
                .values(display_name=body.display_name.strip() or None)
            )
        if res.rowcount == 0:
            raise HTTPException(404, "Employee not found")
        return {"ok": True}

    @app.get("/api/punches", dependencies=[Depends(require_admin)])
    def list_punches(start: str | None = None, end: str | None = None, user_id: str | None = None):
        s, e = parse_range(start, end)
        with engine.connect() as conn:
            names = names_map(conn)
            rows = load_punches(conn, s, e, user_id)
        for r in rows:
            r["name"] = names.get(r["user_id"]) or f"Employee {r['user_id']}"
        return list(reversed(rows))

    @app.get("/api/report", dependencies=[Depends(require_admin)])
    def report(start: str | None = None, end: str | None = None):
        s, e = parse_range(start, end)
        with engine.connect() as conn:
            names = names_map(conn)
            rows = load_punches(conn, s, e)
        rep = build_report(rows, names)
        rep.update(start=s[:10], end=e[:10])
        return rep

    @app.get("/api/report.csv", dependencies=[Depends(require_admin)])
    def report_csv(start: str | None = None, end: str | None = None):
        rep = report(start, end)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Employee ID", "Name", "Date", "First In", "Last Out", "Punches", "Hours", "Missing Punch"])
        for emp in rep["employees"]:
            for d in emp["days"]:
                w.writerow([emp["user_id"], emp["name"], d["date"], d["first_in"] or "", d["last_out"] or "",
                            " ".join(d["punches"]), f"{d['minutes'] / 60:.2f}", "YES" if d["missing_punch"] else ""])
            w.writerow([emp["user_id"], emp["name"], "TOTAL", "", "", "", f"{emp['total_minutes'] / 60:.2f}", ""])
        fname = f"timesheet_{rep['start']}_to_{rep['end']}.csv"
        return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{fname}"'})

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
