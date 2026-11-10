"use strict";
/* Weekly class timetable (topics.json → "timetable"): Today's classes and the Timetable page. */

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const isoWeekday = iso => ((parseDay(iso).getDay() + 6) % 7) + 1;           // 1 = Monday … 7 = Sunday
const breakOn = iso => (S.breaks || []).find(b => b.start <= iso && iso <= b.end);
const toMin = hhmm => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };
const nowMin = () => { const d = new Date(); return d.getHours() * 60 + d.getMinutes(); };
const localIso = (d = new Date()) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function classesOn(iso) {
  if (breakOn(iso)) return [];
  const wd = isoWeekday(iso);
  return (S.timetable || []).filter(c => c.day === wd).sort((a, b) => a.start.localeCompare(b.start));
}
// The topic currently being taught in that subject: the latest one that has started.
function classTopic(subject, iso = S.today) {
  const c = typeof PG !== "undefined" ? PG.d?.subjects.find(s => s.name === subject)?.compass : null;   // Compass: that day's topic
  const lesson = (c?.lessons || []).find(l => l.date === iso);
  if (lesson && topicById(lesson.topic)) return topicById(lesson.topic);
  return allTopics().filter(t => t.subject === subject && t.start <= iso).sort((a, b) => b.start.localeCompare(a.start))[0];
}
function classState(c, iso) {
  if (iso !== localIso()) return "";
  const n = nowMin();
  if (n >= toMin(c.end)) return "done";
  if (n >= toMin(c.start)) return "now";
  return "later";
}

function classRow(c, iso, isNext) {
  const t = classTopic(c.subject, iso);
  const st = classState(c, iso);
  const tag = st === "now" ? `<span class="tag due">In class now</span>`
    : st === "done" ? `<span class="tag">Done</span>`
    : isNext ? `<span class="tag soon">Next</span>` : "";
  const body = `
      <div class="row-title"><span class="cls-time">${c.start}–${c.end}</span>${subjPill(c.subject)}<span>${t ? esc(t.name) : esc(c.subject)}</span></div>
      <div class="row-meta"><span>Room ${esc(c.room)}</span><span>${esc(c.teacher)}</span><span>${esc(c.code)}</span></div>
      <div class="row-side">${tag}</div>`;
  return t ? `<a class="row-link cls-row ${st}" href="#/topic/${t.id}/check">${body}</a>` : `<div class="row-link cls-row ${st}">${body}</div>`;
}

// Today page: today's classes, or the next day that has classes.
function classesTodayHTML() {
  if (!(S.timetable || []).length) return "";
  const today = S.today;
  let list = classesOn(today), iso = today;
  const leftToday = iso === localIso() ? list.filter(c => toMin(c.end) > nowMin()) : list;
  let title = "Classes today", note = "";
  if (!leftToday.length) {
    for (let i = 1; i <= 40; i++) {
      const d = addDays(today, i);
      if (classesOn(d).length) { iso = d; break; }
    }
    if (iso === today) return "";
    const br = breakOn(today);
    note = list.length ? "Today's classes are over." : br ? `${br.name}.` : "No classes today.";
    title = `Next classes: ${dayDiff(today, iso) === 1 ? "tomorrow" : WEEKDAYS[isoWeekday(iso) - 1]}`;
    list = classesOn(iso);
  }
  const next = iso === localIso() ? list.find(c => toMin(c.start) > nowMin()) : list[0];
  return `
    <section class="section" aria-labelledby="cls-h">
      <div class="section-head"><h2 id="cls-h">${esc(title)}</h2><p>${note ? esc(note) + " " : ""}${fmtDay(iso, { weekday: "long", day: "numeric", month: "long" })} · <a href="#/timetable">Full week</a></p></div>
      <div class="rows">${list.map(c => classRow(c, iso, c === next)).join("")}</div>
    </section>`;
}

// Timetable page: Monday–Friday grid for the current week (next week at the weekend).
function renderTimetable() {
  setNav("timetable", "Timetable");
  const tt = S.timetable || [];
  const wd = isoWeekday(S.today);
  const monday = addDays(S.today, wd >= 6 ? 8 - wd : 1 - wd);
  const days = [0, 1, 2, 3, 4].map(i => addDays(monday, i));
  const first = Math.min(...tt.map(c => toMin(c.start)), 9 * 60), last = Math.max(...tt.map(c => toMin(c.end)), 17 * 60);
  const h0 = Math.floor(first / 60), h1 = Math.ceil(last / 60), HOUR = 64;
  const hours = []; for (let h = h0; h < h1; h++) hours.push(h);
  const perSubj = {};
  tt.forEach(c => { perSubj[c.subject] = (perSubj[c.subject] || 0) + (toMin(c.end) - toMin(c.start)) / 60; });
  const isToday = d => d === localIso();
  const n = nowMin();

  view.innerHTML = `
    <div class="page-head"><div>
      <h1>Timetable</h1>
      <p class="sub">Week of ${fmtDay(monday, { day: "numeric", month: "long" })}. ${tt.length} ${tt.length === 1 ? "class" : "classes"} a week: ${
        Object.entries(perSubj).map(([s, h]) => `${esc(SHORT[s] || s)} ${h} h`).join(", ")}. Click a class to open the topic being taught now.</p>
    </div></div>
    <div class="tt" style="--hour:${HOUR}px; --hours:${hours.length}">
      <div class="tt-hours" aria-hidden="true"><div class="tt-dayhead"></div>${hours.map(h => `<div class="tt-hour">${String(h).padStart(2, "0")}:00</div>`).join("")}</div>
      ${days.map((d, i) => {
        const br = breakOn(d);
        const cls = br ? [] : tt.filter(c => c.day === i + 1).sort((a, b) => a.start.localeCompare(b.start));
        const nowLine = isToday(d) && n >= h0 * 60 && n <= h1 * 60 ? `<div class="tt-now" style="top:${(n - h0 * 60) / 60 * HOUR}px" aria-hidden="true"></div>` : "";
        return `<section class="tt-day ${isToday(d) ? "today" : ""}" aria-label="${WEEKDAYS[i]}">
          <div class="tt-dayhead"><b>${WEEKDAYS[i].slice(0, 3)}</b><span>${fmtDay(d)}</span></div>
          <div class="tt-col">
            ${br ? `<div class="tt-break">${esc(br.name)}</div>` : ""}
            ${!br && !cls.length ? `<div class="tt-free">No classes</div>` : ""}
            ${cls.map(c => {
              const t = classTopic(c.subject, d);
              const top = (toMin(c.start) - h0 * 60) / 60 * HOUR, h = (toMin(c.end) - toMin(c.start)) / 60 * HOUR;
              const st = isToday(d) ? classState(c, d) : "";
              const inner = `<span class="tt-time">${c.start}–${c.end}</span><b>${esc(SHORT[c.subject] || c.subject)}</b>
                <span class="tt-topic">${t ? esc(t.name) : ""}</span><span class="tt-room">${esc(c.room)} · ${esc(c.teacher)}</span>`;
              const attrs = `class="tt-class ${st}" style="top:${top}px; height:${h - 4}px; --c:${subjColor(c.subject)}"`;
              return t ? `<a ${attrs} href="#/topic/${t.id}/check">${inner}</a>` : `<div ${attrs}>${inner}</div>`;
            }).join("")}
            ${nowLine}
          </div></section>`;
      }).join("")}
    </div>`;
}
