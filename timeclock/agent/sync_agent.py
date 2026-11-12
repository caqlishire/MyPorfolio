"""Office sync program.

Runs on a computer at the office (same network as the clock). Every few minutes it
reads employees and punches from the clock and uploads them to your dashboard server.
It only READS from the clock - it never changes or deletes anything on it.

Usage:
  python sync_agent.py            # run forever, syncing every few minutes
  python sync_agent.py --once     # sync one time and exit
  python sync_agent.py --dry-run  # read the clock and print what would be sent, upload nothing
"""
import argparse
import configparser
import json
import logging
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "config.ini"
STATE_FILE = HERE / "sync_state.json"
BATCH = 2000
log = logging.getLogger("sync")


def load_config(path: Path = CONFIG_FILE) -> dict:
    if not path.exists():
        sys.exit(f"Missing {path.name}. Copy config.example.ini to config.ini and fill it in.")
    cp = configparser.ConfigParser()
    cp.read(path)
    return {
        "ip": cp.get("clock", "ip"),
        "port": cp.getint("clock", "port", fallback=4370),
        "comm_key": cp.getint("clock", "comm_key", fallback=0),
        "force_udp": cp.getboolean("clock", "force_udp", fallback=False),
        "url": cp.get("server", "url").rstrip("/"),
        "agent_key": cp.get("server", "agent_key"),
        "interval": cp.getint("sync", "interval_minutes", fallback=5),
    }


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"last_ts": ""}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state))


def read_clock(cfg: dict) -> dict:
    """Connect to the clock and return device info, users and punches (read-only)."""
    from zk import ZK  # imported here so --help works even before pyzk is installed

    conn = ZK(cfg["ip"], port=cfg["port"], timeout=15, password=cfg["comm_key"],
              force_udp=cfg["force_udp"]).connect()
    try:
        def safe(fn):
            try:
                return str(fn() or "")
            except Exception:
                return ""
        device = {"sn": safe(conn.get_serialnumber), "name": safe(conn.get_device_name),
                  "firmware": safe(conn.get_firmware_version)}
        users = conn.get_users() or []
        records = conn.get_attendance() or []
    finally:
        conn.disconnect()
    return {"device": device, "users": users, "records": records}


def to_payload(raw: dict, since_ts: str) -> tuple[dict, list[dict]]:
    """Convert pyzk objects into plain JSON. Only punches newer than since_ts are included."""
    users = [{"user_id": str(u.user_id), "name": (u.name or "").strip()} for u in raw["users"]]
    punches = []
    for r in raw["records"]:
        ts = r.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        if ts >= since_ts:  # ">=" so punches sharing the last second are not lost; server ignores duplicates
            punches.append({"user_id": str(r.user_id), "ts": ts,
                            "status": int(r.status or 0), "punch": int(r.punch or 0)})
    punches.sort(key=lambda p: p["ts"])
    device = dict(raw["device"])
    device["sn"] = device.get("sn") or "unknown"
    return {"device": device, "users": users}, punches


def upload(cfg: dict, base: dict, punches: list[dict]) -> int:
    inserted = 0
    batches = [punches[i:i + BATCH] for i in range(0, len(punches), BATCH)] or [[]]
    for i, batch in enumerate(batches):
        body = dict(base, punches=batch, users=base["users"] if i == 0 else [])
        r = requests.post(f"{cfg['url']}/api/agent/sync", json=body,
                          headers={"X-Agent-Key": cfg["agent_key"]}, timeout=60)
        if r.status_code == 401:
            raise RuntimeError("Server rejected the agent_key in config.ini")
        r.raise_for_status()
        inserted += r.json().get("inserted", 0)
    return inserted


def report_error(cfg: dict, message: str):
    """Tell the dashboard the clock could not be read, so the status pill turns red."""
    device = load_state().get("device")
    if not device:
        return
    try:
        requests.post(f"{cfg['url']}/api/agent/sync",
                      json={"device": dict(device, error=message[:500])},
                      headers={"X-Agent-Key": cfg["agent_key"]}, timeout=30)
    except Exception:
        pass


def sync_once(cfg: dict, dry_run: bool = False) -> int:
    state = load_state()
    raw = read_clock(cfg)
    base, punches = to_payload(raw, state.get("last_ts", ""))
    log.info("Clock %s: %d employees, %d punches total, %d new since last sync",
             base["device"]["sn"], len(base["users"]), len(raw["records"]), len(punches))
    if dry_run:
        print(json.dumps(dict(base, punches=punches[-10:]), indent=2))
        print(f"(showing last {min(10, len(punches))} of {len(punches)} punches - nothing uploaded)")
        return 0
    inserted = upload(cfg, base, punches)
    if punches:
        state["last_ts"] = punches[-1]["ts"]
    state["device"] = base["device"]
    save_state(state)
    log.info("Uploaded: %d new punches stored", inserted)
    return inserted


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(HERE / "sync.log", encoding="utf-8")],
    )
    cfg = load_config()

    if args.once or args.dry_run:
        sync_once(cfg, dry_run=args.dry_run)
        return

    log.info("Sync program started. Syncing every %d minutes. Close this window to stop.", cfg["interval"])
    while True:
        try:
            sync_once(cfg)
        except Exception as e:
            log.error("Sync failed: %s", e)
            report_error(cfg, f"Could not read clock or upload: {e}")
        time.sleep(cfg["interval"] * 60)


if __name__ == "__main__":
    main()
