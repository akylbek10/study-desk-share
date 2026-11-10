"use strict";
/* Learn mode: subtopic path, guided lesson, notes, slides, mini test, and an "ask anytime" chat.
   Uses helpers from app.js ($, api, md, esc, icon, toast, busy, store, plural, fmtDay, relDay, topicById, ...). */

const PASS = 80;
const DIFF = [["easy", "Easy"], ["medium", "Medium"], ["hard", "Hard"], ["exam", "Exam style"]];
const LS = { tid: null, sid: null, outline: [], subs: {}, focus: "", chat: [] };

/* ---------- small pieces used elsewhere ---------- */
function learnCell(t) {
  const l = t.learn;
  if (!l?.built) return `<span class="faint">Not started</span>`;
  return `<div class="mini-progress" title="${l.passed} of ${l.subs} subtopics passed"><span class="mp-track"><i style="width:${l.subs ? l.passed / l.subs * 100 : 0}%"></i></span><span>${l.passed}/${l.subs}</span></div>`;
}
const statusOf = st => {
  if (st?.passed) return { cls: "good", label: `Passed${st.best ? `, ${st.best}%` : ""}` };
  if (st?.tests?.length) return { cls: "bad", label: `Best ${st.best}%, need ${PASS}%` };
  if (st?.step || st?.lesson_done) return { cls: "due", label: st.lesson_done ? "Lesson done, test next" : "In progress" };
  return { cls: "", label: "Not started" };
};
const loadingHTML = (title, text) => `<div class="gen-wait" role="status"><span class="spinner" aria-hidden="true"></span><div><b>${title}</b><p>${text}</p></div></div>`;

/* ---------- topic page: the Learn tab ---------- */
async function paneLearn(t) {
  const pane = $("#pane");
  if (!S.has_key) { pane.innerHTML = `<div class="rows empty"><h3>Connect Gemini to start learning</h3><p>Learn mode builds lessons, notes, slides and tests from your course files with Gemini.</p><a class="btn primary" href="#/settings">Open settings</a></div>`; return; }
  pane.innerHTML = loadingHTML("Building your lesson plan", `Gemini is reading ${t.files ? plural(t.files, "course file") : "the syllabus"} for ${esc(t.name)} and splitting it into short lessons. This takes about 20 seconds the first time.`);
  let data;
  try { data = await api("/api/learn/outline", { topic: t.id }); }
  catch (e) { pane.innerHTML = `<div class="rows empty"><h3>Couldn't build the lesson plan</h3><p>${esc(e.message)}</p><button class="btn" type="button" id="retry">Try again</button></div>`; $("#retry").onclick = () => paneLearn(t); return; }
  if (current.topic !== t.id) return;
  const { outline, subs, summary } = data;
  const nextId = summary.next?.id;
  pane.innerHTML = `
    <div class="path-head">
      <div><h2>${summary.complete ? "Topic complete" : `${summary.passed} of ${plural(summary.subs, "subtopic")} passed`}</h2>
        <p class="muted small">Each subtopic is a short lesson. Pass its mini test with ${PASS}% or more to tick it off. Everything is open, so you can learn in any order.</p></div>
      <div class="path-progress" aria-hidden="true"><i style="width:${summary.subs ? summary.passed / summary.subs * 100 : 0}%"></i></div>
    </div>
    <ol class="path">${outline.map((s, i) => {
      const st = subs[s.id] || {}, status = statusOf(st), passed = !!st.passed;
      const cta = passed ? "Review" : (st.step || st.tests?.length ? "Continue" : "Start");
      return `<li class="${passed ? "done" : ""} ${s.id === nextId ? "next" : ""}">
        <span class="path-num" aria-hidden="true">${passed ? icon("check") : i + 1}</span>
        <div class="path-body">
          <a class="path-title" href="#/learn/${t.id}/${s.id}">${esc(s.title)}</a>
          <p>${esc(s.summary)}</p>
          <div class="row-meta"><span>About ${s.minutes || 20} min</span><span>${plural(s.objectives.length, "objective")}</span>${st.tests?.length ? `<span>${plural(st.tests.length, "test")} taken</span>` : ""}<span class="tag ${status.cls}">${status.label}</span></div>
        </div>
        <a class="btn ${s.id === nextId ? "primary" : ""} sm" href="#/learn/${t.id}/${s.id}">${cta}</a>
      </li>`;
    }).join("")}</ol>
    <div style="margin-top:16px"><button class="btn quiet sm" id="rebuild" type="button">${icon("refresh")}Rebuild the plan</button></div>`;
  $("#rebuild").onclick = async e => {
    if (!confirm("Rebuild the lesson plan from your files? Subtopics that get new names lose their saved lessons and test results.")) return;
    busy(e.currentTarget, true, "Rebuilding");
    try { await api("/api/learn/outline", { topic: t.id, refresh: true }); await refresh(); renderTopic(t.id, "learn"); }
    catch (err) { toast(err.message); busy(e.currentTarget, false); }
  };
}

/* ---------- the learning canvas ---------- */
async function renderLearn(tid, sid, tab) {
  const t = topicById(tid);
  if (!t) return renderToday();
  setNav("subject:" + t.subject, t.name);
  tab = ["lesson", "notes", "slides", "test", "mine"].includes(tab) ? tab : "lesson";
  view.innerHTML = loadingHTML("Opening lesson", "Loading the plan for this topic.");
  let data;
  try { data = await api("/api/learn/outline", { topic: tid }); }
  catch (e) { view.innerHTML = `<div class="rows empty" style="margin-top:30px"><h3>Couldn't open this lesson</h3><p>${esc(e.message)}</p><a class="btn" href="#/topic/${tid}/learn">Back to ${esc(t.name)}</a></div>`; return; }
  const idx = data.outline.findIndex(s => s.id === sid);
  if (idx < 0) { location.hash = `#/topic/${tid}/learn`; return; }
  const sub = data.outline[idx], st = data.subs[sid] || {};
  if (LS.tid !== tid || LS.sid !== sid) LS.chat = store.get(`learnchat:${tid}:${sid}`, []);
  Object.assign(LS, { tid, sid, outline: data.outline, subs: data.subs, sub, focus: "" });
  const prev = data.outline[idx - 1], next = data.outline[idx + 1], status = statusOf(st);
  setNav("subject:" + t.subject, sub.title);

  view.innerHTML = `
    <div class="learn">
      <div class="learn-main">
        <a class="crumb" href="#/topic/${tid}/learn">${icon("chevronLeft")}${esc(t.name)}</a>
        <div class="learn-head">
          <div>
            <p class="learn-kicker">Part ${idx + 1} of ${data.outline.length}<span class="tag ${status.cls}">${status.label}</span></p>
            <h1>${esc(sub.title)}</h1>
            <p class="sub muted">${esc(sub.summary)}</p>
          </div>
          <button class="btn gem-btn" type="button" data-gemini="${tid}" data-gemini-sub="${sid}">${icon("sparkles")}Study in Gemini</button>
        </div>
        <details class="objectives"><summary>What you'll be able to do</summary><ul>${sub.objectives.map(o => `<li>${esc(o)}</li>`).join("")}</ul></details>
        <nav class="tabs" aria-label="Lesson sections">
          ${[["lesson", "Guided lesson", "layers"], ["notes", "Notes", "file"], ["slides", "Slides", "cards"], ["test", "Mini test", "target"], ["mine", "My notes", "pen"]].map(([k, l, ic]) =>
            `<a href="#/learn/${tid}/${sid}/${k}" ${k === tab ? 'aria-current="page"' : ""}>${icon(ic)}${l}</a>`).join("")}
        </nav>
        <div id="lpane"></div>
        <div class="learn-foot">
          ${prev ? `<a class="btn quiet" href="#/learn/${tid}/${prev.id}">${icon("chevronLeft")}<span>${esc(prev.title)}</span></a>` : "<span></span>"}
          ${next ? `<a class="btn quiet" href="#/learn/${tid}/${next.id}"><span>${esc(next.title)}</span>${icon("chevronRight")}</a>` : `<a class="btn quiet" href="#/topic/${tid}/learn"><span>All subtopics</span>${icon("chevronRight")}</a>`}
        </div>
      </div>
      <aside class="learn-chat" id="lchat" aria-label="Ask about this lesson">
        <div class="lc-head"><div><h2>Ask anytime</h2><p>Stop the lesson and ask about anything on screen</p></div>
          <div class="as-actions"><button class="icon-btn" id="lc-clear" type="button" aria-label="Clear this chat" title="Clear chat">${icon("refresh")}</button><button class="icon-btn lc-close" id="lc-close" type="button" aria-label="Close chat">${icon("x")}</button></div></div>
        <div class="lc-body" id="lc-body"></div>
        <form class="composer" id="lc-form" style="margin:0 14px 14px">
          <label class="icon-btn" title="Send a photo of your working" aria-label="Send a photo of your working">${icon("camera")}<input type="file" accept="image/*" id="lc-img" hidden></label>
          <textarea id="lc-input" rows="1" placeholder="Ask a question" aria-label="Ask about this lesson"></textarea>
          <button class="icon-btn primary" id="lc-send" aria-label="Send">${icon("send")}</button>
        </form>
      </aside>
      <button class="btn primary ask-fab" id="ask-fab" type="button">${icon("chat")}Ask</button>
    </div>`;
  setupLearnChat();
  ({ lesson: paneLesson, notes: paneNotes, slides: paneSlides, test: paneTest, mine: paneMine })[tab](sub, st, next);
}

/* ----- ask anytime ----- */
function setupLearnChat() {
  const body = $("#lc-body"), input = $("#lc-input"), send = $("#lc-send"), chatEl = $("#lchat");
  const key = `learnchat:${LS.tid}:${LS.sid}`;
  const draw = () => {
    body.innerHTML = LS.chat.length ? chatHTML(LS.chat) : `<div class="chat-empty"><p class="muted small">Confused by a step, a slide or a test question? Ask here. It sees what you're looking at.</p>
      <div class="chips" style="flex-direction:column;align-items:stretch;margin-top:12px">${["Explain this in simpler words", "Give me another example", "Why is this step needed?", "How does this come up in the exam?"].map(q => `<button class="chip" type="button">${q}</button>`).join("")}</div></div>`;
    body.scrollTop = body.scrollHeight;
  };
  LS.ask = async (text, image = null) => {
    text = String(text || "").trim(); if (!text && !image) return;
    chatEl.classList.add("open");
    LS.chat.push({ role: "user", text: text || CHECK_PHOTO, image, hadImage: !!image }); input.value = ""; input.style.height = "";
    draw(); body.insertAdjacentHTML("beforeend", thinkingHTML("Thinking it through")); body.scrollTop = body.scrollHeight;
    send.disabled = true;
    try {
      LS.chat.push(modelMsg(await api("/api/learn/ask", { topic: LS.tid, sub: LS.sid, focus: LS.focus,
        messages: LS.chat.map(m => ({ role: m.role, text: m.text, image: m.image })) })));
    } catch (e) { const last = LS.chat.pop(); input.value = last.text; toast(e.message); }
    LS.chat.forEach(m => { delete m.image; });          // photos are sent once, never stored
    send.disabled = false; store.set(key, LS.chat.slice(-40)); draw();
    const lastModel = [...body.querySelectorAll(".msg.model")].pop(); if (lastModel) body.scrollTop = lastModel.offsetTop - 10;
  };
  draw();
  $("#lc-form").onsubmit = e => { e.preventDefault(); LS.ask(input.value); };
  input.onkeydown = e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); LS.ask(input.value); } };
  input.oninput = () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 160) + "px"; };
  body.addEventListener("click", e => {
    const c = e.target.closest(".chip"); if (!c || send.disabled) return;
    if ("photo" in c.dataset) $("#lc-img").click(); else LS.ask(c.textContent);
  });
  $("#lc-img").onchange = async e => {
    try { const img = await readImage(e.target.files[0]); if (img) LS.ask(input.value, img); } catch (err) { toast(err.message); }
    e.target.value = "";
  };
  $("#lc-clear").onclick = () => { LS.chat = []; store.set(key, []); draw(); };
  $("#ask-fab").onclick = () => { chatEl.classList.add("open"); input.focus(); };
  $("#lc-close").onclick = () => chatEl.classList.remove("open");
  // "Ask about this" buttons anywhere in the lesson
  $("#lpane").addEventListener("click", e => { const b = e.target.closest("[data-ask]"); if (b) LS.ask(b.dataset.ask); });
}
const askBtn = q => `<button class="btn quiet sm" type="button" data-ask="${esc(q)}">${icon("chat")}Ask about this</button>`;

/* ----- guided lesson ----- */
async function paneLesson(sub, st, next) {
  const pane = $("#lpane");
  pane.innerHTML = loadingHTML("Preparing your lesson", "Gemini is writing a step-by-step lesson from your materials. About 20 to 30 seconds the first time; after that it opens instantly.");
  let lesson;
  try { ({ lesson } = await api("/api/learn/lesson", { topic: LS.tid, sub: LS.sid })); }
  catch (e) { pane.innerHTML = `<div class="rows empty"><h3>Couldn't prepare the lesson</h3><p>${esc(e.message)}</p><button class="btn" id="retry" type="button">Try again</button></div>`; $("#retry").onclick = () => paneLesson(sub, st, next); return; }
  if (LS.sid !== sub.id) return;
  const steps = lesson.steps;
  let at = Math.min(st.step || 0, steps.length);  // index of the current step; steps.length = finished
  const answers = {};
  const save = (done) => api("/api/learn/save", { topic: LS.tid, sub: LS.sid, step: at, lesson_done: !!done }).catch(() => {});
  const stepHTML = (s, i) => {
    const a = answers[i], isCurrent = i === at, answered = a != null || i < at;
    const correct = s.check.answer_index;
    return `<article class="step ${i < at ? "past" : ""}" id="step-${i}">
      <div class="step-top"><span class="step-n">Step ${i + 1} of ${steps.length}</span><h3>${esc(s.title)}</h3></div>
      <div class="md">${md(s.explain)}</div>
      ${figureHTML(s.figure)}
      <div class="check">
        <p class="check-q"><b>Check:</b> </p><div class="md">${md(s.check.question)}</div>
        ${figureHTML(s.check.figure)}
        <div class="options" role="group" aria-label="Answer options">${s.check.options.map((o, j) => {
          let cls = "";
          if (answered) cls = j === correct ? "right" : (a === j ? "wrong" : "");
          return `<button type="button" class="option ${cls}" data-step="${i}" data-opt="${j}" ${answered ? "disabled" : ""}><span class="opt-key">${"ABCDE"[j]}</span><span class="md">${md(o)}</span></button>`;
        }).join("")}</div>
        ${answered ? `<div class="feedback ${a == null || a === correct ? "good" : "bad"}"><b>${a == null || a === correct ? "Correct." : `Not quite. The answer is ${"ABCDE"[correct]}.`}</b><div class="md">${md(s.check.explanation)}</div></div>` : ""}
        ${isCurrent && answered ? `<div class="step-actions">${a !== correct ? askBtn(`I chose ${"ABCDE"[a]} for "${s.check.question}" but the answer is ${"ABCDE"[correct]}. Why?`) : ""}<button class="btn primary" type="button" id="next-step">${i + 1 < steps.length ? "Next step" : "Finish lesson"}</button></div>` : ""}
      </div>
    </article>`;
  };
  const draw = (scroll) => {
    const shown = steps.slice(0, Math.min(at + 1, steps.length));
    const done = at >= steps.length;
    pane.innerHTML = `
      <div class="lesson-progress" aria-label="Lesson progress">${steps.map((s, i) => `<i class="${i < at ? "on" : i === at ? "cur" : ""}" title="${esc(s.title)}"></i>`).join("")}</div>
      <div class="md lesson-intro">${md(lesson.intro)}</div>
      ${shown.map(stepHTML).join("")}
      ${done ? `<section class="lesson-done">
          <h3>Lesson complete</h3><div class="md">${md(lesson.summary)}</div>
          <div class="step-actions"><a class="btn primary" href="#/learn/${LS.tid}/${LS.sid}/test">${icon("target")}Take the mini test</a>
          <a class="btn" href="#/learn/${LS.tid}/${LS.sid}/notes">Read the notes</a><button class="btn quiet" type="button" id="restart">Start the lesson again</button></div></section>` : ""}
      <div class="regen"><button class="btn quiet sm" type="button" id="regen">${icon("refresh")}Write a different version of this lesson</button></div>`;
    const cur = steps[Math.min(at, steps.length - 1)];
    LS.focus = done ? `Lesson summary: ${lesson.summary}` : `Lesson step "${cur.title}":\n${cur.explain}\nCheck question: ${cur.check.question}`;
    if (scroll) document.getElementById(done ? "regen" : `step-${at}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  pane.onclick = async e => {
    const opt = e.target.closest(".option");
    if (opt && !opt.disabled) { answers[+opt.dataset.step] = +opt.dataset.opt; draw(); document.getElementById(`step-${at}`)?.querySelector(".feedback")?.scrollIntoView({ behavior: "smooth", block: "nearest" }); return; }
    if (e.target.closest("#next-step")) { at++; save(at >= steps.length); draw(true); if (at >= steps.length) refresh(); return; }
    if (e.target.closest("#restart")) { at = 0; Object.keys(answers).forEach(k => delete answers[k]); save(); draw(true); return; }
    const rg = e.target.closest("#regen");
    if (rg) {
      if (!confirm("Write a new version of this lesson? Your place in the lesson resets.")) return;
      busy(rg, true, "Writing");
      try { await api("/api/learn/lesson", { topic: LS.tid, sub: LS.sid, refresh: true }); at = 0; await api("/api/learn/save", { topic: LS.tid, sub: LS.sid, step: 0 }); paneLesson(sub, { ...st, step: 0 }, next); }
      catch (err) { toast(err.message); busy(rg, false); }
    }
  };
  draw(at > 0);
}

/* ----- notes ----- */
async function paneNotes(sub) {
  const pane = $("#lpane");
  pane.innerHTML = loadingHTML("Writing your notes", "Revision notes: definitions, formulas, methods, a worked example, common mistakes and exam tips.");
  let notes;
  try { ({ notes } = await api("/api/learn/notes", { topic: LS.tid, sub: LS.sid })); }
  catch (e) { pane.innerHTML = `<div class="rows empty"><h3>Couldn't write the notes</h3><p>${esc(e.message)}</p></div>`; return; }
  if (LS.sid !== sub.id) return;
  LS.focus = `Revision notes:\n${notes.slice(0, 3500)}`;
  pane.innerHTML = `
    <div class="toolbar"><span class="muted small grow">Made from your course files. Select any part and ask about it on the right.</span>
      <button class="btn sm" type="button" id="dl">${icon("file")}Save as file</button>
      <button class="btn quiet sm" type="button" id="regen">${icon("refresh")}Rewrite</button></div>
    <article class="panel pad md notes-doc">${md(notes)}</article>`;
  $("#dl").onclick = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([`# ${sub.title}\n\n${notes}`], { type: "text/markdown" })); a.download = `${sub.title.replace(/[^\w\- ]+/g, "")}.md`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 5000); };
  $("#regen").onclick = async e => { busy(e.currentTarget, true, "Rewriting"); try { await api("/api/learn/notes", { topic: LS.tid, sub: LS.sid, refresh: true }); paneNotes(sub); } catch (err) { toast(err.message); busy(e.currentTarget, false); } };
  // ask about a selection
  $(".notes-doc").addEventListener("mouseup", () => {
    const sel = String(getSelection()).trim();
    if (sel.length > 3) LS.focus = `The student highlighted this in the notes: "${sel.slice(0, 1500)}"`;
  });
}

/* ----- slides ----- */
async function paneSlides(sub) {
  const pane = $("#lpane");
  pane.innerHTML = loadingHTML("Making the slides", "A short deck: one idea per slide, worked examples, and a summary at the end.");
  let slides;
  try { ({ slides } = await api("/api/learn/slides", { topic: LS.tid, sub: LS.sid })); }
  catch (e) { pane.innerHTML = `<div class="rows empty"><h3>Couldn't make the slides</h3><p>${esc(e.message)}</p></div>`; return; }
  if (LS.sid !== sub.id || !slides.length) return;
  let i = 0, showNotes = false;
  const draw = () => {
    const s = slides[i];
    LS.focus = `Slide ${i + 1} "${s.title}":\n${s.bullets.join("\n")}\nNotes: ${s.notes || ""}`;
    pane.innerHTML = `
      <div class="deck" id="deck" tabindex="0" aria-roledescription="slide deck" aria-label="Slides for ${esc(sub.title)}">
        <div class="slide">
          <span class="slide-n">${i + 1} / ${slides.length}</span>
          <h2 class="slide-title">${md(s.title).replace(/^<p>|<\/p>\s*$/g, "")}</h2>
          <ul class="slide-bullets">${s.bullets.map(b => `<li class="md">${md(b)}</li>`).join("")}</ul>
        </div>
        <div class="deck-bar">
          <button class="icon-btn" id="prev" type="button" aria-label="Previous slide" ${i === 0 ? "disabled" : ""}>${icon("chevronLeft")}</button>
          <div class="deck-dots">${slides.map((_, j) => `<i class="${j === i ? "on" : ""}"></i>`).join("")}</div>
          <button class="icon-btn" id="next" type="button" aria-label="Next slide" ${i === slides.length - 1 ? "disabled" : ""}>${icon("chevronRight")}</button>
          <button class="btn quiet sm" id="notes-t" type="button" aria-pressed="${showNotes}">Speaker notes</button>
          <button class="btn quiet sm" id="fs" type="button">Full screen</button>
        </div>
      </div>
      ${showNotes && s.notes ? `<div class="panel pad md slide-notes">${md(s.notes)}</div>` : ""}
      <div class="step-actions" style="margin-top:14px">${askBtn(`Explain slide ${i + 1} ("${s.title}") in more detail`)}<button class="btn quiet sm" type="button" id="regen">${icon("refresh")}Remake slides</button></div>`;
  };
  const go = d => { const n = Math.max(0, Math.min(slides.length - 1, i + d)); if (n !== i) { i = n; draw(); $("#deck")?.focus({ preventScroll: true }); } };
  pane.onclick = async e => {
    if (e.target.closest("#prev")) go(-1);
    else if (e.target.closest("#next")) go(1);
    else if (e.target.closest("#notes-t")) { showNotes = !showNotes; draw(); }
    else if (e.target.closest("#fs")) { const d = $("#deck"); (document.fullscreenElement ? document.exitFullscreen() : d.requestFullscreen?.())?.catch?.(() => {}); }
    else if (e.target.closest("#regen")) { busy(e.target.closest("#regen"), true, "Remaking"); try { await api("/api/learn/slides", { topic: LS.tid, sub: LS.sid, refresh: true }); paneSlides(sub); } catch (err) { toast(err.message); } }
  };
  current.keys = e => {
    if (e.target.matches("input, textarea, select")) return;
    if (e.key === "ArrowRight" || e.key === "PageDown") { e.preventDefault(); go(1); }
    if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); go(-1); }
  };
  draw();
}

/* ----- mini test ----- */
function paneTest(sub, st, next) {
  const pane = $("#lpane");
  const prefs = store.get("testprefs", { count: 10, difficulty: "medium" });
  const history = (st.tests || []).slice().reverse();
  LS.focus = `The student is about to take a mini test on ${sub.title}.`;
  pane.innerHTML = `
    <div class="panel pad test-setup">
      <h2>Mini test</h2>
      <p class="muted">Score ${PASS}% or more to pass <b>${esc(sub.title)}</b>. You choose how many questions and how hard.</p>
      <div class="setup-grid">
        <div class="field"><span>Questions</span><div class="seg" role="group" aria-label="Number of questions" id="count">${[5, 10, 15, 20].map(n => `<button type="button" data-v="${n}" aria-pressed="${prefs.count === n}">${n}</button>`).join("")}</div></div>
        <div class="field"><span>Difficulty</span><div class="seg" role="group" aria-label="Difficulty" id="diff">${DIFF.map(([v, l]) => `<button type="button" data-v="${v}" aria-pressed="${prefs.difficulty === v}" style="padding:0 12px">${l}</button>`).join("")}</div></div>
      </div>
      <button class="btn primary" type="button" id="start">${icon("target")}Start the test</button>
    </div>
    ${history.length ? `<section class="section"><div class="section-head"><h2>Your attempts</h2><p>Best ${st.best}%${st.passed ? `, passed on ${fmtDay(st.passed)}` : ""}</p></div>
      <div class="rows">${history.map(h => `<div class="row-link" style="cursor:default"><div class="row-title"><span>${h.correct} of ${h.total} correct (${h.pct}%)</span></div>
        <div class="row-meta"><span>${fmtDay(h.date)}</span><span>${esc((DIFF.find(d => d[0] === h.difficulty) || ["", h.difficulty])[1])}</span></div>
        <div class="row-side"><span class="tag ${h.pct >= PASS ? "good" : "bad"}">${h.pct >= PASS ? "Passed" : "Not passed"}</span></div></div>`).join("")}</div></section>` : ""}`;
  const pick = id => $(`#${id}`).addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; $$(`#${id} button`).forEach(x => x.setAttribute("aria-pressed", x === b)); prefs[id === "count" ? "count" : "difficulty"] = id === "count" ? +b.dataset.v : b.dataset.v; store.set("testprefs", prefs); });
  pick("count"); pick("diff");
  $("#start").onclick = async e => {
    busy(e.currentTarget, true, "Writing questions");
    try {
      const { questions } = await api("/api/learn/test", { topic: LS.tid, sub: LS.sid, count: prefs.count, difficulty: prefs.difficulty });
      if (LS.sid === sub.id) runTest(sub, questions, prefs.difficulty, next);
    } catch (err) { toast(err.message); busy(e.currentTarget, false); }
  };
}

function runTest(sub, qs, difficulty, next) {
  const pane = $("#lpane");
  const picked = new Array(qs.length).fill(null);
  let i = 0;
  const draw = () => {
    const q = qs[i];
    LS.focus = `The student is taking a test. Do NOT reveal the answer to this question; give only a hint if asked.\nQuestion ${i + 1}: ${q.question}\nOptions: ${q.options.join(" | ")}`;
    const answered = picked.filter(x => x != null).length;
    pane.innerHTML = `
      <div class="test-run">
        <div class="test-top"><span>Question ${i + 1} of ${qs.length}</span><span class="muted">${answered} answered</span></div>
        <div class="lesson-progress">${qs.map((_, j) => `<i class="${picked[j] != null ? "on" : ""} ${j === i ? "cur" : ""}"></i>`).join("")}</div>
        <article class="question">
          <div class="md test-q">${md(q.question)}</div>
          ${figureHTML(q.figure)}
          <div class="options" role="radiogroup" aria-label="Answer options">${q.options.map((o, j) =>
            `<button type="button" role="radio" aria-checked="${picked[i] === j}" class="option ${picked[i] === j ? "picked" : ""}" data-opt="${j}"><span class="opt-key">${"ABCDE"[j]}</span><span class="md">${md(o)}</span></button>`).join("")}</div>
        </article>
        <div class="step-actions">
          <button class="btn" type="button" id="back" ${i === 0 ? "disabled" : ""}>${icon("chevronLeft")}Back</button>
          ${i < qs.length - 1 ? `<button class="btn primary" type="button" id="fwd">Next${icon("chevronRight")}</button>` : `<button class="btn primary" type="button" id="submit">Submit test</button>`}
          <span class="muted small" style="margin-left:auto">Keys 1 to ${q.options.length} choose, Enter goes on. Stuck? Ask for a hint on the right.</span>
        </div>
      </div>`;
  };
  const submit = async () => {
    const left = picked.filter(x => x == null).length;
    if (left && !confirm(`${plural(left, "question")} not answered. Unanswered questions count as wrong. Submit anyway?`)) return;
    const correct = qs.filter((q, j) => picked[j] === q.answer_index).length;
    let res;
    try { res = await api("/api/learn/submit", { topic: LS.tid, sub: LS.sid, total: qs.length, correct, difficulty }); }
    catch (e) { toast(e.message); return; }
    current.keys = null;
    showResults(sub, qs, picked, res, next);
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
}

function showResults(sub, qs, picked, res, next) {
  const pane = $("#lpane");
  const wrong = qs.map((q, j) => ({ q, j })).filter(({ q, j }) => picked[j] !== q.answer_index);
  LS.focus = `Test result: ${res.pct}%. Wrong questions:\n` + wrong.map(({ q, j }) => `- ${q.question} (chose ${picked[j] == null ? "nothing" : q.options[picked[j]]}, correct ${q.options[q.answer_index]})`).join("\n");
  pane.innerHTML = `
    <section class="result-hero ${res.passed ? "good" : "bad"}">
      <div class="big">${res.pct}%</div>
      <div><h2>${res.passed ? (res.newly_passed ? "Passed. Subtopic ticked off." : "Passed again.") : `Not yet. You need ${res.pass_mark}%.`}</h2>
        <p>${qs.length - wrong.length} of ${qs.length} correct. ${res.passed ? (next ? `Next up: ${esc(next.title)}.` : "That was the last subtopic of this topic.") : "Go through the mistakes below, then try again."} Topic confidence is now ${res.confidence.toFixed(1)}/5.</p>
        <div class="step-actions">
          ${res.passed && next ? `<a class="btn primary" href="#/learn/${LS.tid}/${next.id}">Next subtopic${icon("chevronRight")}</a>` : ""}
          <button class="btn ${res.passed ? "" : "primary"}" type="button" id="retest">${res.passed ? "Take another test" : "Try again"}</button>
          ${wrong.length ? `<button class="btn" type="button" id="to-cards">${icon("cards")}Make ${plural(wrong.length, "flashcard")} from mistakes</button>` : ""}
          ${!res.passed ? `<a class="btn quiet" href="#/learn/${LS.tid}/${LS.sid}/lesson">Back to the lesson</a>` : ""}
        </div></div>
    </section>
    <section class="section"><div class="section-head"><h2>Review</h2><p>${wrong.length ? plural(wrong.length, "mistake") : "No mistakes"}</p></div>
      ${qs.map((q, j) => {
        const ok = picked[j] === q.answer_index;
        return `<article class="question review ${ok ? "ok" : "no"}">
          <div class="q-top"><span class="q-num">Question ${j + 1}</span><span class="tag ${ok ? "good" : "bad"}">${ok ? "Correct" : "Wrong"}</span></div>
          <div class="md">${md(q.question)}</div>
          ${figureHTML(q.figure)}
          <div class="options">${q.options.map((o, k) => `<div class="option ${k === q.answer_index ? "right" : (k === picked[j] ? "wrong" : "")}"><span class="opt-key">${"ABCDE"[k]}</span><span class="md">${md(o)}</span>${k === picked[j] ? `<span class="yours">Your answer</span>` : ""}</div>`).join("")}</div>
          <div class="feedback ${ok ? "good" : "bad"}"><div class="md">${md(q.explanation)}</div></div>
          ${ok ? "" : `<div class="step-actions">${askBtn(`In the test I chose "${picked[j] == null ? "nothing" : q.options[picked[j]]}" for: ${q.question} The correct answer is "${q.options[q.answer_index]}". Explain my mistake.`)}</div>`}
        </article>`;
      }).join("")}
    </section>`;
  $("#retest").onclick = () => renderLearn(LS.tid, LS.sid, "test");
  // keep the header status in step with the new result
  const st = LS.subs[LS.sid] = LS.subs[LS.sid] || {};
  st.tests = [...(st.tests || []), { pct: res.pct }];
  st.best = Math.max(st.best || 0, res.pct);
  if (res.passed) st.passed = st.passed || S.today;
  const s2 = statusOf(st), tag = $(".learn-kicker .tag");
  if (tag) { tag.className = `tag ${s2.cls}`; tag.textContent = s2.label; }
  $("#to-cards")?.addEventListener("click", async e => {
    busy(e.currentTarget, true, "Adding");
    try {
      const r = await api("/api/learn/mistakes", { topic: LS.tid, items: wrong.map(({ q }) => ({ question: q.question, answer: q.options[q.answer_index], explanation: q.explanation })) });
      e.currentTarget.outerHTML = `<span class="tag good">${plural(r.added, "card")} added to Flashcards</span>`; refresh();
    } catch (err) { toast(err.message); busy(e.currentTarget, false); }
  });
  scrollTo({ top: 0, behavior: "smooth" });
}

/* ----- my notes ----- */
function paneMine(sub, st) {
  const pane = $("#lpane");
  pane.innerHTML = `<p class="muted small" style="margin-bottom:10px">Your own notes for this subtopic. They save automatically on this Mac.</p>
    <textarea id="mine" class="mine" placeholder="Write anything: what clicked, what to ask your teacher, formulas to remember" aria-label="My notes">${esc(st.my_notes || "")}</textarea>
    <p class="small faint" id="saved" role="status" style="margin-top:6px"></p>`;
  const ta = $("#mine"); let h;
  LS.focus = `The student's own notes:\n${(st.my_notes || "").slice(0, 2000)}`;
  ta.oninput = () => {
    clearTimeout(h); $("#saved").textContent = "Saving";
    h = setTimeout(async () => { try { await api("/api/learn/save", { topic: LS.tid, sub: LS.sid, my_notes: ta.value }); $("#saved").textContent = "Saved"; LS.focus = `The student's own notes:\n${ta.value.slice(0, 2000)}`; } catch (e) { $("#saved").textContent = e.message; } }, 700);
  };
  ta.focus();
}

/* ---------- profile section ---------- */
function learnProfileHTML(L) {
  if (!L.topics_built) return `<div class="panel empty-note"><b>No lessons yet</b>Open any topic and choose Learn. Each subtopic you pass with ${L.pass_mark}% shows up here.</div>`;
  const subjRows = Object.entries(L.by_subject).filter(([, v]) => v.subs);
  return `<div class="kpis" style="margin-bottom:18px">
      <div class="kpi"><div class="v">${L.subs_passed}<small>/ ${L.subs_total}</small></div><div class="k">${icon("check")}Subtopics passed</div></div>
      <div class="kpi"><div class="v">${L.topics_complete}</div><div class="k">${icon("layers")}Topics fully learned</div></div>
      <div class="kpi"><div class="v">${L.tests}</div><div class="k">${icon("target")}Mini tests taken${L.tests ? `, ${L.tests_passed} passed` : ""}</div></div>
      <div class="kpi"><div class="v">${L.avg_test ?? "–"}${L.avg_test != null ? "<small>%</small>" : ""}</div><div class="k">${icon("pen")}Average test score</div></div>
    </div>
    <div class="cols">
      <div class="panel pad">${subjRows.map(([s, v]) => `<div class="hbar"><span>${subjPill(s)}</span>
        <span class="track"><i style="width:${v.passed / v.subs * 100}%;background:${subjColor(s)}" data-tip="${v.passed} of ${v.subs} subtopics passed"></i></span><span class="val">${v.passed}/${v.subs}</span></div>`).join("")}
        <p class="small faint" style="margin-top:8px">Only topics you've opened in Learn mode are counted.</p></div>
      <div class="panel pad">${L.recent_tests.length ? `<h3 style="margin-bottom:8px">Recent mini tests</h3>${L.recent_tests.slice().reverse().slice(0, 6).map(x => `
        <div class="hbar" style="grid-template-columns:minmax(0,1fr) auto"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${subjPill(x.subject)} ${esc(x.name)} <span class="faint small">${fmtDay(x.date)}</span></span>
        <span class="tag ${x.pct >= L.pass_mark ? "good" : "bad"}">${x.pct}%</span></div>`).join("")}` : `<div class="empty-note"><b>No tests yet</b>Finish a lesson and take its mini test.</div>`}</div>
    </div>`;
}
