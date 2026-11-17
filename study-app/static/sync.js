"use strict";
/* IntoStudy file sync: the "Update files" button, the Today section "New from your teachers" and the Settings card. */

const SY = { st: null, timer: null };

const syncAgo = iso => {
  if (!iso) return "never";
  const m = Math.round((Date.now() - new Date(iso)) / 60000);
  if (m < 1) return "just now"; if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60); if (h < 24) return `${h} h ago`;
  return relDay(iso.slice(0, 10));
};
const needsLogin = st => st && st.installed && (st.state === "login" || !st.signed_in);   // also before the first sign-in

async function syncLoad() {
  try { SY.st = await api("/api/sync"); } catch { SY.st = null; }
  syncPaintButton();
  if (SY.st?.running) syncPoll();
  return SY.st;
}

function syncPaintButton() {
  const b = $("#sync-btn"), st = SY.st;
  if (!b) return;
  b.hidden = !st || !st.installed;
  if (!st) return;
  const label = st.state === "login_window" ? "Signing in…" : st.running ? "Updating…" : needsLogin(st) ? "Connect IntoStudy" : "Update files";
  b.innerHTML = `${st.running ? `<span class="spinner" aria-hidden="true"></span>` : icon(needsLogin(st) ? "external" : "refresh")}<span class="sync-label">${label}</span>`;
  b.setAttribute("aria-label", label);
  b.disabled = !!st.running;
  b.dataset.tip = st.running ? (st.step || "Working…") : needsLogin(st) ? "Sign in once so Study Desk can fetch new files from IntoStudy" : `Last checked ${syncAgo(st.last_ok)}. Checks automatically every ${st.every_hours} h.`;
}

async function syncStart(mode) {
  try {
    await api("/api/sync", { mode });
    toast(mode === "login" ? "A Chrome window opened. Sign in to IntoStudy there; it closes by itself." : "Checking IntoStudy for new files…");
  } catch (e) { toast(e.message); return; }
  await syncLoad();
  syncPoll();
}

function syncPoll() {
  clearTimeout(SY.timer);
  SY.timer = setTimeout(async () => {
    const wasRunning = SY.st?.running;
    await syncLoad();
    if (SY.st?.running) return;                 // syncLoad keeps polling
    if (wasRunning) {
      toast(SY.st.error ? `Update failed: ${SY.st.error}` : SY.st.step || "Done.");
      const safe = ["today", "settings", "files", "subjects", "subject"].includes(current.page);   // never wipe a test or lesson in progress
      if (safe) route(); else if ((SY.st.added || []).length) refresh();
    }
  }, 2000);
}

document.addEventListener("click", e => {
  const b = e.target.closest("#sync-btn, [data-sync]");
  if (!b || b.disabled) return;
  const mode = b.dataset.sync || (needsLogin(SY.st) ? "login" : "sync");
  syncStart(mode);
});

/* Today: files your teachers added in the last two weeks */
function newFilesHTML() {
  const st = SY.st;
  if (!st || !st.installed) return "";
  if (needsLogin(st) && !(st.recent || []).length) {
    return `<div class="notice section">${icon("info")}<div><b>Connect IntoStudy to get new files automatically.</b><br>Sign in once in the window that opens; Study Desk then checks for new files every ${st.every_hours} hours and adds them to your topics.
      <div style="margin-top:10px"><button class="btn sm primary" type="button" data-sync="login">Connect IntoStudy</button></div></div></div>`;
  }
  const recent = st.recent || [];
  const head = `<div class="section-head"><h2 id="new-h">New from your teachers</h2><p>${st.running ? esc(st.step || "Updating…") : `Checked ${syncAgo(st.last_ok)}`}${st.running ? "" : ` · <button class="linklike" type="button" data-sync="sync">Update now</button>`} · <a href="#/files">Files check</a></p></div>`;
  if (!recent.length) return `<section class="section" aria-labelledby="new-h">${head}<div class="rows empty-note"><b>Nothing new in the last two weeks</b>New files appear here as soon as teachers upload them.</div></section>`;
  // one row per topic (files outside any topic are grouped by subject), newest first
  const groups = [];
  for (const x of recent) {
    const key = x.topic || "subject:" + x.course;
    let g = groups.find(g => g.key === key);
    if (!g) groups.push(g = { key, topic: x.topic ? topicById(x.topic) : null, course: x.course, files: [], date: x.date });
    g.files.push(x);
  }
  return `<section class="section" aria-labelledby="new-h">${head}<div class="rows">${groups.slice(0, 8).map(g => {
    const n = g.files.length, upd = g.files.filter(x => x.kind === "updated").length;
    const names = g.files.slice(0, 3).map(x => esc(x.name)).join(", ") + (n > 3 ? ` and ${n - 3} more` : "");
    const one = n === 1 && !g.topic && g.files[0].text ? fileHref(g.files[0].text) : null;
    const href = g.topic ? `#/topic/${g.topic.id}/files` : one;
    const body = `
      <div class="row-title">${subjPill(g.course)}<span>${g.topic ? esc(g.topic.name) : "Not linked to a topic yet"}</span></div>
      <div class="row-meta"><span>${names}</span><span>${syncAgo(g.date)}</span></div>
      <div class="row-side"><span class="tag ${upd === n ? "" : "due"}">${upd === n ? `${n} updated` : `${n - upd} new`}</span></div>`;
    return href ? `<a class="row-link" href="${href}">${body}</a>` : `<div class="row-link">${body}</div>`;
  }).join("")}</div></section>`;
}

/* Settings card */
function syncSettingsHTML() {
  const st = SY.st;
  if (!st) return "";
  if (!st.installed) return `<section class="panel pad" style="display:grid;gap:10px"><h2>IntoStudy files</h2><p class="muted small">Automatic updates need a one-time install. In Terminal, in the study-app folder, run:<br><code>python3 -m venv .venv && .venv/bin/pip install playwright pypdf python-pptx python-docx</code></p></section>`;
  return `<section class="panel pad" style="display:grid;gap:12px" id="sync-settings">
    <h2>IntoStudy files</h2>
    <p class="muted small">Study Desk checks man.intostudy.com every ${st.every_hours} hours while it's open, downloads new and changed files, turns them into text and adds them to your topics. It signs in with its own Chrome window; your password is never stored by Study Desk.</p>
    <dl class="sync-facts">
      <div><dt>Status</dt><dd>${st.running ? esc(st.step || "Updating…") : needsLogin(st) ? "Not signed in" : st.state === "error" ? `Failed: ${esc(st.error || "")}` : esc(st.step || "Not checked yet")}</dd></div>
      <div><dt>Last successful check</dt><dd>${st.last_ok ? `${syncAgo(st.last_ok)} (${esc(st.last_ok.replace("T", " ").slice(0, 16))})` : "never"}</dd></div>
      ${(st.failed || []).length ? `<div><dt>Couldn't download</dt><dd>${st.failed.map(esc).join("<br>")}</dd></div>` : ""}
    </dl>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn primary" type="button" data-sync="sync" ${st.running ? "disabled" : ""}>Update files now</button>
      <a class="btn" href="#/files">Files check</a>
      <button class="btn" type="button" data-sync="login" ${st.running ? "disabled" : ""}>${needsLogin(st) ? "Sign in to IntoStudy" : "Sign in again"}</button>
    </div>
  </section>`;
}

/* Compass: read by the Chrome helper in your own browser (Settings card) */
function compassSettingsHTML() {
  const c = S.compass;
  if (!c || S.hosted) return "";
  const state = c.asked ? "Waiting for the helper to read Compass (up to a minute)…"
    : c.connected ? `Connected${c.via === "extension" ? " through the Chrome helper" : ""}. Last read ${syncAgo(c.last_ok)}${c.classes ? `, ${c.classes} weekly classes in your timetable` : ""}.`
    : c.via === "extension" && c.state === "login" ? "The helper is installed, but Compass isn't signed in in Chrome."
    : "Not connected. Install the Chrome helper (setup guide, step 4).";
  return `<section class="panel pad" style="display:grid;gap:12px" id="compass-settings">
    <h2>Compass</h2>
    <p class="muted small">A small helper in your own Chrome reads your timetable, class news and learning tasks from Compass every ${c.every_hours} hours while Chrome is open, and gives them only to Study Desk. You can always paste a post instead (Programme page).</p>
    <p class="small">${state}</p>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn primary" type="button" data-compass-read ${c.asked ? "disabled" : ""}>Read Compass now</button>
      <a class="btn" href="/setup">Install the helper</a>
      <button class="btn" type="button" data-compass>Paste a post instead</button>
    </div></section>`;
}
document.addEventListener("click", async e => {
  const b = e.target.closest("[data-compass-read]");
  if (!b || b.disabled) return;
  busy(b, true, "Asking");
  try { await api("/api/compass/helper", { action: "read" }); toast("Asked the Chrome helper to read Compass (up to a minute)."); }
  catch (err) { toast(err.message); }
  const poll = async (n = 0) => { await refresh(); if (S.compass?.asked && n < 30) setTimeout(() => poll(n + 1), 3000); else if (current.page === "settings") renderSettings(); };
  setTimeout(poll, 3000);
});

/* Settings: OneDrive (files teachers share with you there) */
function onedriveSettingsHTML() {
  const o = S.onedrive;
  if (!o || S.hosted || !SY.st?.installed) return "";
  const state = o.running ? esc(o.step || "Checking OneDrive…") : o.state === "login" ? "Your Microsoft sign-in for OneDrive has expired."
    : o.roots ? `${o.roots} shared folders${o.linked.length ? ` (${esc(o.linked.join(", "))})` : ""}, ${o.files} files. Last checked ${syncAgo(o.last_ok)}.`
    : "Nothing found yet.";
  return `<section class="panel pad" style="display:grid;gap:12px" id="onedrive-settings">
    <h2>OneDrive</h2>
    <p class="muted small">Folders teachers share with you on OneDrive are checked together with IntoStudy, every ${SY.st.every_hours} hours. Only teachers' files and your own work are downloaded, never classmates' copies.</p>
    <p class="small">${state}</p>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn" type="button" data-onedrive="connect" ${o.running ? "disabled" : ""}>Check OneDrive now</button>
      ${o.state === "login" ? `<button class="btn primary" type="button" data-onedrive="login">Sign in to OneDrive</button>` : ""}
    </div></section>`;
}
document.addEventListener("click", async e => {
  const b = e.target.closest("[data-onedrive]");
  if (!b || b.disabled) return;
  busy(b, true, "Starting");
  try { await api("/api/onedrive", { action: b.dataset.onedrive }); toast(b.dataset.onedrive === "login" ? "A window opened: sign in to Microsoft there." : "Checking OneDrive…"); }
  catch (err) { toast(err.message); }
  setTimeout(async () => { await refresh(); if (current.page === "settings") renderSettings(); }, 3000);
});

/* Settings: update the app (Mac app only) */
function updateSettingsHTML() {
  if (!S.app) return "";
  return `<section class="panel pad" style="display:grid;gap:12px" id="update-settings">
    <h2>Study Desk ${esc(S.version || "")}</h2>
    <p class="muted small">Updates replace the app's code only. Your progress, notes, course files and sign-ins stay where they are.</p>
    <p class="small" id="upd-msg" role="status"></p>
    <div><button class="btn" type="button" id="upd-btn">Check for updates</button></div></section>`;
}
document.addEventListener("click", async e => {
  const b = e.target.closest("#upd-btn");
  if (!b || b.disabled) return;
  const msg = $("#upd-msg");
  busy(b, true, "Checking");
  try {
    const r = await api("/api/update", { check: !b.dataset.ready });
    if (!r.newer) { msg.textContent = `You have the latest version (${r.current}).`; busy(b, false); return; }
    if (!r.started) { msg.textContent = `Version ${r.latest} is available (you have ${r.current}).`; b.dataset.ready = "1"; busy(b, false); b.textContent = `Update to ${r.latest}`; return; }
    msg.textContent = `Updating to ${r.latest}…`;
    const target = r.latest;
    const poll = async () => {                 // show the real progress; reload only when the new version answers
      try {
        const s = await (await fetch("/api/state")).json();
        if (s.version === target) { location.reload(); return; }
        const c = await api("/api/update", { check: true });
        if (c.job?.state === "error") { msg.textContent = `The update failed: ${c.job.error}. Your Study Desk is unchanged.`; busy(b, false); return; }
        if (c.job?.step) msg.textContent = c.job.step;
      } catch { msg.textContent = "Restarting…"; }
      setTimeout(poll, 2000);
    };
    setTimeout(poll, 1500);
  } catch (err) { msg.textContent = err.message; busy(b, false); }
});
