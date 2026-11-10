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
      if (["today", "settings"].includes(current.page) || (SY.st.added || []).length) route();
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
    const one = n === 1 && !g.topic && g.files[0].text ? `/file?p=${encodeURIComponent(g.files[0].text)}` : null;
    const href = g.topic ? `#/topic/${g.topic.id}/files` : one;
    const body = `
      <div class="row-title">${subjPill(g.course)}<span>${g.topic ? esc(g.topic.name) : "Not linked to a topic yet"}</span></div>
      <div class="row-meta"><span>${names}</span><span>${syncAgo(g.date)}</span></div>
      <div class="row-side"><span class="tag ${upd === n ? "" : "due"}">${upd === n ? `${n} updated` : `${n - upd} new`}</span></div>`;
    return href ? `<a class="row-link" href="${href}" ${g.topic ? "" : `target="_blank" rel="noopener"`}>${body}</a>` : `<div class="row-link">${body}</div>`;
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
