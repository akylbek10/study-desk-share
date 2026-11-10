"use strict";
/* Progress: you learn in Gemini; Study Desk keeps score.
   studied   = you ticked the topic (or worked on it here)
   confirmed = you proved it: 80%+ in the check here or in Gemini's final test
   behind    = its class weeks are over and it is not studied */

const weeksLabel = t => !t.weeks ? "" : t.weeks[0] === t.weeks[1] ? `Week ${t.weeks[0]}` : `Weeks ${t.weeks[0]}–${t.weeks[1]}`;
const inClassNow = t => t.weeks && PG.d?.week && t.weeks[0] <= PG.d.week && PG.d.week <= t.weeks[1];

function statusTag(t) {
  if (t.status === "confirmed") return `<span class="tag good">${icon("check")}Confirmed</span>`;
  if (t.status === "studied") return `<span class="tag soon">${t.to_confirm ? "Studied, not confirmed" : "Studied"}</span>`;
  if (t.behind) return `<span class="tag bad">Behind</span>`;
  if (inClassNow(t)) return `<span class="tag due">In class now</span>`;
  return `<span class="tag">${t.ended === false && taught(t) ? "Not studied" : "Not started"}</span>`;
}

/* one row = one topic with its status and the three things you can do */
function progressRow(t, { pill = true } = {}) {
  const done = t.status !== "none";
  return `<div class="pr-row ${t.behind ? "late" : ""}" data-topic="${t.id}">
    <button class="pr-tick ${done ? "on" : ""}" type="button" data-mark="${t.id}" data-on="${done ? 1 : 0}" ${t.status === "confirmed" ? "disabled" : ""}
      aria-pressed="${done}" aria-label="${done ? "Studied" : "Mark as studied"}: ${esc(t.name)}" data-tip="${t.status === "confirmed" ? "Confirmed" : done ? "Studied. Click to untick" : "Tick when you have studied it"}">${icon("check")}</button>
    <div class="pr-main">
      <div class="row-title">${pill ? subjPill(t.subject) : ""}<a href="#/topic/${t.id}/check">${esc(t.name)}</a></div>
      <div class="row-meta"><span>${weeksLabel(t) || "All semester"}</span>${t.studied_on && t.status === "studied" ? `<span>studied ${relDay(t.studied_on)}</span>` : ""}${t.confirmed_on ? `<span>confirmed ${relDay(t.confirmed_on)}</span>` : ""}${t.check_best != null && t.status !== "confirmed" ? `<span>best check ${t.check_best}%</span>` : ""}</div>
    </div>
    <div class="pr-side">${statusTag(t)}
      ${t.status === "confirmed" ? "" : `<a class="btn sm ${t.status === "studied" ? "primary" : ""}" href="#/topic/${t.id}/check">Check</a>`}
      <a class="btn sm" href="#/topic/${t.id}/review">Review</a>
      <button class="btn sm" type="button" data-gemini="${t.id}" aria-label="Study ${esc(t.name)} in Gemini">${icon("sparkles")}Gemini</button>
    </div>
  </div>`;
}

document.addEventListener("click", async e => {
  const b = e.target.closest("[data-mark]");
  if (!b || b.disabled) return;
  try {
    const on = b.dataset.on !== "1";
    await api("/api/topic/mark", { topic: b.dataset.mark, studied: on });
    toast(on ? "Marked as studied. Confirm it with a check when you're ready." : "Unticked");
    route.keepScroll = scrollY; route();
  } catch (err) { toast(err.message); }
});

const standingLists = () => {
  const all = allTopics();
  const by = (a, b) => (b.weeks?.[1] || 0) - (a.weeks?.[1] || 0);        // most recent first
  return { all, behind: all.filter(t => t.behind).sort(by), toConfirm: all.filter(t => t.to_confirm).sort(by),
           ended: all.filter(t => t.ended), confirmed: all.filter(t => t.ended && t.status === "confirmed") };
};

function standStatsHTML() {
  const s = standingLists();
  const next = (S.assessments || [])[0];
  const stat = (n, label, cls = "") => `<a class="fc-stat ${cls}" href="#/progress"><b>${n}</b><span>${label}</span></a>`;
  return `<div class="fc-stats">
    ${stat(`${s.confirmed.length}/${s.ended.length}`, "finished topics confirmed", s.ended.length && s.confirmed.length === s.ended.length ? "good" : "")}
    ${stat(s.toConfirm.length, "studied, not confirmed", s.toConfirm.length ? "warn" : "good")}
    ${stat(s.behind.length, "behind: taught, not studied", s.behind.length ? "bad" : "good")}
    ${next ? stat(next.kind === "plan" ? `wk ${next.week}` : Math.max(0, dayDiff(S.today, next.date)) + " d", esc(next.name.length > 46 ? next.name.slice(0, 44) + "…" : next.name)) : ""}
  </div>`;
}

/* Today: what to do next, in order */
function doNextHTML() {
  const s = standingLists(), steps = [];
  const untouched = s.ended.length >= 4 && s.ended.every(t => t.status === "none");
  if (untouched) steps.push({ what: "Tick the topics you have already studied", why: `Study Desk doesn't know yet what you covered in the first ${PG.d?.week ? PG.d.week - 1 : ""} weeks. It takes a minute and makes everything below true.`, href: "#/progress", cta: "Open Progress" });
  // the next class whose topic you haven't touched: preview it
  const tmr = addDays(S.today, 1);
  for (const iso of [S.today, tmr]) {
    for (const c of classesOn(iso)) {
      const t = classTopic(c.subject, iso);
      if (t && t.status === "none" && !steps.some(x => x.tid === t.id) && (iso !== S.today || toMin(c.start) > nowMin()))
        steps.push({ tid: t.id, what: `Before ${iso === S.today ? "today's" : "tomorrow's"} ${SHORT[c.subject]} class: ${t.name}`, why: `${c.start}, room ${c.room}. Go through it in Gemini first, then tick it.`, gem: t.id, cta: "Study in Gemini" });
    }
  }
  if (s.toConfirm[0]) { const t = s.toConfirm[s.toConfirm.length - 1]; steps.push({ what: `Confirm ${t.name}`, why: `You studied it ${t.studied_on ? relDay(t.studied_on) : ""} but haven't proved it yet. ${S.pass_mark}% in an 8-question check confirms it.`, href: `#/topic/${t.id}/check`, cta: "Take the check" }); }
  if (!untouched && s.behind[0]) { const t = s.behind[0]; steps.push({ what: `Catch up: ${t.name}`, why: `${SHORT[t.subject]}, taught in ${weeksLabel(t).toLowerCase()}. ${plural(s.behind.length, "topic")} behind in total.`, gem: t.id, cta: "Study in Gemini" }); }
  if (S.due_total) steps.push({ what: `Review ${plural(S.due_total, "flashcard")}`, why: `Due today. About ${Math.max(1, Math.round(S.due_total * 12 / 60))} min.`, href: "#/review", cta: "Start review" });
  if (!steps.length) return `<div class="rows empty-note"><b>You're up to date</b>Nothing behind and nothing waiting for a check.</div>`;
  return `<ol class="plan rows">${steps.slice(0, 4).map(x => `<li><div><div class="what">${esc(x.what)}</div><div class="why">${esc(x.why)}</div></div>${
    x.gem ? `<button class="btn sm" type="button" data-gemini="${x.gem}">${icon("sparkles")}${x.cta}</button>` : `<a class="btn sm" href="${x.href}">${esc(x.cta)}</a>`}</li>`).join("")}</ol>`;
}

/* Today: assessments and exams with how ready you are */
function assessmentsHTML() {
  const soon = (S.assessments || []).filter(a => a.kind !== "exam").slice(0, 4);
  const rows = soon.map(a => `<div class="row-link" style="cursor:default">
      <div class="row-title">${subjPill(a.subject)}<span>${esc(a.name)}</span></div>
      <div class="row-meta"><span>${a.kind === "plan" ? `Week ${a.week}, starts ${fmtDay(a.date, { weekday: "short", day: "numeric", month: "short" })}` : fmtDay(a.date, { weekday: "short", day: "numeric", month: "short" })}</span><span>${a.kind === "compass" ? "from Compass" : "from the course plan"}</span></div>
      <div class="row-side"><span class="tag ${dayDiff(S.today, a.date) <= 7 ? "soon" : ""}">${dayDiff(S.today, a.date) <= 0 ? "this week" : relDay(a.date)}</span></div></div>`).join("");
  const exams = (S.readiness || []).map(e => {
    const rest = e.in_scope - e.confirmed - e.studied;
    const seg = (n, bg, tipText) => n ? `<i style="width:${n / e.in_scope * 100}%;background:${bg}" data-tip="${n} ${tipText}"></i>` : "";
    return `<a class="row-link" href="#/subject/${encodeURIComponent(e.subject)}">
      <div class="row-title">${subjPill(e.subject)}<span>${esc(e.name)}</span></div>
      <div class="row-meta"><span>${e.confirmed} of ${plural(e.in_scope, "topic")} confirmed</span><span>${e.studied} studied</span>${e.behind ? `<span class="warn">${e.behind} behind</span>` : ""}</div>
      <div class="row-side"><span class="tag">${plural(e.days, "day")}</span></div>
      <div class="stack-bar pr-bar" role="img" aria-label="${e.confirmed} confirmed, ${e.studied} studied, ${rest} not studied">${seg(e.confirmed, "var(--green)", "confirmed")}${seg(e.studied, "var(--amber)", "studied, not confirmed")}${seg(rest, "var(--surface-3)", "not studied")}</div>
    </a>`;
  }).join("");
  if (!rows && !exams) return `<div class="rows empty-note"><b>No assessments or exams set</b>Add dates in Settings.</div>`;
  return `<div class="rows">${rows}${exams}</div>`;
}

/* Progress page: every topic, in class order */
function renderProgress() {
  setNav("progress", "Progress");
  const s = standingLists();
  const untouched = s.ended.length >= 4 && s.ended.every(t => t.status === "none");
  view.innerHTML = `
    <div class="page-head"><div>
      <h1>Progress</h1>
      <p class="sub">You learn in Gemini; this page keeps score. Tick a topic when you have studied it, then confirm it with an 8-question check (${S.pass_mark}% to pass). Topics whose class weeks are over and are not ticked count as behind.</p>
    </div></div>
    ${untouched ? `<div class="notice">${icon("info")}<div><b>Start here: tick what you already know from class.</b><br>Nothing is ticked yet, so every finished topic shows as behind. Tick the ones you have studied; leave the ones you really haven't.</div></div>` : ""}
    ${standStatsHTML()}
    ${S.subjects.map(sub => {
      const ts = sub.topics.map((t, i) => ({ ...t, subject: sub.name, order: i }));
      const conf = ts.filter(t => t.status === "confirmed").length, stud = ts.filter(t => t.status === "studied").length, beh = ts.filter(t => t.behind).length;
      return `<section class="section"><div class="section-head"><h2>${esc(sub.name)}</h2><p>${conf} confirmed · ${stud} studied · ${beh ? `<span class="warn">${beh} behind</span>` : "0 behind"} · ${ts.length} topics</p></div>
        <div class="rows">${ts.map(t => progressRow(t, { pill: false })).join("")}</div></section>`;
    }).join("")}`;
}

/* Topic page, "Check" tab: the test that confirms a topic */
function paneCheck(t) {
  const pane = $("#pane");
  const intro = () => {
    pane.innerHTML = `
      <div class="panel pad test-setup">
        <h2>${t.status === "confirmed" ? "Confirmed" : "Confirm this topic"}</h2>
        <p class="muted">${t.status === "confirmed" ? `You confirmed ${esc(t.name)} ${relDay(t.confirmed_on)}. You can take another check any time.` : `8 questions over the whole of <b>${esc(t.name)}</b>, written from your course files and checked twice. Score ${S.pass_mark}% or more and the topic is confirmed.`}${t.check_best != null ? ` Best so far: ${t.check_best}%.` : ""}</p>
        <div class="step-actions">
          <button class="btn primary" type="button" id="check-start">${icon("target")}${t.checks ? "Take another check" : "Start the check"}</button>
          <button class="btn" type="button" data-gemini="${t.id}">${icon("sparkles")}Study it in Gemini first</button>
          ${t.status === "confirmed" ? "" : `<button class="btn quiet" type="button" data-mark="${t.id}" data-on="${t.status !== "none" ? 1 : 0}">${t.status !== "none" ? "Untick studied" : "Just tick as studied"}</button>`}
        </div>
        <p class="small muted" style="margin-top:12px">Writing the questions takes about a minute. A score from Gemini's own final test (saved in the Study in Gemini window) also confirms the topic at ${S.pass_mark}%.</p>
      </div>`;
    $("#check-start").onclick = async e => {
      busy(e.currentTarget, true, "Writing questions");
      try { const { questions } = await api("/api/check/test", { topic: t.id }); if (current.topic === t.id) run(questions); }
      catch (err) { toast(err.message); busy(e.currentTarget, false); }
    };
  };
  const run = qs => {
    const picked = new Array(qs.length).fill(null);
    let i = 0;
    const draw = () => {
      const q = qs[i];
      pane.innerHTML = `
        <div class="test-run">
          <div class="test-top"><span>Question ${i + 1} of ${qs.length}</span><span class="muted">${picked.filter(x => x != null).length} answered</span></div>
          <div class="lesson-progress">${qs.map((_, j) => `<i class="${picked[j] != null ? "on" : ""} ${j === i ? "cur" : ""}"></i>`).join("")}</div>
          <article class="question">
            <div class="md test-q">${md(q.question)}</div>
            ${figureHTML(q.figure)}
            <div class="options" role="radiogroup" aria-label="Answer options">${q.options.map((o, j) =>
              `<button type="button" role="radio" aria-checked="${picked[i] === j}" class="option ${picked[i] === j ? "picked" : ""}" data-opt="${j}"><span class="opt-key">${"ABCDE"[j]}</span><span class="md">${md(o)}</span></button>`).join("")}</div>
          </article>
          <div class="step-actions">
            <button class="btn" type="button" id="back" ${i === 0 ? "disabled" : ""}>${icon("chevronLeft")}Back</button>
            ${i < qs.length - 1 ? `<button class="btn primary" type="button" id="fwd">Next${icon("chevronRight")}</button>` : `<button class="btn primary" type="button" id="submit">Submit</button>`}
            <span class="muted small" style="margin-left:auto">Keys 1 to ${q.options.length} choose, Enter goes on.</span>
          </div>
        </div>`;
    };
    const submit = async () => {
      const left = picked.filter(x => x == null).length;
      if (left && !confirm(`${plural(left, "question")} not answered. Unanswered questions count as wrong. Submit anyway?`)) return;
      const correct = qs.filter((q, j) => picked[j] === q.answer_index).length;
      let res;
      try { res = await api("/api/check/submit", { topic: t.id, total: qs.length, correct }); } catch (e) { toast(e.message); return; }
      current.keys = null; pane.onclick = null;
      results(qs, picked, res);
      refresh();
    };
    pane.onclick = e => {
      const o = e.target.closest(".option"); if (o) { picked[i] = +o.dataset.opt; draw(); return; }
      if (e.target.closest("#back")) { i--; draw(); }
      if (e.target.closest("#fwd")) { i++; draw(); }
      if (e.target.closest("#submit")) submit();
    };
    current.keys = e => {
      if (e.target.matches("input, textarea, select")) return;
      const n = parseInt(e.key, 10);
      if (n >= 1 && n <= qs[i].options.length) { picked[i] = n - 1; draw(); }
      else if (e.key === "Enter" && picked[i] != null) { e.preventDefault(); if (i < qs.length - 1) { i++; draw(); } else submit(); }
    };
    draw();
  };
  const results = (qs, picked, res) => {
    const wrong = qs.map((q, j) => ({ q, j })).filter(({ q, j }) => picked[j] !== q.answer_index);
    pane.innerHTML = `
      <section class="result-hero ${res.passed ? "good" : "bad"}">
        <div class="big">${res.pct}%</div>
        <div><h2>${res.passed ? (res.newly_confirmed ? "Confirmed. You know this topic." : "Passed again.") : `Not yet. You need ${res.pass_mark}%.`}</h2>
          <p>${qs.length - wrong.length} of ${qs.length} correct. ${res.passed ? "It now counts toward your exam readiness." : "Go through the mistakes below, study those parts in Gemini, then try again."}</p>
          <div class="step-actions">
            <button class="btn ${res.passed ? "" : "primary"}" type="button" id="again">${res.passed ? "Take another check" : "Try again"}</button>
            ${wrong.length ? `<button class="btn" type="button" id="to-cards">${icon("cards")}Make ${plural(wrong.length, "flashcard")} from mistakes</button>` : ""}
            ${res.passed ? `<a class="btn" href="#/progress">Back to Progress</a>` : `<button class="btn" type="button" data-gemini="${t.id}">${icon("sparkles")}Study in Gemini</button>`}
          </div></div>
      </section>
      ${wrong.length ? `<section class="section"><div class="section-head"><h2>Your mistakes</h2></div>${wrong.map(({ q, j }) => `
        <article class="question" style="margin-bottom:12px">
          <div class="md test-q">${md(q.question)}</div>${figureHTML(q.figure)}
          <p class="small" style="margin-top:8px"><span class="warn">You chose:</span> <span class="md">${picked[j] == null ? "nothing" : md(q.options[picked[j]])}</span></p>
          <p class="small"><span class="good">Correct:</span> <span class="md">${md(q.options[q.answer_index])}</span></p>
          <div class="md small muted" style="margin-top:6px">${md(q.explanation || "")}</div>
        </article>`).join("")}</section>` : ""}`;
    $("#again").onclick = () => { t = topicById(t.id) || t; intro(); $("#check-start").click(); };
    const tc = $("#to-cards");
    if (tc) tc.onclick = async () => {
      try {
        const r = await api("/api/learn/mistakes", { topic: t.id, items: wrong.map(({ q }) => ({ question: q.question, answer: q.options[q.answer_index], explanation: q.explanation })) });
        toast(`Added ${plural(r.added, "flashcard")}`); tc.disabled = true; refresh();
      } catch (err) { toast(err.message); }
    };
  };
  intro();
}

/* Topic page, "Review" tab: one detailed sheet to recall the whole topic, plus your own notes */
async function paneReview(t) {
  const pane = $("#pane");
  const draw = r => {
    if (current.topic !== t.id) return;
    pane.innerHTML = `
      <div class="review-head">
        <p class="muted small">${r.md ? `Written by AI from your ${plural(t.files, "course file")} ${relDay(r.made)} and checked once against them. If something disagrees with your teacher's notes, trust the notes.` : ""}
          ${r.stale ? ` <b class="warn">New files arrived since then.</b>` : ""}</p>
        <div class="step-actions" style="margin:0">
          ${r.md ? `<button class="btn sm" type="button" id="rv-refresh">${icon("refresh")}${r.stale ? "Update with the new files" : "Write again"}</button>` : ""}
          <button class="btn sm" type="button" data-gemini="${t.id}">${icon("sparkles")}Study in Gemini</button>
          <a class="btn sm" href="#/topic/${t.id}/check">${icon("target")}Check</a>
        </div>
      </div>
      ${r.md ? `<article class="panel pad md review-sheet">${md(r.md)}</article>` : `<div class="rows empty-note"><b>No review notes yet</b>${S.has_key ? "They couldn't be written just now. Try again." : "Connect Gemini in Settings first."}<div style="margin-top:10px"><button class="btn sm primary" type="button" id="rv-refresh">Write the review notes</button></div></div>`}
      <section class="section">
        <div class="section-head"><h2>Your own notes</h2><p>Paste the “Notes for Study Desk” block Gemini gives at the end of a session. They show here and in Telegram.</p></div>
        <textarea id="rv-mine" rows="${Math.min(14, Math.max(5, (r.mine || "").split("\n").length + 1))}" placeholder="What you learned, your mistakes, things to remember…">${esc(r.mine || "")}</textarea>
        <div class="step-actions"><button class="btn" type="button" id="rv-save">Save my notes</button><span class="muted small" id="rv-saved"></span></div>
      </section>`;
    const rf = $("#rv-refresh");
    if (rf) rf.onclick = () => load(true);
    $("#rv-save").onclick = async () => {
      try { await api("/api/review/mine", { topic: t.id, text: $("#rv-mine").value }); $("#rv-saved").textContent = "Saved"; }
      catch (err) { toast(err.message); }
    };
  };
  const load = async refresh => {
    pane.innerHTML = `<div class="rows empty-note"><span class="spinner" aria-hidden="true"></span><b style="margin-top:10px">${refresh ? "Rewriting" : "Opening"} your review notes for ${esc(t.name)}</b>The first time takes about a minute: the AI reads your files, writes the notes and checks them. After that they open instantly.</div>`;
    try { draw(await api("/api/review", { topic: t.id, refresh: !!refresh })); }
    catch (err) { toast(err.message); draw({ md: null, mine: "" }); }
  };
  load(false);
}

/* Today: tasks from teachers (also added and ticked off in Telegram) */
const WHEN_EN = w => w.replace("ПРОСРОЧЕНО, было", "overdue, was").replace("без срока", "no date").replace("к паре", "before the class").replace("до ", "by ")
  .replace("сегодня", "today").replace("завтра", "tomorrow").replace(/\b(пн|вт|ср|чт|пт|сб|вс)\b/, d => ({ пн: "Mon", вт: "Tue", ср: "Wed", чт: "Thu", пт: "Fri", сб: "Sat", вс: "Sun" }[d]));
function tasksHTML() {
  const tasks = S.tasks || [], open = tasks.filter(t => !t.done);
  return `<section class="section" aria-labelledby="tasks-h">
    <div class="section-head"><h2 id="tasks-h">Tasks${open.length ? ` <span class="muted">${open.length}</span>` : ""}</h2><p>Small things from teachers. Add them here or just write them to the agent in Telegram; you get a reminder the evening before the class.</p></div>
    <form class="task-add" id="task-form"><label class="sr" for="task-text">New task</label>
      <input type="text" id="task-text" placeholder="e.g. Physics: finish Hwk10 for the next lesson" autocomplete="off">
      <button class="btn" type="submit">Add</button></form>
    ${tasks.length ? `<div class="rows">${tasks.map(t => `
      <div class="pr-row task ${t.done ? "done" : ""} ${t.overdue ? "late" : ""}">
        <button class="pr-tick ${t.done ? "on" : ""}" type="button" data-task="${t.id}" data-act="${t.done ? "undo" : "done"}" aria-pressed="${!!t.done}" aria-label="${t.done ? "Done" : "Mark as done"}: ${esc(t.text)}">${icon("check")}</button>
        <div class="pr-main"><div class="row-title">${t.subject ? subjPill(t.subject) : ""}<span>${esc(t.text)}</span></div>
          <div class="row-meta"><span class="${t.overdue ? "warn" : ""}">${t.done ? "done" : esc(WHEN_EN(t.when))}</span><span>${t.source === "telegram" ? "from Telegram" : t.source === "compass" ? "from Compass" : "added here"}</span></div></div>
        <div class="pr-side"><button class="icon-btn" type="button" data-task="${t.id}" data-act="delete" aria-label="Delete task: ${esc(t.text)}" data-tip="Delete">${icon("x")}</button></div>
      </div>`).join("")}</div>` : `<div class="rows empty-note"><b>No tasks</b>Nothing to remember right now.</div>`}
  </section>`;
}
document.addEventListener("submit", async e => {
  if (e.target.id !== "task-form") return;
  e.preventDefault();
  const input = $("#task-text"), text = input.value.trim();
  if (!text) return input.focus();
  const btn = e.target.querySelector("button");
  busy(btn, true, "Adding");
  try { await api("/api/tasks", { action: "add", text }); route.keepScroll = scrollY; await route(); }
  catch (err) { toast(err.message); busy(btn, false); }
});
document.addEventListener("click", async e => {
  const b = e.target.closest("[data-task]");
  if (!b) return;
  try { await api("/api/tasks", { action: b.dataset.act, id: b.dataset.task }); route.keepScroll = scrollY; route(); }
  catch (err) { toast(err.message); }
});
