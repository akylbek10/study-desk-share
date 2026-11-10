"use strict";
/* Study Desk front end: plain JS, no build step. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const view = $("#view");
const SUBJECT_VAR = { "Technical Maths": "--maths", Physics: "--physics", Chemistry: "--chemistry", EAP: "--eap" };
const SHORT = { "Technical Maths": "Maths", Physics: "Physics", Chemistry: "Chemistry", EAP: "EAP" };
const SUBJECT_SLOTS = ["--maths", "--physics", "--chemistry", "--eap"];
const CONF_WORDS = ["Not rated", "Lost", "Shaky", "Getting there", "Solid", "Exam-ready"];
let S = null;          // /api/state
let current = {};      // what the router is showing

/* ---------- icons (inline SVG, 24px grid, 1.8 stroke) ---------- */
const ICON_PATHS = {
  today: '<rect x="3" y="4.5" width="18" height="16" rx="2.5"/><path d="M3 9.5h18M8 2.5v4M16 2.5v4"/><path d="M8 14h3v3H8z"/>',
  book: '<path d="M2.5 5h6a3.5 3.5 0 0 1 3.5 3.5V20a2.5 2.5 0 0 0-2.5-2.5h-7z"/><path d="M21.5 5h-6A3.5 3.5 0 0 0 12 8.5V20a2.5 2.5 0 0 1 2.5-2.5h7z"/>',
  cards: '<rect x="6.5" y="3" width="14" height="15" rx="2.5"/><path d="M3.5 7.5v10A3.5 3.5 0 0 0 7 21h9"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4.5 21a7.5 7.5 0 0 1 15 0"/>',
  sliders: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  sparkles: '<path d="M11 3.5 12.9 8.6 18 10.5l-5.1 1.9L11 17.5l-1.9-5.1L4 10.5l5.1-1.9z"/><path d="M18.5 15.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z"/>',
  send: '<path d="M21 3 10.5 13.5M21 3l-6.5 18-4-7.5L3 9.5z"/>',
  camera: '<path d="M3 8.5A2.5 2.5 0 0 1 5.5 6h2l1.6-2.2h5.8L16.5 6h2A2.5 2.5 0 0 1 21 8.5v9a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/><circle cx="12" cy="13" r="3.5"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  refresh: '<path d="M20 12a8 8 0 1 1-2.6-5.9M20 4v4.5h-4.5"/>',
  chevronLeft: '<path d="m15 18-6-6 6-6"/>',
  chevronRight: '<path d="m9 18 6-6-6-6"/>',
  file: '<path d="M14 2.5H6.5A2 2 0 0 0 4.5 4.5v15a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V8z"/><path d="M14 2.5V8h5.5M8.5 13h7M8.5 17h5"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
  flame: '<path d="M12 21.5c-3.9 0-7-2.8-7-6.6 0-2.6 1.4-4.4 2.9-5.9.4 1.8 1.4 2.7 2.4 3.1-.5-3.6 1.1-6.6 4.2-8.6-.2 2.9.9 4.7 2.4 6.3 1.3 1.4 2.1 3 2.1 5.1 0 3.8-3.1 6.6-7 6.6z"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.2"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
  pen: '<path d="M4 20h4L19.5 8.5a2.1 2.1 0 0 0-3-3L5 17z"/><path d="M14.5 7.5l2 2"/>',
  chat: '<path d="M20.5 14.5a2 2 0 0 1-2 2H8l-4.5 4V5.5a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2z"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.5v.5"/>',
  trash: '<path d="M4 7h16M9.5 7V4.5h5V7M6 7l1 13h10l1-13"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  copy: '<rect x="8.5" y="8.5" width="12" height="12" rx="2.5"/><path d="M15.5 8.5v-2a2.5 2.5 0 0 0-2.5-2.5H6A2.5 2.5 0 0 0 3.5 6.5V13A2.5 2.5 0 0 0 6 15.5h2.5"/>',
};
const icon = (name, cls = "i") => `<span class="${cls}" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICON_PATHS[name] || ""}</svg></span>`;
function paintIcons(root = document) { $$("[data-i]", root).forEach(el => { el.outerHTML = icon(el.dataset.i); }); }

/* ---------- helpers ---------- */
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const subjColor = s => `var(${SUBJECT_VAR[s] || "--pen"})`;
const parseDay = iso => new Date(iso + "T12:00:00");
const dayDiff = (a, b) => Math.round((parseDay(b) - parseDay(a)) / 86400000);
const fmtDay = (iso, opts = { day: "numeric", month: "short" }) => parseDay(iso).toLocaleDateString("en-GB", opts);
function relDay(iso) {
  const n = dayDiff(S.today, iso);
  if (n === 0) return "today"; if (n === 1) return "tomorrow"; if (n === -1) return "yesterday";
  return n > 0 ? `in ${n} days` : `${-n} days ago`;
}
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 3800); }
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({ error: "The server sent an unreadable reply." }));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode: fine */ } },
};
function busy(btn, on, label) {
  if (!btn) return;
  if (on) { btn.dataset.html = btn.innerHTML; btn.disabled = true; btn.innerHTML = `<span class="spinner" aria-hidden="true"></span>${label ? `<span>${label}</span>` : ""}`; }
  else if (btn.dataset.html) { btn.disabled = false; btn.innerHTML = btn.dataset.html; }
}
const allTopics = () => S.subjects.flatMap(s => s.topics.map((t, i) => ({ ...t, subject: s.name, order: i })));
const topicById = id => allTopics().find(t => t.id === id);
const taught = t => t.start <= S.today;
const thisWeek = t => taught(t) && dayDiff(t.start, S.today) < 7;

function meter(v) {
  const lv = Math.round(v || 0);
  return `<span class="meter l${lv}" role="img" aria-label="Confidence ${lv ? lv + " of 5, " + CONF_WORDS[lv] : "not rated"}">${[1, 2, 3, 4, 5].map(i => `<i class="${i <= lv ? "on" : ""}"></i>`).join("")}</span>`;
}
const subjPill = s => `<span class="subj-pill"><i style="background:${subjColor(s)}"></i>${esc(SHORT[s] || s)}</span>`;

// Markdown + LaTeX: take maths out first so Markdown can't mangle it.
function md(text) {
  const maths = [];
  const hold = (tex, display) => { maths.push({ tex, display }); return `@@M${maths.length - 1}@@`; };
  const s = String(text || "")
    .replace(/\\n(?![a-z])/g, "\n")   // Gemini sometimes sends a literal "\n"; LaTeX \neq, \nu stay
    .replace(/\$\$([\s\S]+?)\$\$/g, (_, t) => hold(t, true))
    .replace(/\\\[([\s\S]+?)\\\]/g, (_, t) => hold(t, true))
    .replace(/\\\(([\s\S]+?)\\\)/g, (_, t) => hold(t, false))
    .replace(/(^|[^\\$])\$([^\s$](?:[^$\n]*?[^\s$])?)\$(?!\d)/g, (_, pre, t) => pre + hold(t, false));
  return DOMPurify.sanitize(marked.parse(s, { breaks: true })).replace(/@@M(\d+)@@/g, (_, i) => {
    const m = maths[+i];
    try { return katex.renderToString(m.tex, { displayMode: m.display, throwOnError: false }); } catch { return esc(m.tex); }
  });
}
function readImage(file) {
  return new Promise((res, rej) => {
    if (!file) return res(null);
    if (file.size > 18e6) return rej(new Error("That image is over 18 MB. Take a smaller photo."));
    const r = new FileReader();
    r.onload = () => res({ mime: file.type || "image/jpeg", data: String(r.result).split(",")[1], name: file.name });
    r.onerror = () => rej(new Error("Couldn't read that image."));
    r.readAsDataURL(file);
  });
}

/* ---------- tooltip (for charts, ruler, heatmap) ---------- */
const tip = $("#tooltip");
function showTip(el) {
  const html = el.getAttribute("data-tip"); if (!html) return;
  tip.innerHTML = html; tip.hidden = false;
  const r = el.getBoundingClientRect(), t = tip.getBoundingClientRect();
  let x = r.left + r.width / 2 - t.width / 2, y = r.top - t.height - 8;
  if (y < 8) y = r.bottom + 8;
  tip.style.left = Math.max(8, Math.min(x, innerWidth - t.width - 8)) + "px";
  tip.style.top = y + "px";
}
document.addEventListener("mouseover", e => { const el = e.target.closest("[data-tip]"); if (el) showTip(el); });
document.addEventListener("mouseout", e => { if (e.target.closest("[data-tip]")) tip.hidden = true; });
document.addEventListener("focusin", e => { const el = e.target.closest("[data-tip]"); if (el) showTip(el); else tip.hidden = true; });
document.addEventListener("scroll", () => { tip.hidden = true; }, true);

/* ---------- navigation chrome ---------- */
function navHTML() {
  const due = S.due_total ? `<span class="count" aria-label="${S.due_total} due">${S.due_total}</span>` : "";
  $("#nav-main").innerHTML = `
    <a class="nav-link" href="#/today" data-nav="today">${icon("today")}Today</a>
    <a class="nav-link" href="#/progress" data-nav="progress">${icon("target")}Progress</a>
    <a class="nav-link" href="#/program" data-nav="program">${icon("layers")}Programme</a>
    <a class="nav-link" href="#/timetable" data-nav="timetable">${icon("clock")}Timetable</a>
    <a class="nav-link" href="#/review" data-nav="review">${icon("cards")}Flashcards${due}</a>
    <a class="nav-link" href="#/files" data-nav="files">${icon("file")}Files check</a>
    <a class="nav-link" href="#/profile" data-nav="profile">${icon("user")}Profile</a>`;
  $("#nav-subjects").innerHTML = S.subjects.map(s =>
    `<a class="nav-link" href="#/subject/${encodeURIComponent(s.name)}" data-nav="subject:${esc(s.name)}"><span class="swatch" style="background:${subjColor(s.name)}"></span>${esc(s.name)}</a>`).join("");
  $("#nav-bottom").innerHTML = `<a class="nav-link" href="#/settings" data-nav="settings">${icon("sliders")}Settings</a>`;
  $("#tabbar").innerHTML = `
    <a href="#/today" data-nav="today">${icon("today")}Today</a>
    <a href="#/progress" data-nav="progress">${icon("target")}Progress</a>
    <a href="#/review" data-nav="review">${icon("cards")}Cards${S.due_total ? `<span class="count">${S.due_total}</span>` : ""}</a>
    <a href="#/profile" data-nav="profile">${icon("user")}Profile</a>
    <button type="button" data-open-assistant>${icon("sparkles")}Assistant</button>`;
}
function setNav(key, title) {
  $$("[data-nav]").forEach(a => {
    const on = a.dataset.nav === key || (key.startsWith("subject:") && a.dataset.nav === "subjects");
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  $("#topbar-title").textContent = title || "";
  document.title = title ? `${title} | Study Desk` : "Study Desk";
}
addEventListener("scroll", () => $(".topbar").classList.toggle("scrolled", scrollY > 30), { passive: true });

const keyNotice = () => S.has_key ? "" :
  `<div class="notice">${icon("info")}<div><b>Connect Gemini to turn on the tutor, practice marking and the assistant.</b><br>Get a free key at <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">aistudio.google.com/apikey</a>, then paste it in <a href="#/settings">Settings</a>.</div></div>`;

/* =====================================================================
   TODAY
   ===================================================================== */
function renderToday() {
  setNav("today", "Today");
  const topics = allTopics();
  const upcomingExams = [...S.exams].filter(e => e.date >= S.today).sort((a, b) => a.date.localeCompare(b.date));
  const firstExam = upcomingExams[0];
  const inClass = topics.filter(t => thisWeek(t) && t.subject !== "EAP");
  const name = S.name ? `, ${esc(S.name)}` : "";
  const h = new Date().getHours();
  const greet = h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";

  let sub = firstExam ? `${plural(dayDiff(S.today, firstExam.date), "day")} until your Semester 1 exams start.` : "No upcoming exams set.";
  if (inClass.length) sub += ` In class this week: ${inClass.map(t => esc(t.name)).join(", ")}.`;

  view.innerHTML = `
    <div class="page-head"><div>
      <h1>${greet}${name}</h1>
      <p class="sub">${fmtDay(S.today, { weekday: "long", day: "numeric", month: "long" })}. ${sub}</p>
    </div></div>
    ${keyNotice()}
    <section class="section" aria-labelledby="stand-h">
      <div class="section-head"><h2 id="stand-h">Where you stand</h2><p><a href="#/progress">All topics</a></p></div>
      ${standStatsHTML()}
    </section>
    ${tasksHTML()}
    <div class="cols section">
      <section aria-labelledby="plan-h">
        <div class="section-head"><h2 id="plan-h">Do next</h2></div>
        ${doNextHTML()}
      </section>
      <section aria-labelledby="ass-h">
        <div class="section-head"><h2 id="ass-h">Assessments and exams</h2><p>How ready you are</p></div>
        ${assessmentsHTML()}
      </section>
    </div>
    ${thisWeekHTML()}
    ${classesTodayHTML()}
    ${newFilesHTML()}
    <section class="section" aria-labelledby="ruler-h">
      <div class="section-head"><h2 id="ruler-h">Your semester</h2><p>Each dot is a topic, placed on the week it starts in class. Hover a dot for details.</p></div>
      ${rulerHTML(topics, upcomingExams)}
    </section>`;
  // On narrow screens the ruler scrolls: start with today in view and pin the subject labels.
  const sc = $(".ruler-scroll", view), line = $(".today-line", view);
  if (sc && line && sc.scrollWidth > sc.clientWidth) {
    const svg = $("svg", sc), box = svg.getBoundingClientRect(), ratio = box.width / 1000;
    sc.scrollLeft = Math.max(0, line.x1.baseVal.value * ratio - sc.clientWidth * 0.6);
    $(".ruler", view).insertAdjacentHTML("beforeend", `<div class="lane-overlay" aria-hidden="true" style="height:${box.height}px">${
      RULER.lanes.map(l => `<span style="top:${l.y / RULER.H * 100}%"><i style="background:${subjColor(l.s)}"></i>${esc(SHORT[l.s])}</span>`).join("")}</div>`);
  }
}
const RULER = { H: 1, lanes: [] };

function rulerHTML(topics, exams) {
  const start = "2026-09-14";
  const lastExam = exams.length ? exams[exams.length - 1].date : "2027-01-12";
  const end = parseDay(lastExam) > parseDay(S.today) ? addDays(lastExam, 9) : addDays(S.today, 21);
  const W = 1000, L = 128, R = 18, top = 46, laneH = 38;
  const subjects = S.subjects.map(s => s.name);
  const H = top + laneH * subjects.length + 14;
  const span = dayDiff(start, end);
  const x = iso => L + (W - L - R) * Math.max(0, Math.min(1, dayDiff(start, iso) / span));
  let svg = "";

  // holidays
  for (const b of S.breaks || []) {
    const x1 = x(b.start), x2 = x(addDays(b.end, 1));
    svg += `<rect class="holiday" x="${x1}" y="${top - 8}" width="${x2 - x1}" height="${H - top}" /><text class="holiday-label" x="${(x1 + x2) / 2}" y="${H - 6}" text-anchor="middle">${esc(b.name)}</text>`;
  }
  // months + week ticks
  for (let d = start; parseDay(d) <= parseDay(end); d = addDays(d, 1)) {
    const dt = parseDay(d);
    if (dt.getDate() === 1 || d === start) svg += `<text class="month" x="${x(d) + 3}" y="18">${dt.toLocaleDateString("en-GB", { month: "short" })}</text>`;
    if (dt.getDay() === 1) svg += `<line class="tick" x1="${x(d)}" x2="${x(d)}" y1="26" y2="${dt.getDate() <= 7 ? 36 : 32}" />`;
  }
  // lanes
  RULER.H = H; RULER.lanes = [];
  subjects.forEach((s, i) => {
    const y = top + laneH * i + laneH / 2;
    RULER.lanes.push({ s, y });
    svg += `<rect x="12" y="${y - 5}" width="10" height="10" rx="3" fill="${subjColor(s)}" />
      <text class="lane-label" x="30" y="${y + 4.5}">${esc(SHORT[s])}</text>
      <line class="lane" x1="${L}" x2="${W - R}" y1="${y}" y2="${y}" />`;
    if (s === "EAP") {
      svg += `<rect x="${x("2026-09-15")}" y="${y - 3}" width="${x(addDays(S.breaks?.[0]?.start || "2026-12-14", -1)) - x("2026-09-15")}" height="6" rx="3" fill="${subjColor(s)}" opacity=".45"
        data-tip="<b>EAP</b><br>Reading, writing, listening and speaking skills run all semester" tabindex="0" />`;
      return;
    }
    for (const t of topics.filter(t => t.subject === s)) {
      const past = dayDiff(t.start, S.today) >= 7;
      const tipText = `<b>${esc(t.name)}</b><br>${esc(SHORT[s])}, class from ${fmtDay(t.start)}${t.confidence ? `<br>Confidence ${Math.round(t.confidence)}/5` : ""}`;
      svg += `<a href="#/topic/${t.id}/check" aria-label="${esc(t.name)}, ${esc(s)}, class from ${fmtDay(t.start)}">
        <circle class="topic-dot${past ? " past" : ""}" cx="${x(t.start)}" cy="${y}" r="6" fill="${subjColor(s)}" data-tip="${esc(tipText)}" /></a>`;
    }
  });
  // exams (grouped by date)
  const byDate = {};
  exams.forEach(e => (byDate[e.date] ||= []).push(e));
  for (const [d, list] of Object.entries(byDate)) {
    const ex = x(d);
    svg += `<line x1="${ex}" x2="${ex}" y1="${top - 10}" y2="${H - 22}" stroke="var(--ink)" stroke-width="1.5" />
      <path class="exam-flag" d="M${ex} ${top - 10} h12 l-3 4 3 4 h-12z" />
      <text class="exam-label" x="${ex - 4}" y="${top - 16}" text-anchor="end" data-tip="${esc(list.map(e => e.name).join("<br>"))}" tabindex="0">${list.length > 1 ? "Exams" : esc(SHORT[list[0].subject] || "Exam")} ${fmtDay(d)}</text>`;
  }
  // today
  const tx = x(S.today);
  svg += `<line class="today-line" x1="${tx}" x2="${tx}" y1="24" y2="${H - 22}" /><text class="today-label" x="${tx + 5}" y="36">Today</text>`;

  const legend = `<span class="legend-item"><span class="legend-dot" style="background:var(--ink-3)"></span>Dot: topic starts in class</span>
    <span class="legend-item"><span class="legend-dot" style="background:var(--ink-3);opacity:.45"></span>Faded: already taught</span>
    <span class="legend-item"><span style="width:2px;height:12px;background:var(--red)"></span>Today</span>
    <span class="legend-item"><span style="width:10px;height:8px;background:var(--ink);clip-path:polygon(0 0,100% 0,75% 50%,100% 100%,0 100%)"></span>Exam</span>`;
  return `<div class="ruler"><div class="ruler-scroll"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Semester timeline with topics, today and exam dates">${svg}</svg></div><div class="ruler-foot">${legend}</div></div>`;
}
function addDays(iso, n) { const d = parseDay(iso); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); }

/* =====================================================================
   SUBJECTS
   ===================================================================== */
function subjectFacts(s) {
  const tt = s.topics.filter(taught), rated = tt.filter(t => t.confidence);
  const att = s.topics.reduce((n, t) => n + t.attempts, 0);
  const accs = s.topics.filter(t => t.accuracy != null);
  const avg = rated.length ? (rated.reduce((n, t) => n + t.confidence, 0) / rated.length).toFixed(1) : null;
  const next = s.topics.filter(t => !taught(t)).sort((a, b) => a.start.localeCompare(b.start))[0];
  return { tt, rated, att, avg, next, acc: accs.length ? Math.round(accs.reduce((n, t) => n + t.accuracy * t.attempts, 0) / accs.reduce((n, t) => n + t.attempts, 0)) : null,
           cards: s.topics.reduce((n, t) => n + t.cards, 0) };
}
function renderSubjects() {
  setNav("subjects", "Subjects");
  view.innerHTML = `<div class="page-head"><div><h1>Subjects</h1><p class="sub">Semester 1 of the International Foundation Year.</p></div></div>
    <div class="rows">${S.subjects.map(s => {
      const f = subjectFacts(s);
      return `<a class="subject-band" href="#/subject/${encodeURIComponent(s.name)}">
        <span class="bar" style="background:${subjColor(s.name)}"></span>
        <div><h3>${esc(s.name)}</h3><div class="facts">
          <span><b>${f.tt.length}</b> of ${s.topics.length} topics taught</span>
          <span>Confidence <b>${f.avg ?? "not rated"}</b>${f.avg ? " / 5" : ""}</span>
          ${f.acc != null ? `<span>Marks <b>${f.acc}%</b></span>` : ""}
          ${f.next ? `<span>Next: ${esc(f.next.name)}, ${relDay(f.next.start)}</span>` : ""}
        </div></div>
        ${icon("chevronRight")}</a>`;
    }).join("")}</div>`;
}

function renderSubject(name) {
  const s = S.subjects.find(x => x.name === name);
  if (!s) return renderSubjects();
  setNav("subject:" + name, name);
  const f = subjectFacts(s);
  const exam = S.exams.find(e => e.subject === name && e.date >= S.today);
  view.innerHTML = `
    <div class="page-head"><div>
      <a class="crumb" href="#/subjects">${icon("chevronLeft")}Subjects</a>
      <h1><span class="swatch" style="display:inline-block;width:14px;height:14px;border-radius:4px;margin:0 12px 4px 0;background:${subjColor(name)}"></span>${esc(name)}</h1>
      <p class="sub">${f.tt.length} of ${s.topics.length} Semester 1 topics taught.${f.avg ? ` Average confidence ${f.avg}/5 across ${plural(f.rated.length, "rated topic")}.` : " No topics rated yet."}${exam ? ` ${esc(exam.name)} ${relDay(exam.date)}.` : ""}</p>
    </div></div>
    ${keyNotice()}
    <div class="table-wrap"><table>
      <thead><tr><th>Topic</th><th>In class</th><th>Status</th><th>Confidence</th><th class="num">Marks</th><th class="num">Cards</th><th>Last studied</th></tr></thead>
      <tbody>${s.topics.map(t => `
        <tr class="link" data-href="#/topic/${t.id}/check">
          <td><a href="#/topic/${t.id}/check">${esc(t.name)}</a>${t.code ? `<div class="small faint">${esc(t.code)}</div>` : ""}</td>
          <td>${taught(t) ? (thisWeek(t) ? `<span class="tag due">This week</span>` : `<span class="faint">From ${fmtDay(t.start)}</span>`) : `<span class="tag soon">${esc(relDay(t.start))}</span>`}</td>
          <td>${statusTag(t)}</td>
          <td><div style="display:flex;align-items:center;gap:10px">${meter(t.confidence)}<span class="meter-label">${CONF_WORDS[Math.round(t.confidence || 0)]}</span></div></td>
          <td class="num">${t.accuracy != null ? `${t.accuracy}%<div class="small faint">${plural(t.attempts, "answer")}</div>` : `<span class="faint">None yet</span>`}</td>
          <td class="num">${t.cards ? `${t.cards}${t.due ? `<div class="small" style="color:var(--pen)">${t.due} due</div>` : ""}` : `<span class="faint">0</span>`}</td>
          <td>${t.last ? esc(relDay(t.last)) : `<span class="faint">Never</span>`}</td>
        </tr>`).join("")}</tbody></table></div>`;
  $$("tr.link", view).forEach(tr => tr.addEventListener("click", e => { if (!e.target.closest("a")) location.hash = tr.dataset.href; }));
}

/* =====================================================================
   TOPIC
   ===================================================================== */
function renderTopic(id, tab) {
  const t = topicById(id);
  if (!t) return renderToday();
  tab = ["check", "review", "learn", "tutor", "practice", "cards", "files"].includes(tab) ? tab : "check";
  setNav("subject:" + t.subject, t.name);
  const lv = Math.round(t.confidence || 0);
  const when = taught(t) ? (thisWeek(t) ? "In class this week" : `Taught from ${fmtDay(t.start)}`) : `Class starts ${relDay(t.start)}, ${fmtDay(t.start)}`;
  view.innerHTML = `
    <div class="topic-head">
      <div>
        <a class="crumb" href="#/subject/${encodeURIComponent(t.subject)}">${icon("chevronLeft")}${esc(t.subject)}</a>
        <h1>${esc(t.name)}</h1>
        <p class="sub muted" style="margin-top:8px">${t.code ? esc(t.code) + ". " : ""}${when}.${t.files ? "" : " No course files yet, so the AI uses the standard syllabus."}</p>
        <div class="topic-actions">${statusTag(t)}
          <button class="btn gem-btn" type="button" data-gemini="${t.id}">${icon("sparkles")}Study this topic in Gemini</button>
          ${t.status === "confirmed" ? "" : `<button class="btn" type="button" data-mark="${t.id}" data-on="${t.status !== "none" ? 1 : 0}">${icon("check")}${t.status !== "none" ? "Studied (untick)" : "Mark as studied"}</button>`}
        </div>
      </div>
      <div class="rater">
        <span class="rater-label" id="rate-l">How confident are you? <b id="rate-word">${CONF_WORDS[lv]}</b></span>
        <div class="seg" role="group" aria-labelledby="rate-l">${[1, 2, 3, 4, 5].map(i => `<button type="button" data-lv="${i}" aria-pressed="${lv === i}" title="${CONF_WORDS[i]}">${i}</button>`).join("")}</div>
      </div>
    </div>
    ${keyNotice()}
    <nav class="tabs" aria-label="Topic sections">
      ${[["check", "Check", "target", t.check_best != null ? `${t.check_best}%` : ""], ["review", "Review notes", "book"], ["learn", "Learn here", "layers", t.learn?.built ? `${t.learn.passed}/${t.learn.subs}` : ""], ["tutor", "Tutor", "chat"], ["practice", "Practice questions", "pen"], ["cards", "Flashcards", "cards", t.cards], ["files", "Course files", "file", t.files]].map(([k, l, ic, n]) =>
        `<a href="#/topic/${id}/${k}" ${k === tab ? 'aria-current="page"' : ""}>${icon(ic)}${l}${n ? `<span class="n">${n}</span>` : ""}</a>`).join("")}
    </nav>
    <div id="pane"></div>`;
  $(".seg", view).addEventListener("click", async e => {
    const b = e.target.closest("button"); if (!b) return;
    try {
      await api("/api/confidence", { topic: id, level: +b.dataset.lv });
      $$(".seg button", view).forEach(x => x.setAttribute("aria-pressed", x === b));
      $("#rate-word").textContent = CONF_WORDS[+b.dataset.lv];
      await refresh(); toast(`Saved: ${CONF_WORDS[+b.dataset.lv]}`);
    } catch (err) { toast(err.message); }
  });
  ({ check: paneCheck, review: paneReview, learn: paneLearn, tutor: paneTutor, practice: panePractice, cards: paneCards, files: paneFiles })[tab](t);
}

/* ----- shared chat renderer (tutor + assistant) ----- */
const CHECK_PHOTO = "Check my working in the photo: find the first line where I went wrong and explain why.";
const FOLLOW_UPS = {
  problem: ["Give me a hint", "Show the next step", "Show the full solution"],
  explain: ["Explain it more simply", "Show another example", "Give me a problem to try", "Make it harder"],
};
function chatHTML(msgs) {
  const last = msgs[msgs.length - 1];
  return msgs.map(m => m.role === "user"
    ? `<div class="msg user">${esc(m.text)}${m.hadImage ? `<div class="att">${icon("camera")}Photo attached</div>` : ""}</div>`
    : `<div class="msg model md">${md(m.text)}${typeof figureHTML === "function" ? figureHTML(m.figure) : ""}${
        m.checked || m.card ? `<div class="msg-notes">${m.checked ? `<span>${icon("check")}Double-checked</span>` : ""}${m.card ? `<span>${icon("cards")}Your mistake was saved as a flashcard</span>` : ""}</div>` : ""}</div>${
        m === last ? `<div class="chips follow">${(m.problem ? FOLLOW_UPS.problem : FOLLOW_UPS.explain).map(q => `<button class="chip" type="button">${q}</button>`).join("")}<button class="chip" type="button" data-photo>${icon("camera")}Check my solution (photo)</button></div>` : ""}`).join("");
}
const modelMsg = r => ({ role: "model", text: r.answer, figure: r.figure || null, problem: !!r.problem, checked: !!r.checked, card: !!r.card_added });
const thinkingHTML = label => `<div class="msg model thinking"><span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>${label}</div>`;

function paneTutor(t) {
  const key = "chat:" + t.id;
  let msgs = store.get(key, []), image = null;
  const starters = ["Explain this topic from the beginning", "List the key formulas and rules", "Show me a worked exam-style example", "What mistakes lose marks here?", "Quiz me with three quick questions"];
  $("#pane").innerHTML = `
    <div class="chat" id="chat"></div>
    <div class="dock">
      <div class="attach" id="attach" hidden></div>
      <form class="composer" id="tutor-form">
        <label class="icon-btn" title="Attach a photo of your working" aria-label="Attach a photo of your working">${icon("camera")}<input type="file" accept="image/*" id="img" hidden></label>
        <textarea id="input" rows="1" placeholder="Ask about ${esc(t.name)}" aria-label="Message the tutor"></textarea>
        <button class="icon-btn primary" id="send" aria-label="Send">${icon("send")}</button>
      </form>
      <div class="dock-note"><span>Enter sends, Shift+Enter adds a line. Answers come from your course files.</span><button class="btn quiet sm" id="clear" type="button">New chat</button></div>
    </div>`;
  const chat = $("#chat"), input = $("#input"), send = $("#send");
  const draw = () => {
    $(".dock").classList.toggle("static", !msgs.length);
    chat.innerHTML = msgs.length ? chatHTML(msgs) : `
      <div class="chat-empty"><h3>Your tutor for ${esc(t.name)}</h3>
      <p class="muted" style="margin-bottom:16px">It has read your ${t.files ? plural(t.files, "course file") : "syllabus"} for this topic. Ask anything, or send a photo of a question you're stuck on.</p>
      <div class="chips">${starters.map(q => `<button class="chip" type="button">${q}</button>`).join("")}</div></div>`;
  };
  const go = async text => {
    text = text.trim(); if (!text && !image) return;
    msgs.push({ role: "user", text: text || CHECK_PHOTO, image, hadImage: !!image });
    image = null; $("#attach").hidden = true; input.value = ""; input.style.height = "";
    draw(); chat.insertAdjacentHTML("beforeend", thinkingHTML("Thinking it through and checking the answer"));
    chat.lastElementChild.scrollIntoView({ block: "end", behavior: "smooth" });
    send.disabled = true;
    try {
      msgs.push(modelMsg(await api("/api/chat", { topic: t.id, messages: msgs.map(m => ({ role: m.role, text: m.text, image: m.image })) })));
    } catch (e) { const last = msgs.pop(); input.value = last.text; toast(e.message); }
    send.disabled = false; draw();
    store.set(key, msgs.map(({ image, ...m }) => m).slice(-40));
    const lastModel = $$(".msg.model", chat).pop(); lastModel?.scrollIntoView({ block: "start", behavior: "smooth" });
  };
  draw();
  $("#tutor-form").onsubmit = e => { e.preventDefault(); go(input.value); };
  input.onkeydown = e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); go(input.value); } };
  input.oninput = () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 200) + "px"; };
  let sendPhotoNow = false;
  chat.addEventListener("click", e => {
    const c = e.target.closest(".chip"); if (!c || send.disabled) return;
    if ("photo" in c.dataset) { sendPhotoNow = true; $("#img").click(); } else go(c.textContent);
  });
  $("#img").onchange = async e => {
    try { image = await readImage(e.target.files[0]); const a = $("#attach"); a.hidden = !image; a.innerHTML = image ? `${icon("camera")}${esc(image.name)} will be sent with your message` : ""; }
    catch (err) { toast(err.message); }
    e.target.value = "";
    if (sendPhotoNow && image) go(input.value);
    sendPhotoNow = false;
  };
  $("#clear").onclick = () => { msgs = []; store.set(key, []); draw(); };
}

function panePractice(t) {
  const key = "practice:" + t.id;
  let qs = store.get(key, []);
  $("#pane").innerHTML = `
    <div class="toolbar">
      <label class="sr" for="count">Number of questions</label>
      <select id="count">${[1, 2, 3, 4, 5].map(n => `<option value="${n}" ${n === 3 ? "selected" : ""}>${plural(n, "question")}</option>`).join("")}</select>
      <label class="sr" for="diff">Difficulty</label>
      <select id="diff"><option value="easy">Warm-up</option><option value="exam" selected>Exam level</option><option value="challenging">Challenging</option></select>
      <span class="muted small grow">Written in the style of your past papers, marked against a mark scheme.</span>
      <button class="btn primary" id="gen" type="button">Write questions</button>
    </div>
    <div id="qs"></div>`;
  const list = $("#qs");
  const resultHTML = q => {
    const r = q.result, pct = r.score / r.max_marks;
    return `<div class="mark-result ${pct >= .7 ? "good" : pct >= .4 ? "mid" : "bad"}">
      <div class="score">${r.score}<span class="of"> / ${r.max_marks} marks</span></div>
      <div class="md">${md(r.feedback)}</div>
      <details><summary>Model answer</summary><div class="md">${md(r.model_answer)}</div></details></div>`;
  };
  const draw = () => {
    if (!qs.length) {
      list.innerHTML = `<div class="rows empty"><h3>No questions yet</h3><p>Choose how many and how hard, then write a set. Answer by typing or with a photo of your written working.</p></div>`;
      return;
    }
    list.innerHTML = qs.map((q, i) => `
      <article class="question" data-i="${i}">
        <div class="q-top"><span class="q-num">Question ${i + 1}</span><span class="marks">${plural(q.marks, "mark")}</span></div>
        <div class="md">${md(q.question)}</div>
        ${figureHTML(q.figure)}
        ${q.source ? `<div class="q-source">Modelled on ${esc(q.source)}</div>` : ""}
        ${q.result ? resultHTML(q) : `
          <div class="answer-area">
            <label class="sr" for="ans-${i}">Your answer</label>
            <textarea class="ans" id="ans-${i}" placeholder="Write your answer and working here">${esc(q.draft || "")}</textarea>
            <div class="answer-actions">
              <label class="btn sm">${icon("camera")}<span class="photo-name">${q.image ? esc(q.image.name) : "Add photo of working"}</span><input type="file" accept="image/*" class="img" hidden></label>
              <span class="grow" style="flex:1"></span>
              <button class="btn primary mark" type="button">Mark my answer</button>
            </div>
          </div>`}
        <details><summary>Mark scheme</summary><div class="md">${md(q.mark_scheme)}</div></details>
      </article>`).join("") + `<div style="margin-top:18px"><button class="btn quiet" id="clearq" type="button">${icon("trash")}Clear these questions</button></div>`;
  };
  const save = () => store.set(key, qs.map(({ image, ...q }) => q));
  draw();
  $("#gen").onclick = async e => {
    const b = e.currentTarget; busy(b, true, "Writing");
    try { const r = await api("/api/practice/generate", { topic: t.id, count: +$("#count").value, difficulty: $("#diff").value }); qs = r.questions; save(); draw(); }
    catch (err) { toast(err.message); }
    busy(b, false);
  };
  list.addEventListener("input", e => { if (e.target.classList.contains("ans")) { qs[+e.target.closest(".question").dataset.i].draft = e.target.value; save(); } });
  list.addEventListener("change", async e => {
    if (!e.target.classList.contains("img")) return;
    const card = e.target.closest(".question"), q = qs[+card.dataset.i];
    try { q.image = await readImage(e.target.files[0]); $(".photo-name", card).textContent = q.image ? q.image.name : "Add photo of working"; } catch (err) { toast(err.message); }
  });
  list.addEventListener("click", async e => {
    if (e.target.closest("#clearq")) { qs = []; save(); draw(); return; }
    const b = e.target.closest(".mark"); if (!b) return;
    const card = b.closest(".question"), q = qs[+card.dataset.i];
    const answer = $(".ans", card).value.trim();
    if (!answer && !q.image) { toast("Write an answer or add a photo first."); $(".ans", card).focus(); return; }
    busy(b, true, "Marking");
    try {
      q.result = await api("/api/practice/mark", { topic: t.id, question: q.question, marks: q.marks, mark_scheme: q.mark_scheme, answer, image: q.image });
      delete q.image; save(); await refresh(); draw();
      toast(`Marked. ${t.name} confidence is now ${q.result.confidence.toFixed(1)}/5`);
    } catch (err) { toast(err.message); busy(b, false); }
  });
}

async function paneCards(t) {
  $("#pane").innerHTML = `
    <div class="toolbar">
      <label class="sr" for="n">Number of cards</label>
      <select id="n">${[5, 10, 15, 20].map(n => `<option value="${n}" ${n === 10 ? "selected" : ""}>${n} cards</option>`).join("")}</select>
      <span class="muted small grow">Made from this topic's notes: definitions, formulae and methods.</span>
      ${t.due ? `<a class="btn" href="#/review/${t.id}">Review ${t.due} due</a>` : ""}
      <button class="btn primary" id="gen" type="button">Create cards</button>
    </div>
    <div id="all" class="rows"><div class="empty-note">Loading cards</div></div>`;
  $("#gen").onclick = async e => {
    const b = e.currentTarget; busy(b, true, "Creating");
    try { const r = await api("/api/cards/generate", { topic: t.id, count: +$("#n").value }); toast(`Created ${r.added} cards`); await refresh(); renderTopic(t.id, "cards"); }
    catch (err) { toast(err.message); busy(b, false); }
  };
  try {
    const { cards } = await api("/api/cards/all", { topic: t.id });
    $("#all").outerHTML = cards.length ? `<div class="rows card-list" id="all">${cards.map(c => `
      <div class="card-row"><div class="md">${md(c.front)}</div><div class="md muted back">${md(c.back)}</div>
      <div style="display:flex;align-items:center;gap:8px"><span class="tag ${c.due <= S.today ? "due" : ""}">${c.due <= S.today ? "Due" : fmtDay(c.due)}</span>
      <button class="icon-btn del" data-id="${c.id}" aria-label="Delete card" title="Delete card">${icon("trash")}</button></div></div>`).join("")}</div>`
      : `<div class="rows empty" id="all"><h3>No flashcards for this topic</h3><p>Create a set from your notes. They come back for review just before you'd forget them.</p></div>`;
    $("#all").addEventListener("click", async e => {
      const b = e.target.closest(".del"); if (!b) return;
      await api("/api/cards/delete", { id: b.dataset.id }); await refresh(); renderTopic(t.id, "cards"); toast("Card deleted");
    });
  } catch (err) { toast(err.message); }
}

async function paneFiles(t) {
  const pane = $("#pane");
  pane.innerHTML = `<div class="rows empty-note">Loading files</div>`;
  let files;
  try { ({ files } = await api("/api/topic/files", { topic: t.id })); } catch (err) { toast(err.message); return; }
  if (!files.length) { pane.innerHTML = `<div class="rows empty"><h3>No course files yet</h3><p>Files appear here once your teacher uploads them and you refresh your downloads.</p></div>`; return; }
  const scanned = files.filter(f => !f.readable).length;
  const fileRow = (f, extra = "") => {
    const ext = (f.name.split(".").pop() || "").toUpperCase();
    const inner = `<span class="ext">${esc(ext)}</span><span class="name">${esc(f.name.replace(/\.[^.]+$/, ""))}${extra}</span>${f.readable === false ? `<span class="tag">Scan</span>` : ""}${f.original ? icon("external") : ""}`;
    return f.original ? `<a class="file" href="/file?p=${encodeURIComponent(f.path)}" target="_blank" rel="noopener">${inner}</a>` : `<div class="file">${inner}</div>`;
  };
  const all = () => {
    const groups = {};
    files.forEach(f => (groups[f.folder || "Main folder"] ||= []).push(f));
    return Object.entries(groups).map(([folder, list]) => `<div class="file-group"><h3>${esc(folder)}</h3><div class="rows">${list.map(f => fileRow(f)).join("")}</div></div>`).join("");
  };
  pane.innerHTML = `
    <div class="file-search"><label class="sr" for="fs-q">Search this topic's files</label>
      <input type="search" id="fs-q" placeholder="Search ${files.length} files by name or by words inside them" autocomplete="off">
      <span class="muted small" id="fs-note"></span></div>
    <p class="muted small" style="margin-bottom:16px">${plural(files.length, "file")} from the portal${scanned ? `. ${scanned} ${scanned === 1 ? "is a scan" : "are scans"} without text, so the AI can't read ${scanned === 1 ? "it" : "them"}` : ""}. PDFs open in a new tab; slides and Word files download.</p>
    <div id="fs-list">${all()}</div>`;
  const list = $("#fs-list"), note = $("#fs-note");
  const mark = (text, words) => words.reduce((h, w) => h.replace(new RegExp(`(${w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi"), "<mark>$1</mark>"), esc(text));
  let timer, seq = 0;
  $("#fs-q").addEventListener("input", e => {
    clearTimeout(timer);
    const q = e.target.value.trim();
    if (q.length < 2) { seq++; list.innerHTML = all(); note.textContent = ""; return; }
    timer = setTimeout(async () => {
      const mine = ++seq;
      try {
        const { results, words } = await api("/api/topic/search", { topic: t.id, q });
        if (mine !== seq) return;                       // a newer search is already on its way
        note.textContent = results.length ? `${plural(results.length, "file")} found` : "";
        list.innerHTML = results.length
          ? `<div class="rows">${results.map(r => fileRow(r, `<span class="file-hit">${r.in_name ? "in the name" : `${r.hits}× in the text`}${r.folder ? ` · ${esc(r.folder)}` : ""}</span>${r.snippet && !r.in_name ? `<span class="file-snip">…${mark(r.snippet, words)}…</span>` : ""}`)).join("")}</div>`
          : `<div class="rows empty-note"><b>Nothing found for “${esc(q)}”</b>Try one word, or a word from the file's name. Scanned files have no text to search.</div>`;
      } catch (err) { toast(err.message); }
    }, 180);
  });
}

/* =====================================================================
   FLASHCARD REVIEW
   ===================================================================== */
async function renderReview(topic) {
  const t = topic ? topicById(topic) : null;
  setNav("review", "Flashcards");
  view.innerHTML = `<div class="page-head"><div><h1>Flashcards</h1><p class="sub">${t ? `Due cards for ${esc(t.name)}.` : "Every card that's due today, across all subjects."} Press Space to turn a card, then 1 to 4 to grade it.</p></div></div><div class="review-wrap" id="rev"></div>`;
  let cards = [];
  try { ({ cards } = await api("/api/cards/due", topic ? { topic } : {})); } catch (e) { toast(e.message); }
  for (let i = cards.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [cards[i], cards[j]] = [cards[j], cards[i]]; }
  const total = cards.length;
  let i = 0, shown = false, done = 0, again = 0;
  const box = $("#rev");
  const draw = () => {
    if (i >= cards.length) {
      box.innerHTML = `<div class="rows empty"><h3>${done ? "Done for today" : "Nothing due right now"}</h3>
        <p>${done ? `You reviewed ${plural(done, "card")}${again ? ` and repeated ${again} you missed` : ""}. They'll come back when they're due.` : "Create flashcards on any topic page. They show up here on the day they're due."}</p>
        <a class="btn" href="#/today">Back to Today</a></div>`;
      refresh(); return;
    }
    const c = cards[i];
    box.innerHTML = `
      <div class="progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="${total}" aria-valuenow="${done}" aria-label="Review progress"><i style="width:${total ? done / total * 100 : 0}%"></i></div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;font-size:13.5px" class="muted"><span>${subjPill(c.subject)} ${esc(c.topic_name.split(": ").slice(1).join(": "))}</span><span>${done} of ${total} done</span></div>
      <div class="flash" id="flip" style="--c:${subjColor(c.subject)}" tabindex="0" role="button" aria-label="${shown ? "Card answer" : "Show answer"}">
        <span class="side-label">${shown ? "Answer" : "Question"}</span>
        <div class="md">${md(c.front)}</div>
        ${shown ? `<div class="answer md">${md(c.back)}</div>` : `<div class="hint">Click or press Space to see the answer</div>`}
      </div>
      ${shown ? `<div class="grades">${[[0, "Again", "1", "See it again today"], [3, "Hard", "2", "Shorter gap"], [4, "Good", "3", "Normal gap"], [5, "Easy", "4", "Longer gap"]].map(([g, l, k, d]) =>
        `<button class="btn ${g === 4 ? "primary" : ""}" data-g="${g}" title="${d}">${l}<small>Key ${k}</small></button>`).join("")}</div>` : ""}`;
    if (!shown) $("#flip").focus({ preventScroll: true });
  };
  const grade = async g => {
    const c = cards[i]; i++; shown = false;
    if (g === 0) { cards.push(c); again++; } else done++;
    try { await api("/api/cards/review", { id: c.id, grade: g }); } catch (e) { toast(e.message); }
    draw();
  };
  box.onclick = e => { if (e.target.closest("#flip") && !shown) { shown = true; draw(); } const b = e.target.closest("[data-g]"); if (b) grade(+b.dataset.g); };
  current.keys = e => {
    if (e.target.matches("input, textarea, select")) return;
    if ((e.key === " " || e.key === "Enter") && !shown && i < cards.length) { e.preventDefault(); shown = true; draw(); }
    else if (shown && ["1", "2", "3", "4"].includes(e.key)) grade([0, 3, 4, 5][+e.key - 1]);
  };
  draw();
}

/* =====================================================================
   PROFILE (all numbers come from /api/stats, computed from saved data)
   ===================================================================== */
async function renderProfile() {
  setNav("profile", "Profile");
  view.innerHTML = `<div class="page-head"><div><h1>${S.name ? esc(S.name) : "Your progress"}</h1><p class="sub">Loading statistics</p></div></div>`;
  let st;
  try { st = await api("/api/stats"); } catch (e) { view.innerHTML += `<div class="rows empty"><h3>Couldn't load statistics</h3><p>${esc(e.message)}</p></div>`; return; }
  const a = st.activity, p = st.practice, c = st.cards;
  const since = a.first_day ? `Tracking since ${fmtDay(a.first_day, { day: "numeric", month: "long" })}: ${plural(a.active_days, "active day")}.` : "Nothing tracked yet. Statistics fill in as you use the tutor, practice questions and flashcards.";
  const kpi = (ic, v, unit, k) => `<div class="kpi"><div class="v">${v}${unit ? `<small>${unit}</small>` : ""}</div><div class="k">${icon(ic)}${k}</div></div>`;
  view.innerHTML = `
    <div class="page-head"><div><h1>${S.name ? esc(S.name) : "Your progress"}</h1><p class="sub">${since} Every number here comes from what you've actually done in the app.</p></div>
      <a class="btn" href="#/settings">${icon("sliders")}Edit name and exams</a></div>

    <div class="kpis">
      ${kpi("flame", a.streak, a.streak === 1 ? "day" : "days", `Current streak${a.best_streak ? `, best ${a.best_streak}` : ""}`)}
      ${kpi("clock", a.minutes_week, "min", `Study time this week${a.minutes_total ? `, ${fmtMinutes(a.minutes_total)} in total` : ""}`)}
      ${kpi("target", p.accuracy != null ? p.accuracy : "–", p.accuracy != null ? "%" : "", p.answers ? `Marks earned in ${plural(p.answers, "answer")}` : "Practice marks: none yet")}
      ${kpi("cards", c.due, "due", `Flashcards today, ${plural(c.total, "card")} in total`)}
    </div>

    <section class="section" aria-labelledby="lp-h">
      <div class="section-head"><h2 id="lp-h">Learning path</h2><p>Subtopics passed with ${st.learn.pass_mark}% or more in the mini test</p></div>
      ${learnProfileHTML(st.learn)}
    </section>

    <section class="section" aria-labelledby="act-h">
      <div class="section-head"><h2 id="act-h">Activity</h2><p>Last 16 weeks. A day counts when you study in the app.</p></div>
      <div class="panel pad activity">
        <div>${heatHTML(a.heat)}</div>
        <div class="act-facts">
          <div><div class="v">${a.active_days}</div><div class="k">Active days in total</div></div>
          <div><div class="v">${a.best_streak}</div><div class="k">Longest streak (days)</div></div>
          <div><div class="v">${a.actions_week}</div><div class="k">Study actions this week</div></div>
          <div><div class="v">${fmtMinutes(a.minutes_total)}</div><div class="k">Time in the app</div></div>
        </div>
      </div>
    </section>

    <section class="section" aria-labelledby="ready-h">
      <div class="section-head"><h2 id="ready-h">Exam readiness</h2><p>Topics in each exam's scope, by your confidence rating</p></div>
      <div class="panel pad readiness">${readinessHTML(st.exams)}</div>
    </section>

    <section class="section" aria-labelledby="sub-h">
      <div class="section-head"><h2 id="sub-h">Subjects</h2></div>
      ${subjectsTableHTML(st.subjects)}
    </section>

    <div class="cols section">
      <section aria-labelledby="pr-h">
        <div class="section-head"><h2 id="pr-h">Practice marks over time</h2><p>${p.answers ? `${p.earned} of ${p.available} marks` : ""}</p></div>
        <div class="panel pad chart">${trendHTML(p.trend)}</div>
      </section>
      <section aria-labelledby="tp-h">
        <div class="section-head"><h2 id="tp-h">Marks by topic</h2><p>Weakest first</p></div>
        <div class="panel pad">${topicBarsHTML(p.by_topic)}</div>
      </section>
    </div>

    <div class="cols section">
      <section aria-labelledby="fc-h">
        <div class="section-head"><h2 id="fc-h">Flashcards</h2><p>${c.retention != null ? `${c.retention}% remembered in the last 30 days` : ""}</p></div>
        <div class="panel pad">${cardsStatsHTML(c)}</div>
      </section>
      <section aria-labelledby="fo-h">
        <div class="section-head"><h2 id="fo-h">Reviews coming up</h2><p>Next 14 days</p></div>
        <div class="panel pad chart">${forecastHTML(c.forecast)}</div>
      </section>
    </div>

    <section class="section" aria-labelledby="ai-h">
      <div class="section-head"><h2 id="ai-h">AI analysis</h2><p>Gemini reads the numbers above and suggests a plan for the week</p></div>
      <div class="panel insight" id="insight">
        <p class="muted" style="margin-bottom:14px">So far: ${plural(st.ai.tutor, "tutor message")}, ${plural(st.ai.assistant, "assistant message")} and ${plural(st.ai.generated_questions, "practice question")} written for you.</p>
        <button class="btn primary" id="analyse" type="button">${icon("sparkles")}Analyse my progress</button>
      </div>
    </section>`;
  $("#analyse").onclick = async e => {
    const b = e.currentTarget; busy(b, true, "Analysing");
    try { const { answer } = await api("/api/insights", {}); $("#insight").innerHTML = `<div class="md">${md(answer)}</div><div style="margin-top:16px"><button class="btn sm" id="analyse-again" type="button">${icon("refresh")}Analyse again</button></div>`; $("#analyse-again").onclick = () => renderProfile(); }
    catch (err) { toast(err.message); busy(b, false); }
  };
}
function fmtMinutes(m) { return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`; }

function heatHTML(heat) {
  const lvl = d => { const s = d.actions + d.minutes / 5; return s <= 0 ? 0 : s < 3 ? 1 : s < 7 ? 2 : s < 14 ? 3 : 4; };
  const firstDow = (parseDay(heat[0].date).getDay() + 6) % 7; // Monday = 0
  const cells = Array(firstDow).fill(`<i style="visibility:hidden"></i>`).concat(heat.map(d =>
    `<i data-l="${lvl(d)}" class="${d.date === S.today ? "today" : ""}" tabindex="-1" data-tip="<b>${fmtDay(d.date, { weekday: "short", day: "numeric", month: "short" })}</b><br>${d.actions ? plural(d.actions, "action") : "No actions"}${d.minutes ? `, ${d.minutes} min` : ""}"></i>`));
  const any = heat.some(d => d.actions || d.minutes);
  return `<div style="display:flex;gap:8px"><div class="heat-days" aria-hidden="true"><span>Mon</span><span></span><span>Wed</span><span></span><span>Fri</span><span></span><span>Sun</span></div>
    <div class="heat-wrap"><div class="heat" role="img" aria-label="Study activity per day for the last 16 weeks">${cells.join("")}</div></div></div>
    <div class="heat-legend">Less <i style="background:var(--heat-0)"></i><i style="background:var(--heat-1)"></i><i style="background:var(--heat-2)"></i><i style="background:var(--heat-3)"></i><i style="background:var(--heat-4)"></i> More
    ${any ? "" : `<span style="margin-left:12px">No study days recorded yet.</span>`}</div>`;
}

function readinessHTML(exams) {
  if (!exams.length) return `<div class="empty-note"><b>No exams set</b>Add exam dates in Settings.</div>`;
  return exams.map(e => {
    const notReady = e.in_scope - e.ready;
    return `<div class="exam">
      <div><h3>${esc(e.name)}</h3><div class="small muted" style="margin-top:3px">${e.ready} of ${plural(e.in_scope, "topic")} rated 3/5 or higher${e.not_started ? `. ${e.not_started} taught ${e.not_started === 1 ? "topic hasn't" : "topics haven't"} been studied in the app` : ""}.</div></div>
      <div class="days-left">${e.days >= 0 ? e.days : "Done"}<small>${e.days >= 0 ? "days left" : fmtDay(e.date)}</small></div>
      <div class="stack-bar" role="img" aria-label="${e.ready} ready, ${notReady} not yet">
        ${e.ready ? `<i style="width:${e.ready / e.in_scope * 100}%;background:${subjColor(e.subject)}" data-tip="${e.ready} rated 3+"></i>` : ""}
        ${notReady ? `<i style="width:${notReady / e.in_scope * 100}%;background:var(--surface-3)" data-tip="${notReady} below 3 or not rated"></i>` : ""}
      </div>
      <div class="legend"><span class="legend-item"><span class="legend-dot" style="background:${subjColor(e.subject)}"></span>Rated 3+ (${e.ready})</span><span class="legend-item"><span class="legend-dot" style="background:var(--surface-3);border:1px solid var(--rule-strong)"></span>Below 3 or not rated (${notReady})</span></div>
    </div>`;
  }).join("");
}

function subjectsTableHTML(subjects) {
  return `<div class="table-wrap"><table>
    <thead><tr><th>Subject</th><th class="num">Taught</th><th class="num">Studied</th><th class="num">Avg confidence</th><th class="num">Practice marks</th><th class="num">Cards</th><th class="num">Course files</th></tr></thead>
    <tbody>${subjects.map(s => `<tr class="link" data-href="#/subject/${encodeURIComponent(s.name)}">
      <td><span class="subj-pill" style="font-size:14.5px;color:var(--ink)"><i style="background:${subjColor(s.name)};width:10px;height:10px"></i>${esc(s.name)}</span></td>
      <td class="num">${s.taught}/${s.topics}</td><td class="num">${s.studied}</td>
      <td class="num">${s.confidence != null ? `${s.confidence}<span class="faint"> /5</span><div class="small faint">${s.rated} rated</div>` : `<span class="faint">Not rated</span>`}</td>
      <td class="num">${s.practice_accuracy != null ? `${s.practice_accuracy}%<div class="small faint">${plural(s.practice_answers, "answer")}</div>` : `<span class="faint">None yet</span>`}</td>
      <td class="num">${s.cards}</td><td class="num">${s.files}</td></tr>`).join("")}</tbody></table></div>`;
}
document.addEventListener("click", e => { const tr = e.target.closest("tr.link"); if (tr && !e.target.closest("a") && tr.closest("#view") && current.page === "profile") location.hash = tr.dataset.href; });

function trendHTML(trend) {
  if (trend.length < 2) return `<div class="empty-note"><b>${trend.length ? "One answer marked so far" : "No marked answers yet"}</b>Mark at least two practice answers to see your trend. Open any topic and choose Practice questions.</div>`;
  const W = 560, H = 210, L = 34, R = 10, T = 12, B = 26;
  const x = i => L + (W - L - R) * (trend.length === 1 ? 0.5 : i / (trend.length - 1));
  const y = v => T + (H - T - B) * (1 - v / 100);
  const grid = [0, 50, 100].map(v => `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" /><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${v}%</text>`).join("");
  const path = trend.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(d.pct).toFixed(1)}`).join(" ");
  const pts = trend.map((d, i) => `<circle cx="${x(i)}" cy="${y(d.pct)}" r="5" fill="${subjColor(d.subject)}" stroke="var(--surface)" stroke-width="2" tabindex="0"
      data-tip="<b>${d.pct}%</b> ${esc(d.topic)}<br>${esc(SHORT[d.subject])}, ${fmtDay(d.date)}" />`).join("");
  const subs = [...new Set(trend.map(d => d.subject))];
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Score of each marked answer over time">${grid}
    <path d="${path}" fill="none" stroke="var(--ink-3)" stroke-width="2" stroke-linejoin="round" />${pts}
    <text x="${L}" y="${H - 6}">${fmtDay(trend[0].date)}</text><text x="${W - R}" y="${H - 6}" text-anchor="end">${fmtDay(trend[trend.length - 1].date)}</text></svg>
    <div class="heat-legend" style="gap:14px">${subs.map(s => `<span class="legend-item"><span class="legend-dot" style="background:${subjColor(s)}"></span>${esc(SHORT[s])}</span>`).join("")}</div>`;
}

function topicBarsHTML(rows) {
  if (!rows.length) return `<div class="empty-note"><b>No marked answers yet</b>Each topic appears here after you mark a practice answer for it.</div>`;
  return rows.slice(0, 8).map(r => `<div class="hbar">
    <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(r.name)}">${esc(r.name)}</span>
    <span class="track"><i style="width:${r.accuracy}%;background:${subjColor(r.subject)}" data-tip="<b>${r.accuracy}%</b>: ${r.earned} of ${r.available} marks in ${plural(r.answers, "answer")}"></i></span>
    <span class="val">${r.accuracy}%</span></div>`).join("");
}

function cardsStatsHTML(c) {
  if (!c.total) return `<div class="empty-note"><b>No flashcards yet</b>Create cards on any topic page, then review them here every day.</div>`;
  const seg = [["New", c.new, "var(--surface-3)"], ["Learning", c.learning, "var(--heat-2)"], ["Mature (3+ weeks)", c.mature, "var(--pen)"]];
  return `<div class="stack-bar" style="height:16px" role="img" aria-label="${seg.map(s => `${s[1]} ${s[0]}`).join(", ")}">${seg.filter(s => s[1]).map(s => `<i style="width:${s[1] / c.total * 100}%;background:${s[2]}" data-tip="${s[1]} ${s[0].toLowerCase()}"></i>`).join("")}</div>
    <div class="heat-legend" style="gap:16px;margin-bottom:18px">${seg.map(s => `<span class="legend-item"><span class="legend-dot" style="background:${s[2]};border:1px solid var(--rule-strong)"></span>${s[0]} ${s[1]}</span>`).join("")}</div>
    <div class="facts" style="font-size:14.5px"><span><b>${c.reviews_week}</b> reviews this week</span><span><b>${c.reviews_total}</b> in total</span><span>Remembered: <b>${c.retention != null ? c.retention + "%" : "no reviews yet"}</b></span></div>`;
}

function forecastHTML(fc) {
  if (!fc.some(d => d.due)) return `<div class="empty-note"><b>Nothing scheduled</b>Reviews appear here once you have flashcards.</div>`;
  const W = 560, H = 190, L = 28, R = 6, T = 10, B = 26, max = Math.max(...fc.map(d => d.due), 1);
  const bw = (W - L - R) / fc.length;
  const y = v => T + (H - T - B) * (1 - v / max);
  const bars = fc.map((d, i) => {
    const h = H - B - y(d.due), x = L + i * bw + 3;
    return `<g data-tip="<b>${d.due} due</b><br>${i === 0 ? "Today (incl. overdue)" : fmtDay(d.date, { weekday: "short", day: "numeric", month: "short" })}" tabindex="0">
      <rect x="${x - 3}" y="${T}" width="${bw}" height="${H - T - B}" fill="transparent" />
      ${d.due ? `<rect x="${x}" y="${y(d.due)}" width="${bw - 6}" height="${h}" rx="4" fill="${i === 0 ? "var(--pen)" : "var(--heat-2)"}" />` : ""}
      ${i % 2 === 0 ? `<text x="${x + (bw - 6) / 2}" y="${H - 8}" text-anchor="middle">${i === 0 ? "Today" : parseDay(d.date).getDate()}</text>` : ""}</g>`;
  }).join("");
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Flashcards due on each of the next 14 days">
    <line class="axis" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}" /><text x="${L - 6}" y="${y(max) + 4}" text-anchor="end">${max}</text>${bars}</svg>`;
}

/* =====================================================================
   SETTINGS
   ===================================================================== */
function renderSettings() {
  setNav("settings", "Settings");
  const theme = store.get("theme", "dark");
  view.innerHTML = `
    <div class="page-head"><div><h1>Settings</h1></div></div>
    <div style="max-width:680px;display:grid;gap:22px">
      ${syncSettingsHTML()}
      <section class="panel pad" style="display:grid;gap:14px">
        <h2>Gemini</h2>
        <p class="muted small">${S.has_key ? "A key is saved on this Mac. Paste a new one to replace it." : `Get a free key at <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">aistudio.google.com/apikey</a>. It's saved only in this app's folder.`}</p>
        <label class="field"><span>API key</span>
          <div style="display:flex;gap:8px"><input type="password" id="key" placeholder="${S.has_key ? "Saved. Paste a new key to replace it" : "Paste your key (starts with AIza)"}" autocomplete="off"><button class="btn primary" id="savekey" type="button">Save key</button></div>
          <span id="keymsg" class="small" role="status"></span></label>
        <label class="field"><span>Model <span class="hint">Leave empty to use the newest Gemini Flash automatically</span></span>
          <div style="display:flex;gap:8px"><input type="text" id="model" value="${esc(S.model)}" placeholder="Automatic"><button class="btn" id="savemodel" type="button">Save</button></div></label>
      </section>
      <section class="panel pad" style="display:grid;gap:14px">
        <h2>You</h2>
        <label class="field"><span>Your name <span class="hint">Shown on Today and Profile</span></span>
          <div style="display:flex;gap:8px"><input type="text" id="name" value="${esc(S.name)}" maxlength="40" placeholder="First name"><button class="btn" id="savename" type="button">Save</button></div></label>
      </section>
      <section class="panel pad" style="display:grid;gap:14px">
        <h2>Exam dates</h2>
        <p class="muted small">Set to the start of the January exam period from your calendars. Change them when your exam timetable comes out.</p>
        <div id="exams" style="display:grid;gap:8px">${S.exams.map(examRow).join("")}</div>
        <div style="display:flex;gap:8px;justify-content:space-between"><button class="btn" id="addexam" type="button">Add exam</button><button class="btn primary" id="saveexams" type="button">Save exam dates</button></div>
      </section>
    </div>`;
  // Appearance goes first: colour scheme picker with live previews
  const pal = store.get("palette", "graphite");
  $("#view > div[style]").insertAdjacentHTML("afterbegin", `
      <section class="panel pad" style="display:grid;gap:16px" id="appearance">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
          <h2>Colour scheme</h2>
          <div class="seg" role="group" aria-label="Light or dark">${[["auto", "Match system"], ["light", "Light"], ["dark", "Dark"]].map(([v, l]) => `<button type="button" data-theme="${v}" aria-pressed="${v === theme}" style="padding:0 14px">${l}</button>`).join("")}</div>
        </div>
        <p class="muted small">Click a scheme to switch the whole app at once. Each one has a light and a dark version. Subject colours stay the same in every scheme, so Maths is always blue.</p>
        <div class="pal-grid">${Object.entries(window.PALETTES || {}).map(([id, p]) => paletteCard(id, p, id === pal)).join("")}</div>
      </section>`);
  $(".pal-grid").addEventListener("click", e => {
    const c = e.target.closest("[data-pick]"); if (!c) return;
    store.set("palette", c.dataset.pick); applyTheme(); renderSettings();
    toast(`${window.PALETTES[c.dataset.pick].name} scheme on. Open Today to see it in place.`);
  });
  const msg = $("#keymsg");
  $("#savekey").onclick = async e => {
    const k = $("#key").value.trim();
    if (!k) { msg.style.color = "var(--red)"; msg.textContent = "Paste your key into the box first."; return; }
    const b = e.currentTarget; busy(b, true, "Checking"); msg.style.color = ""; msg.textContent = "Saving and checking the key with Google";
    try { const r = await api("/api/settings", { key: k }); await refresh(); renderSettings(); const m = $("#keymsg"); m.style.color = "var(--green)"; m.textContent = `Key saved and working. Using ${r.model}.`; }
    catch (err) { msg.style.color = "var(--red)"; msg.textContent = err.message; busy(b, false); }
  };
  $("#savemodel").onclick = async () => { await api("/api/settings", { model: $("#model").value }); await refresh(); toast("Model saved"); };
  $("#savename").onclick = async () => { await api("/api/settings", { name: $("#name").value }); await refresh(); toast("Name saved"); };
  $("#addexam").onclick = () => $("#exams").insertAdjacentHTML("beforeend", examRow({ subject: "", name: "", date: S.today }));
  $("#exams").onclick = e => { if (e.target.closest(".rm")) e.target.closest(".exam-row").remove(); };
  $("#saveexams").onclick = async () => {
    const exams = $$(".exam-row").map(r => ({ subject: $(".es", r).value, name: $(".en", r).value.trim(), date: $(".ed", r).value }));
    await api("/api/settings", { exams }); await refresh(); toast("Exam dates saved");
  };
  $$("[data-theme]", view).forEach(b => b.onclick = () => { store.set("theme", b.dataset.theme); applyTheme(); renderSettings(); });
}
const examRow = e => `<div class="exam-row" style="display:flex;gap:8px;flex-wrap:wrap">
  <select class="es" aria-label="Subject">${["", ...S.subjects.map(s => s.name)].map(s => `<option value="${esc(s)}" ${s === e.subject ? "selected" : ""}>${esc(s || "Other")}</option>`).join("")}</select>
  <input type="text" class="en" value="${esc(e.name)}" placeholder="Exam name" aria-label="Exam name" style="flex:1;min-width:180px">
  <input type="date" class="ed" value="${esc(e.date)}" aria-label="Exam date" style="width:auto">
  <button class="icon-btn rm" type="button" aria-label="Remove this exam">${icon("x")}</button></div>`;
function applyTheme() {
  const t = store.get("theme", "dark");
  if (t === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.dataset.theme = t;
  const p = store.get("palette", "graphite");
  document.documentElement.dataset.palette = window.PALETTES?.[p] ? p : "graphite";
}

/* Palettes: one CSS block per palette, so any element with data-palette previews it in the current light/dark mode. */
(function injectPalettes() {
  const P = window.PALETTES || {};
  const decl = toks => Object.entries(toks).map(([k, v]) => `--${k}:${v}`).join(";");
  let css = "";
  for (const [id, p] of Object.entries(P)) {
    const self = `[data-palette="${id}"]`;
    css += `${self}{${decl(p.light)}}\n`;
    css += `@media (prefers-color-scheme: dark){:root:not([data-theme="light"])${self},:root:not([data-theme="light"]) ${self}{${decl(p.dark)}}}\n`;
    css += `:root[data-theme="dark"]${self},:root[data-theme="dark"] ${self}{${decl(p.dark)}}\n`;
  }
  const s = document.createElement("style"); s.id = "palettes"; s.textContent = css; document.head.appendChild(s);
})();

function paletteCard(id, p, selected) {
  const subjects = S.subjects.map(s => s.name);
  return `<button type="button" class="pal-card" data-palette="${id}" data-pick="${id}" aria-pressed="${selected}">
    <span class="pal-preview" aria-hidden="true">
      <span class="pal-rail"><i class="on"></i><i></i><i></i>${subjects.map(s => `<i class="dot" style="background:${subjColor(s)}"></i>`).join("")}</span>
      <span class="pal-main">
        <span class="pal-title">Good evening</span>
        <span class="pal-line"></span><span class="pal-line short"></span>
        <span class="pal-row"><span class="pal-btn">Practise</span><span class="meter l4"><i class="on"></i><i class="on"></i><i class="on"></i><i class="on"></i><i></i></span></span>
        <span class="pal-heat">${[0, 1, 2, 3, 4, 2, 1, 3, 4, 0, 2, 3].map(l => `<i style="background:var(--heat-${l})"></i>`).join("")}</span>
      </span>
    </span>
    <span class="pal-meta"><b>${esc(p.name)}</b>${selected ? `<span class="tag due">In use</span>` : ""}<span class="pal-desc">${esc(p.desc)}</span></span>
  </button>`;
}

/* =====================================================================
   ASSISTANT (global, Gemini)
   ===================================================================== */
const AS = { open: false, msgs: store.get("assistant:chat", []) };
const asEl = $("#assistant"), asBody = $("#as-body"), asInput = $("#as-input");
function asDraw() {
  const t = current.topic ? topicById(current.topic) : null;
  $("#as-context").innerHTML = t ? `${icon("file")}Also reading your ${esc(t.name)} files` : "";
  asBody.innerHTML = AS.msgs.length ? chatHTML(AS.msgs) : `
    <div class="chat-empty"><h3>What can I help with?</h3>
    <p class="muted small" style="margin-bottom:14px">I can see your topics, exam dates, confidence ratings, practice marks and flashcards.</p>
    <div class="chips" style="flex-direction:column;align-items:stretch">${["What should I study today?", "Make me a revision plan until the January exams", "Which topics am I weakest in?", "What is on the Chemistry end of Semester 1 test?", "How does the EAP writing exam work?"].map(q => `<button class="chip" type="button">${q}</button>`).join("")}</div></div>`;
  asBody.scrollTop = asBody.scrollHeight;
}
function asToggle(open) {
  AS.open = open ?? !AS.open;
  asEl.hidden = !AS.open; $("#scrim").hidden = !AS.open || innerWidth > 1400;
  $("#open-assistant").setAttribute("aria-expanded", AS.open);
  if (AS.open) { asDraw(); setTimeout(() => asInput.focus(), 50); } else $("#open-assistant").focus({ preventScroll: true });
}
async function asSend(text) {
  text = text.trim(); if (!text) return;
  AS.msgs.push({ role: "user", text }); asInput.value = ""; asInput.style.height = "";
  asDraw(); asBody.insertAdjacentHTML("beforeend", thinkingHTML("Thinking")); asBody.scrollTop = asBody.scrollHeight;
  $("#as-send").disabled = true;
  try {
    const { answer } = await api("/api/assistant", { messages: AS.msgs, topic: current.topic || null });
    AS.msgs.push({ role: "model", text: answer });
  } catch (e) { const last = AS.msgs.pop(); asInput.value = last.text; toast(e.message); }
  $("#as-send").disabled = false;
  store.set("assistant:chat", AS.msgs.slice(-40)); asDraw();
  const lastModel = $$(".msg.model", asBody).pop(); if (lastModel) asBody.scrollTop = lastModel.offsetTop - 12;
}
$("#open-assistant").onclick = () => asToggle();
$("#as-close").onclick = () => asToggle(false);
$("#scrim").onclick = () => asToggle(false);
$("#as-clear").onclick = () => { AS.msgs = []; store.set("assistant:chat", []); asDraw(); asInput.focus(); };
$("#as-form").onsubmit = e => { e.preventDefault(); asSend(asInput.value); };
asInput.onkeydown = e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); asSend(asInput.value); } };
asInput.oninput = () => { asInput.style.height = "auto"; asInput.style.height = Math.min(asInput.scrollHeight, 180) + "px"; };
asBody.addEventListener("click", e => { const c = e.target.closest(".chip"); if (c) asSend(c.textContent); });
document.addEventListener("click", e => { if (e.target.closest("[data-open-assistant]")) asToggle(true); });
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && AS.open) { asToggle(false); return; }
  if (e.key === "/" && !e.target.matches("input, textarea, select") && !AS.open) { e.preventDefault(); asToggle(true); return; }
  current.keys?.(e);
});

/* ---------- real study time: one ping per active minute ---------- */
let lastInput = Date.now();
["pointerdown", "keydown", "scroll", "pointermove"].forEach(ev => addEventListener(ev, () => { lastInput = Date.now(); }, { passive: true }));
setInterval(() => { if (document.visibilityState === "visible" && Date.now() - lastInput < 120000) api("/api/ping", {}).catch(() => {}); }, 60000);

/* ---------- router ---------- */
async function refresh() {
  S = await api("/api/state");
  const used = new Set(S.subjects.map(s => SUBJECT_VAR[s.name]).filter(Boolean));
  const free = SUBJECT_SLOTS.filter(v => !used.has(v));
  S.subjects.forEach((s, i) => {          // any subject list: unknown subjects take the colours nobody uses
    SUBJECT_VAR[s.name] ??= free.shift() || SUBJECT_SLOTS[i % SUBJECT_SLOTS.length];
    SHORT[s.name] ??= s.name;
  });
  if (typeof syncLoad === "function") await syncLoad();
  if (typeof programLoad === "function") await programLoad();
  navHTML();
  if (current.page) { const [k, title] = navKeyFor(current.page, current.a); setNav(k, title); }
}
async function route() {
  const [, page = "today", a, b, c] = decodeURIComponent(location.hash || "#/today").split("/");
  current = { page, a, topic: page === "topic" || page === "learn" ? a : null, sub: page === "learn" ? b : null };
  tip.hidden = true;
  try {
    await refresh();
    if (page === "subjects") renderSubjects();
    else if (page === "subject") renderSubject(a);
    else if (page === "topic") renderTopic(a, b);
    else if (page === "learn") await renderLearn(a, b, c);
    else if (page === "timetable") renderTimetable();
    else if (page === "program") await renderProgram();
    else if (page === "progress") renderProgress();
    else if (page === "files") await renderFilesCheck();
    else if (page === "review") await renderReview(a);
    else if (page === "profile") await renderProfile();
    else if (page === "settings") renderSettings();
    else renderToday();
  } catch (e) {
    view.innerHTML = `<div class="rows empty" style="margin-top:40px"><h3>Study Desk can't reach its server</h3><p>Start it with “Start Study Desk.command” in the study-app folder, then reload this page.</p><p class="small faint">${esc(e.message)}</p></div>`;
  }
  if (AS.open) asDraw();
  view.focus({ preventScroll: true });
  scrollTo(0, route.keepScroll || 0);       // ticking a topic redraws the page in place
  route.keepScroll = 0;
}
function navKeyFor(page, a) {
  if (page === "subject") return ["subject:" + a, a];
  if (page === "topic" || page === "learn") { const t = topicById(a); return t ? ["subject:" + t.subject, t.name] : ["today", "Today"]; }
  const titles = { progress: "Progress", program: "Programme", files: "Files check", timetable: "Timetable", subjects: "Subjects", review: "Flashcards", profile: "Profile", settings: "Settings" };
  return [titles[page] ? page : "today", titles[page] || "Today"];
}
applyTheme();
paintIcons();
addEventListener("hashchange", route);
addEventListener("DOMContentLoaded", route);   // after every script (progress.js, program.js…) has loaded
