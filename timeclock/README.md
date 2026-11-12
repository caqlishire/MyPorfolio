# Time Clock Dashboard

See your NGTeco time clock's punches and hours from anywhere, on your phone or computer.

```
[NGTeco clock] --office network--> [Sync program on office PC] --internet--> [Dashboard server] <-- you, from anywhere
 192.168.0.32:4370                   agent/sync_agent.py                       server/ (FastAPI + Postgres)
```

- **Sync program** (`agent/`) runs on a computer at the office. Every 5 minutes it reads the clock and uploads new punches. It only **reads** from the clock and never changes anything on it. Fingerprint and face data stay on the clock.
- **Dashboard** (`server/`) runs in the cloud and is protected by your admin password.

Dashboard tabs:

| Tab | What it shows |
|---|---|
| Today | Who is clocked in right now, everyone's punches today, and hours so far |
| Timesheet | Hours per employee for any date range, a day-by-day breakdown, missing-punch warnings, and **CSV export** for payroll |
| Punches | Every raw punch, filterable by employee and date |
| Employees | Names from the clock, plus an optional dashboard name you can type in (the clock itself is not changed) |

The badge at the top shows when the clock last synced. It's green when the last sync was under 30 minutes ago, amber when it's been longer, and red if the office PC couldn't reach the clock.

### How hours are calculated
- Punches are paired in order each day: in, out, in, out, and so on. A lunch break is simply the gap between the 2nd and 3rd punch.
- If someone scans twice within 2 minutes, the second scan is ignored.
- An odd number of punches on a past day is flagged **⚠ missing**, and the unpaired punch adds no time. On today's date it shows as **In now**.
- Overnight shifts that cross midnight are not supported yet.

---

## Setup

### Part 1: Database (free, about 5 minutes)
1. Create a free account at **neon.tech** and create a project.
2. Copy the **connection string**. It starts with `postgresql://…`.

### Part 2: Dashboard server (free, about 10 minutes)
1. Create a free account at **render.com** and connect your GitHub account.
2. Click **New → Blueprint** and pick this repository. Render reads `render.yaml`.
3. When asked, fill in:
   - `ADMIN_PASSWORD`: the password you'll use to log in. Make it a strong one.
   - `DATABASE_URL`: the Neon connection string from Part 1.
4. Click **Apply** and wait for the deploy to finish. Your dashboard address looks like `https://timeclock-xxxx.onrender.com`.
5. Open the service → **Environment**, and copy the value of **`AGENT_KEY`**. The sync program needs it.

> On Render's free plan the server goes to sleep when unused. The sync program contacts it every 5 minutes, which keeps it awake.

### Part 3: Sync program on an office computer (about 10 minutes, at the office)
The computer must be on the **same network as the clock** and should stay on.

1. Install Python from python.org. On Windows, tick **"Add Python to PATH"**.
2. Copy the `agent` folder to that computer.
3. In the `agent` folder, copy `config.example.ini` to **`config.ini`** and fill it in:
   - `ip`, `port`, `comm_key`: from the clock's **Menu → COMM. → App Connection** (currently `192.168.0.32`, `4370`, `0`)
   - `url`: your dashboard address from Part 2
   - `agent_key`: the `AGENT_KEY` from Part 2
4. Optional check: open a terminal in the folder and run `python sync_agent.py --dry-run`. This reads the clock and shows what would be uploaded, without uploading anything.
5. Double-click **`start_sync_windows.bat`** (Windows) or **`start_sync_mac.command`** (Mac). Keep the window open.
6. To start it automatically on Windows: press `Win+R`, type `shell:startup`, and put a **shortcut** to `start_sync_windows.bat` in the folder that opens.

Then open your dashboard address on your phone and sign in.

### Keep it reliable
- **Reserve the clock's IP address** in your router (DHCP reservation for the device at 192.168.0.32), so the clock's address doesn't change.
- Set the office PC to never sleep.
- The first sync uploads the clock's whole history, so it may take a minute.

---

## Try it locally with sample data (for developers)
```bash
cd timeclock/server
pip install -r requirements.txt
python seed_demo.py                                   # creates timeclock.db with made-up punches
ADMIN_PASSWORD=demo AGENT_KEY=test COOKIE_SECURE=0 uvicorn main:app --port 8000
# open http://localhost:8000, password: demo
```
Run the tests with `pip install pytest httpx pyzk requests && pytest timeclock/tests`.
