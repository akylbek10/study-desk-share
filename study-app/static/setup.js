"use strict";
/* The first-run setup guide: name, AI engine, IntoStudy, Compass, programme. State comes from /api/setup. */

const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({ error: "The server sent an unreadable reply." }));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove("show"), 4500);
}

const U = { st: null, agy: null, open: null, checkingAgy: false, timer: null };

const done = {
  name: () => !!U.st.name,
  engine: () => !!(U.agy?.signed_in || U.st.engine.has_key),
  intostudy: () => U.st.intostudy.files > 0 && !U.st.intostudy.running,
  compass: () => U.st.compass.connected || U.st.timetable > 0 || store.get("setup:skip-compass", false),
  programme: () => Object.keys(U.st.programme.subjects).length > 0 && U.st.programme.job?.state !== "running",
};
const ORDER = ["name", "engine", "intostudy", "compass", "programme"];
const TITLES = {
  name: ["Your name", "So Study Desk can greet you"],
  engine: ["AI engine", "Antigravity (free, with your Google account), or a Gemini key"],
  intostudy: ["Connect IntoStudy", "Your course files from man.intostudy.com"],
  compass: ["Connect Compass", "Your timetable, class news and homework"],
  programme: ["Build your programme", "Subjects, topics by week, assessments and exams"],
};

async function load() {
  try { U.st = await api("/api/setup"); } catch (e) { $("#steps").innerHTML = `<li class="state bad">Study Desk isn't answering: ${esc(e.message)}</li>`; return; }
  if (!U.agy && !U.checkingAgy) checkAgy();
  draw();
  schedule();
}
async function checkAgy() {
  U.checkingAgy = true;
  try { U.agy = await api("/api/setup/agy", { action: "check" }); } catch (e) { U.agy = { error: e.message }; }
  U.checkingAgy = false;
  draw();
}
function schedule() {
  clearTimeout(U.timer);
  const s = U.st, busy = s.engine.install?.state === "running" || s.engine.signing_in || s.intostudy.running
    || ["login_window", "running"].includes(s.intostudy.state) || s.compass.running || s.programme.job?.state === "running"
    || (ORDER.find(k => !done[k]()) === "compass");          // waiting for the Chrome helper to show up
  U.timer = setTimeout(async () => {
    if (s.engine.signing_in || s.engine.install?.state === "running") await checkAgy();
    load();
  }, busy ? 2500 : 10000);
}

function draw() {
  const s = U.st;
  const first = ORDER.find(k => !done[k]()) || null;
  const open = U.open ?? first;
  $("#steps").innerHTML = ORDER.map((k, i) => {
    const ok = done[k]();
    return `<li class="step ${ok ? "done" : ""} ${k === first ? "current" : ""} ${k === open ? "open" : ""}" data-step="${k}">
      <div class="step-head" data-toggle="${k}"><span class="step-num">${ok ? "✓" : i + 1}</span>
        <div><div class="step-title">${TITLES[k][0]}</div><div class="step-sub">${TITLES[k][1]}</div></div></div>
      <div class="step-body">${BODY[k](s)}</div></li>`;
  }).join("");
  $("#finish").innerHTML = !first
    ? `<button class="btn primary" type="button" data-act="finish">Open Study Desk</button>
       <span class="step-sub" style="align-self:center">Check your programme first: the next page shows it week by week.</span>`
    : s.done ? `<a class="btn" href="/">Back to Study Desk</a>` : "";
}

const stateBox = (text, cls = "") => text ? `<div class="state ${cls}">${esc(text)}</div>` : "";

const BODY = {
  name: s => `<p>Your first name, shown on the Today page.</p>
    <div class="row"><input type="text" id="name" maxlength="40" value="${esc(s.name)}" placeholder="First name" autocomplete="given-name">
    <button class="btn primary" type="button" data-act="name">Save</button></div>`,

  engine: s => {
    const a = U.agy, e = s.engine;
    let main;
    if (U.checkingAgy && !a) main = stateBox("Checking Antigravity…");
    else if (a?.signed_in) main = stateBox(`Antigravity is signed in. Study Desk uses ${a.model} (and ${a.fast_model} for quick jobs) in its own project "Study Desk", so these chats stay apart from yours.`, "ok")
      + `<div class="row"><button class="btn" type="button" data-act="agy-test">Test it</button></div>`;
    else if (!e.installed) main = `<p>Study Desk's tutor, tests and notes run on Antigravity, Google's AI tool. It's free with a Google account. Study Desk downloads Google's own version for this Mac (about 200 MB).</p>
      ${e.install?.state === "running" ? stateBox(e.install.step || "Downloading…") : e.install?.state === "error" ? stateBox(`Install failed: ${e.install.error}`, "bad") : ""}
      <div class="row"><button class="btn primary" type="button" data-act="agy-install" ${e.install?.state === "running" ? "disabled" : ""}>Install Antigravity</button></div>`;
    else if (e.signing_in) main = `<p>A browser tab opened with Google's sign-in. Sign in there with your Google account and allow access. This page notices by itself when you're done.</p>
      ${e.signin_url ? `<p><a href="${esc(e.signin_url)}" target="_blank" rel="noopener">The tab didn't open? Open Google's sign-in page</a></p>` : ""}
      <p>If Google shows you a code to copy, paste it here:</p>
      <div class="row"><input type="text" id="agy-code" placeholder="Code from Google" autocomplete="off"><button class="btn" type="button" data-act="agy-code">Send code</button></div>
      <details><summary>What Antigravity shows</summary><div class="state log">${esc(e.signin_out || "…")}</div></details>
      <div class="row"><button class="btn quiet" type="button" data-act="agy-terminal">Sign in in Terminal instead</button><button class="btn quiet" type="button" data-act="agy-recheck">I've signed in</button></div>`;
    else main = `<p>Antigravity is installed${a?.path ? ` (${esc(a.path)})` : ""}. Sign in once with your Google account.</p>
      <div class="row"><button class="btn primary" type="button" data-act="agy-signin">Sign in with Google</button><button class="btn quiet" type="button" data-act="agy-recheck">Check again</button></div>`;
    return main + `<details ${e.has_key && !a?.signed_in ? "open" : ""}><summary>Or use a Gemini API key instead</summary>
      <p style="margin:10px 0">If Antigravity doesn't work for you, get a free key at <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">aistudio.google.com/apikey</a> (the free key allows fewer requests a day). It's saved only on this Mac.</p>
      <div class="row"><input type="password" id="key" placeholder="${e.has_key ? "A key is saved. Paste a new one to replace it" : "Paste your key"}" autocomplete="off"><button class="btn" type="button" data-act="key">Save key</button></div></details>`;
  },

  intostudy: s => {
    const i = s.intostudy, found = Object.entries(i.found || {}), chosen = Object.keys(i.chosen || {});
    if (i.state === "login_window") return `<p>A Chrome window opened. Sign in to IntoStudy there with your university Microsoft account. The window closes by itself when you're in.</p>${stateBox(i.step)}`;
    if (i.state === "choose" || (found.length && !chosen.length && !i.running)) return `<p>These are the courses on your IntoStudy account. Tick the subjects you study (the likely ones are ticked):</p>
      <div class="courses">${found.map(([id, c]) => `<label><input type="checkbox" value="${esc(id)}" ${c.suggested ? "checked" : ""}> ${esc(c.full || c.name)}</label>`).join("")}</div>
      <div class="row"><button class="btn primary" type="button" data-act="courses">Download my courses</button></div>`;
    if (i.running || i.state === "running") return `<p>Downloading your course files and turning them into text. The first time takes 10–20 minutes; you can carry on with the next step meanwhile.</p>${stateBox(i.step || "Working…")}`;
    if (i.files > 0) return stateBox(`${i.files} course files are ready${chosen.length ? ` from ${chosen.length} courses` : ""}. Study Desk checks IntoStudy for new files every 2 hours.`, "ok")
      + `<div class="row"><button class="btn quiet" type="button" data-act="sync-login">Sign in again</button></div>`;
    return `<p>Study Desk downloads your course files (slides, notes, worksheets, past papers) from man.intostudy.com and keeps them up to date. A Chrome window opens: sign in there yourself with your university Microsoft account.</p>
      ${i.state === "login" && i.step ? stateBox(i.step) : ""}${i.error ? stateBox(i.error, "bad") : ""}
      <div class="row"><button class="btn primary" type="button" data-act="sync-login">Connect IntoStudy</button></div>`;
  },

  compass: s => {
    const c = s.compass, subs = Object.keys(s.programme.subjects).length;
    const fallback = `<p><b>Your timetable from a screenshot.</b> Open Compass, take a screenshot of your weekly timetable (Shift-Cmd-4) and add it here${subs ? "" : " after step 5 (it needs your subjects)"}.</p>
      ${s.timetable ? stateBox(`${s.timetable} weekly classes in your timetable.`, "ok") : ""}
      <div class="row"><input type="file" id="tt-img" accept="image/*" ${subs ? "" : "disabled"}>
      <button class="btn" type="button" data-act="tt-img" ${subs ? "" : "disabled"}>Read the timetable</button></div>
      <p><b>This week's topics and homework:</b> paste your teachers' Compass posts on the Programme page (“Paste from Compass”).</p>`;
    if (c.connected) return stateBox(`Compass is connected${c.via === "extension" ? " through the Chrome helper" : ""}${c.classes ? `: ${c.classes} weekly classes in your timetable` : ""}${c.tasks ? `, ${c.tasks} tasks` : ""}${c.news ? `, ${c.news} class posts read` : ""}. It's read again every ${c.every_hours} hours while Chrome is open.`, "ok")
      + (c.classes || subs ? "" : `<p>Your timetable is matched to your subjects after step 5.</p>`)
      + `<div class="row"><button class="btn" type="button" data-act="helper-read" ${c.asked ? "disabled" : ""}>${c.asked ? "Asked the helper…" : "Read Compass now"}</button></div>`;
    const signin = c.via === "extension" && c.state === "login";
    return `<p>Compass only lets real browsers in, so Study Desk reads it through a small helper in <b>your own Chrome</b>, where you're signed in to Compass. It reads your timetable, class news and homework every ${c.every_hours} hours and gives them only to Study Desk on this Mac. Add it once:</p>
      <ol style="margin:0;padding-left:20px;display:grid;gap:8px;color:var(--ink-2)">
        <li><button class="btn sm primary" type="button" data-act="helper-install">Add the helper to Chrome</button> Chrome opens its extensions page and Finder shows a folder called <b>Chrome helper</b>.</li>
        <li>In Chrome, turn on <b>Developer mode</b> (the switch at the top right).</li>
        <li><b>Drag the “Chrome helper” folder</b> from Finder onto the Chrome page. That's all: this page notices by itself.</li>
      </ol>
      ${c.helper_seen && !signin ? stateBox("The helper is installed. Reading Compass…", "ok") : ""}
      ${signin ? stateBox("The helper is installed, but you're not signed in to Compass in Chrome. Sign in there (intomanchester-uk.compass.education); the helper notices within a minute.", "bad")
        : c.state === "error" && c.via === "extension" ? stateBox(c.error || "The helper couldn't read Compass.", "bad") : ""}
      <div class="row"><button class="btn quiet" type="button" data-act="helper-read" ${c.asked ? "disabled" : ""}>${c.asked ? "Asked the helper…" : "Read Compass again"}</button>
      <button class="btn quiet" type="button" data-act="compass-skip">Skip for now</button></div>
      <details><summary>No Google Chrome, or the helper won't install?</summary><div style="display:grid;gap:10px;margin-top:10px">${fallback}</div></details>`;
  },

  programme: s => {
    const p = s.programme, job = p.job, subs = Object.entries(p.subjects);
    if (job?.state === "running") return `<p>Reading your course files and laying out your subjects, topics week by week, assessments, exams and breaks. This takes a few minutes.</p>${stateBox(job.step)}<div class="state log">${esc(p.log || "")}</div>`;
    const ready = done.intostudy() && done.engine();
    return (subs.length ? stateBox(subs.map(([n, k]) => `${n}: ${k} topics`).join("\n"), "ok") : "")
      + (job?.state === "error" ? stateBox(`That didn't work: ${job.error}`, "bad") + `<div class="state log">${esc(p.log || "")}</div>` : "")
      + `<p>${subs.length ? "Build it again if your files changed a lot." : "When the course files are downloaded, Study Desk's AI reads them and builds your programme. You check it on the next page. Each topic is split into lessons later, when you open it."}</p>
      <div class="row"><button class="btn ${subs.length ? "" : "primary"}" type="button" data-act="programme" ${ready ? "" : "disabled"}>${subs.length ? "Build again" : "Build my programme"}</button>
      ${ready ? "" : `<span class="step-sub">Needs steps 2 and 3 first.</span>`}</div>`;
  },
};

document.addEventListener("click", async e => {
  const t = e.target.closest("[data-toggle]");
  if (t) { U.open = U.open === t.dataset.toggle ? "" : t.dataset.toggle; draw(); return; }
  const b = e.target.closest("[data-act]");
  if (!b || b.disabled) return;
  const act = b.dataset.act;
  const run = async (fn, okMsg) => {
    b.disabled = true;
    try { await fn(); if (okMsg) toast(okMsg); } catch (err) { toast(err.message); }
    b.disabled = false;
    await load();
  };
  if (act === "name") return run(() => api("/api/setup/name", { name: $("#name").value }), "Saved");
  if (act === "agy-install") return run(() => api("/api/setup/agy", { action: "install" }), "Downloading Antigravity…");
  if (act === "agy-signin") return run(() => api("/api/setup/agy", { action: "signin" }), "Opening Google sign-in…");
  if (act === "agy-code") return run(() => api("/api/setup/agy", { action: "code", code: $("#agy-code").value }), "Code sent");
  if (act === "agy-terminal") return run(() => api("/api/setup/agy", { action: "terminal" }), "Terminal opened: choose Google OAuth and sign in, then come back");
  if (act === "agy-recheck") { await checkAgy(); return load(); }
  if (act === "agy-test") return run(async () => { const r = await api("/api/setup/agy", { action: "test" }); toast(r.answer); });
  if (act === "key") return run(async () => { const r = await api("/api/settings", { key: $("#key").value.trim() }); toast(`Key saved and working (${r.model}).`); });
  if (act === "sync-login") return run(() => api("/api/sync", { mode: "login" }), "A Chrome window opened: sign in to IntoStudy there");
  if (act === "courses") {
    const ids = [...document.querySelectorAll(".courses input:checked")].map(x => x.value);
    return run(() => api("/api/setup/courses", { ids }), "Downloading your courses…");
  }
  if (act === "helper-install") return run(() => api("/api/compass/helper", { action: "install" }), "Now: Developer mode on, then drag the folder onto the Chrome page");
  if (act === "helper-folder") return run(() => api("/api/compass/helper", { action: "folder" }), "Finder shows the folder; its location is copied");
  if (act === "helper-ext") return run(() => api("/api/compass/helper", { action: "extensions" }), "Chrome's extensions page opened");
  if (act === "helper-read") return run(() => api("/api/compass/helper", { action: "read" }), "Asked the helper to read Compass");
  if (act === "compass") { store.set("setup:skip-compass", false); return run(() => api("/api/compass/connect", { mode: "login" }), "A Chrome window opened: sign in to Compass there"); }
  if (act === "compass-skip") { store.set("setup:skip-compass", true); U.open = null; return draw(); }
  if (act === "tt-img") {
    const f = $("#tt-img").files[0];
    if (!f) return toast("Choose the screenshot first");
    const data = await new Promise((ok, bad) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(",")[1]); r.onerror = bad; r.readAsDataURL(f); });
    return run(async () => { const r = await api("/api/timetable/image", { image: { mime: f.type || "image/png", data } }); toast(`Read ${r.classes} weekly classes.`); });
  }
  if (act === "programme") return run(() => api("/api/setup/programme", {}), "Building your programme…");
  if (act === "finish") { await api("/api/setup/done", {}); location.href = "/#/program"; }
});
document.addEventListener("keydown", e => { if (e.key === "Enter" && e.target.id === "name") $("[data-act=name]").click(); });
load();
