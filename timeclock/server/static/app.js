"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pad = (n) => String(n).padStart(2, "0");
const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const hm = (mins) => `${Math.floor(mins / 60)}h ${pad(mins % 60)}m`;
const hours = (mins) => (mins / 60).toFixed(2);
const prettyDate = (s) => new Date(s + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 2500);
}

async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: "same-origin", headers: { "Content-Type": "application/json" }, ...opts });
  if (res.status === 401 && path !== "/api/login") { showLogin(); throw new Error("Not logged in"); }
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) { /* ignore */ }
    throw new Error(typeof msg === "string" ? msg : "Request failed");
  }
  return res.json();
}

// ---------- views ----------
function showLogin() { $("app-view").classList.add("hidden"); $("login-view").classList.remove("hidden"); $("password").focus(); }
function showApp() { $("login-view").classList.add("hidden"); $("app-view").classList.remove("hidden"); loadStatus(); loadToday(); loadEmployeesForFilter(); }

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("login-error").textContent = "";
  try {
    await api("/api/login", { method: "POST", body: JSON.stringify({ password: $("password").value }) });
    $("password").value = "";
    showApp();
  } catch (err) { $("login-error").textContent = err.message; }
});

$("logout").addEventListener("click", async () => { await api("/api/logout", { method: "POST" }).catch(() => {}); showLogin(); });

document.querySelectorAll(".tab").forEach((btn) => btn.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === btn));
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("hidden", p.id !== `tab-${btn.dataset.tab}`));
  ({ today: loadToday, timesheet: loadTimesheet, punches: loadPunches, employees: loadEmployees })[btn.dataset.tab]();
}));

// ---------- sync status ----------
async function loadStatus() {
  const pill = $("sync-pill");
  try {
    const { devices } = await api("/api/status");
    if (!devices.length) { pill.className = "pill warn"; pill.textContent = "Clock not connected yet"; return; }
    const d = devices.sort((a, b) => b.last_seen.localeCompare(a.last_seen))[0];
    const mins = Math.round((Date.now() - new Date(d.last_seen).getTime()) / 60000);
    const ago = mins < 1 ? "just now" : mins < 60 ? `${mins} min ago` : mins < 1440 ? `${Math.round(mins / 60)} h ago` : `${Math.round(mins / 1440)} d ago`;
    if (d.last_error) { pill.className = "pill bad"; pill.textContent = `Clock error · ${ago}`; pill.title = d.last_error; }
    else { pill.className = mins <= 30 ? "pill ok" : "pill warn"; pill.textContent = `Synced ${ago}`; pill.title = `${d.name || "Clock"} · ${d.device_sn}`; }
  } catch (_) { pill.className = "pill"; pill.textContent = "Status unavailable"; }
}
setInterval(() => { if (!$("app-view").classList.contains("hidden")) loadStatus(); }, 60000);

// ---------- today ----------
async function loadToday() {
  const today = iso(new Date());
  $("today-title").textContent = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
  const list = $("today-list");
  try {
    const rep = await api(`/api/report?start=${today}&end=${today}`);
    const now = new Date();
    let inNow = 0, total = 0;
    const rows = rep.employees.map((e) => {
      const d = e.days[0];
      let mins = d.minutes;
      if (d.clocked_in) {
        inNow++;
        const [h, m] = d.punches[d.punches.length - 1].split(":").map(Number);
        const since = new Date(now); since.setHours(h, m, 0, 0);
        mins += Math.max(0, Math.round((now - since) / 60000));
      }
      total += mins;
      return { e, d, mins };
    }).sort((a, b) => (b.d.clocked_in - a.d.clocked_in) || a.e.name.localeCompare(b.e.name));
    $("stat-in").textContent = inNow;
    $("stat-present").textContent = rep.employees.length;
    $("stat-hours").textContent = (total / 60).toFixed(1);
    list.innerHTML = rows.length ? rows.map(({ e, d, mins }) => `
      <div class="row">
        <div class="who"><div class="name">${esc(e.name)}</div><div class="sub">Punches: ${esc(d.punches.join(" · "))}</div></div>
        <span class="pill ${d.clocked_in ? "ok" : ""}">${d.clocked_in ? "In" : "Out"}</span>
        <span class="hrs">${hm(mins)}</span>
      </div>`).join("") : `<div class="empty">No punches yet today.</div>`;
  } catch (err) { list.innerHTML = `<div class="empty">${esc(err.message)}</div>`; }
}
$("refresh-today").addEventListener("click", () => { loadStatus(); loadToday(); });

// ---------- timesheet ----------
function presetRange(name) {
  const t = new Date(); t.setHours(0, 0, 0, 0);
  const monday = addDays(t, -((t.getDay() + 6) % 7));
  switch (name) {
    case "this-week": return [monday, t];
    case "last-week": return [addDays(monday, -7), addDays(monday, -1)];
    case "last-14": return [addDays(t, -13), t];
    case "this-month": return [new Date(t.getFullYear(), t.getMonth(), 1), t];
    case "last-month": return [new Date(t.getFullYear(), t.getMonth() - 1, 1), new Date(t.getFullYear(), t.getMonth(), 0)];
  }
}
function setRange(name) {
  const [s, e] = presetRange(name);
  $("ts-start").value = iso(s); $("ts-end").value = iso(e);
  document.querySelectorAll("[data-range]").forEach((b) => b.classList.toggle("active", b.dataset.range === name));
  loadTimesheet();
}
document.querySelectorAll("[data-range]").forEach((b) => b.addEventListener("click", () => setRange(b.dataset.range)));
$("ts-load").addEventListener("click", () => { document.querySelectorAll("[data-range]").forEach((b) => b.classList.remove("active")); loadTimesheet(); });

async function loadTimesheet() {
  if (!$("ts-start").value) { setRange("this-week"); return; }
  const s = $("ts-start").value, e = $("ts-end").value;
  $("ts-csv").href = `/api/report.csv?start=${s}&end=${e}`;
  $("ts-title").textContent = `${prettyDate(s)} – ${prettyDate(e)}`;
  const body = $("ts-body");
  try {
    const rep = await api(`/api/report?start=${s}&end=${e}`);
    const today = iso(new Date());
    $("ts-total").textContent = `Total ${hours(rep.total_minutes)} h`;
    body.innerHTML = rep.employees.length ? rep.employees.map((emp) => {
      const missing = emp.days.filter((d) => d.missing_punch && d.date !== today).length;
      return `
      <details class="emp">
        <summary>
          <div class="who"><div class="name"><b>${esc(emp.name)}</b> <span class="muted">#${esc(emp.user_id)}</span></div>
            <div class="sub muted">${emp.days_worked} day${emp.days_worked === 1 ? "" : "s"}${missing ? ` · <span class="flag">${missing} missing punch${missing === 1 ? "" : "es"}</span>` : ""}</div></div>
          <div class="total">${hours(emp.total_minutes)} h</div>
        </summary>
        <div class="table-wrap"><table class="table">
          <thead><tr><th>Day</th><th>In</th><th>Out</th><th class="num">Hours</th><th>All punches</th></tr></thead>
          <tbody>${emp.days.map((d) => {
            const open = d.missing_punch && d.date === today;
            return `<tr>
              <td>${prettyDate(d.date)}</td><td>${d.first_in ?? ""}</td><td>${d.last_out ?? ""}</td>
              <td class="num">${hours(d.minutes)}${d.missing_punch ? (open ? ` <span class="pill ok">In now</span>` : ` <span class="flag" title="Missing punch">⚠</span>`) : ""}</td>
              <td>${esc(d.punches.join(" · "))}</td></tr>`;
          }).join("")}</tbody>
        </table></div>
      </details>`;
    }).join("") : `<div class="empty">No punches in this date range.</div>`;
  } catch (err) { body.innerHTML = `<div class="empty">${esc(err.message)}</div>`; }
}

// ---------- punches ----------
async function loadEmployeesForFilter() {
  try {
    const emps = await api("/api/employees");
    $("p-user").innerHTML = `<option value="">Everyone</option>` +
      emps.map((e) => `<option value="${esc(e.user_id)}">${esc(e.display_name || e.device_name || "Employee " + e.user_id)}</option>`).join("");
  } catch (_) { /* ignore */ }
}
$("p-load").addEventListener("click", () => loadPunches());
async function loadPunches() {
  if (!$("p-start").value) { $("p-start").value = iso(addDays(new Date(), -6)); $("p-end").value = iso(new Date()); }
  const q = new URLSearchParams({ start: $("p-start").value, end: $("p-end").value });
  if ($("p-user").value) q.set("user_id", $("p-user").value);
  const body = $("p-body");
  try {
    const rows = await api(`/api/punches?${q}`);
    body.innerHTML = rows.length ? rows.map((r) => `<tr><td>${prettyDate(r.ts.slice(0, 10))}</td><td>${r.ts.slice(11, 16)}</td><td>${esc(r.name)}</td><td class="muted">${esc(r.user_id)}</td></tr>`).join("")
      : `<tr><td colspan="4" class="empty">No punches found.</td></tr>`;
  } catch (err) { body.innerHTML = `<tr><td colspan="4" class="empty">${esc(err.message)}</td></tr>`; }
}

// ---------- employees ----------
async function loadEmployees() {
  const body = $("e-body");
  try {
    const emps = await api("/api/employees");
    body.innerHTML = emps.length ? emps.map((e) => `<tr>
      <td>${esc(e.user_id)}</td><td>${esc(e.device_name) || '<span class="muted">(no name)</span>'}</td>
      <td><input data-id="${esc(e.user_id)}" value="${esc(e.display_name || "")}" placeholder="${esc(e.device_name || "Add a name")}"></td>
      <td><button class="btn small" data-save="${esc(e.user_id)}">Save</button></td></tr>`).join("")
      : `<tr><td colspan="4" class="empty">No employees yet. They appear after the first sync.</td></tr>`;
    body.querySelectorAll("[data-save]").forEach((btn) => btn.addEventListener("click", async () => {
      const id = btn.dataset.save;
      const input = body.querySelector(`input[data-id="${CSS.escape(id)}"]`);
      try {
        await api(`/api/employees/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify({ display_name: input.value }) });
        toast("Saved"); loadEmployeesForFilter();
      } catch (err) { toast(err.message); }
    }));
  } catch (err) { body.innerHTML = `<tr><td colspan="4" class="empty">${esc(err.message)}</td></tr>`; }
}

// ---------- start ----------
api("/api/me").then(showApp).catch(() => showLogin());
