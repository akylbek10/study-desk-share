"use strict";
/* Programme: the course plan week by week, where you are now and what comes next.
   Sources: the course plans on IntoStudy (topics.json "plan"), what teachers uploaded this week, and what you paste from Compass. */

const PG = { d: null };

async function programLoad() {
  try { PG.d = await api("/api/program"); } catch { PG.d = null; }
  return PG.d;
}

const topicChip = (id, extra = "") => {
  const t = topicById(id);
  if (!t) return "";
  const state = t.status === "confirmed" ? "done" : t.status === "studied" ? "started" : t.behind ? "late" : "";
  const label = t.status === "confirmed" ? "confirmed" : t.status === "studied" ? "studied, not confirmed" : t.behind ? "behind: not studied" : "not studied yet";
  return `<a class="pg-chip ${state} ${extra}" href="#/topic/${t.id}/check" data-tip="${esc(t.name)}: ${label}">${esc(t.name)}</a>`;
};

function sourceNote(s) {
  if (s.source === "compass") return `From Compass (${relDay(s.compass.date)})`;
  const w = s.weeks.find(w => w.week === PG.d.week);
  if (!w) return "";
  return w.guess ? "Estimated: no plan from teachers yet" : "From the course plan";
}

function freshNote(s) {
  const ids = Object.keys(s.fresh || {});
  if (!ids.length) return "";
  return `New on IntoStudy: ${ids.map(id => `${esc(topicById(id)?.name || id)} (${relDay(s.fresh[id])})`).join(", ")}`;
}

const PACE = { ahead: ["soon", "Teachers are ahead of the plan"], behind: ["bad", "Class is behind the plan"], "on plan": ["good", "On plan"] };

/* Today: "Week 8: what you're on in each subject" */
function thisWeekHTML() {
  const d = PG.d;
  if (!d || !d.week) return "";
  const rows = d.subjects.filter(s => s.now.length || s.next.length).map(s => {
    const pace = s.pace ? PACE[s.pace] : null;
    return `
    <div class="pg-row">
      <div class="pg-subj">${subjPill(s.name)}</div>
      <div class="pg-main">
        <div class="pg-line"><span class="pg-label">Now</span>${s.now.map(id => topicChip(id, "now")).join("") || `<span class="muted small">Nothing planned</span>`}</div>
        ${s.next.length ? `<div class="pg-line"><span class="pg-label">Next</span>${s.next.filter(id => !s.now.includes(id)).map(id => topicChip(id)).join("") || `<span class="muted small">Same topic continues</span>`}</div>` : ""}
        ${s.compass?.summary ? `<p class="small pg-compass">${esc(s.compass.summary)}</p>` : ""}
        ${s.plan_changed && s.source !== "compass" ? `<p class="small pg-compass"><span class="tag soon">Plan changed</span> Your teacher replaced the plan file on IntoStudy ${relDay(s.plan_changed)}. Open it in Course files and check this week.</p>` : ""}
        <div class="row-meta"><span>${sourceNote(s)}</span>${freshNote(s) ? `<span>${freshNote(s)}</span>` : ""}</div>
      </div>
      <div class="row-side">${pace ? `<span class="tag ${pace[0]}">${pace[1]}</span>` : ""}</div>
    </div>`;
  }).join("");
  return `<section class="section" aria-labelledby="wk-h">
    <div class="section-head"><h2 id="wk-h">Week ${d.week} of ${esc(d.term)}</h2><p>w/c ${fmtDay(d.week_start, { day: "numeric", month: "long" })} · <button class="linklike" type="button" data-compass>Add from Compass</button> · <a href="#/program">Whole programme</a></p></div>
    <div class="rows">${rows}</div></section>`;
}

/* Programme page: weeks down, subjects across */
async function renderProgram() {
  setNav("program", "Programme");
  const d = PG.d || await programLoad();
  if (!d || !d.subjects.length) { view.innerHTML = `<div class="rows empty" style="margin-top:40px"><h3>No course plan yet</h3></div>`; return; }
  const subs = d.subjects.filter(s => s.weeks.length);
  const byWeek = (s, n) => s.weeks.find(w => w.week === n);
  const cell = (s, n) => {
    const w = byWeek(s, n);
    if (!w) return `<td class="pg-empty">–</td>`;
    const prev = byWeek(s, n - 1);
    const same = prev && prev.focus === w.focus && JSON.stringify(prev.topics) === JSON.stringify(w.topics);
    const c = n === d.week && s.source === "compass" ? s.compass : null;
    return `<td class="${w.guess ? "pg-guess" : ""}">
      ${same ? `<span class="muted small">continued</span>` : `<div class="pg-chips">${(c ? c.topics : w.topics).map(id => topicChip(id)).join("")}</div>`}
      <div class="pg-focus">${esc(c ? c.summary : w.focus)}${w.guess ? " <em>(estimate)</em>" : ""}</div>
      ${w.assessment ? `<span class="tag soon pg-assess">${esc(w.assessment)}</span>` : ""}
    </td>`;
  };
  const compassBlocks = subs.filter(s => s.compass).map(s => `
    <div class="pg-cbox"><div class="row-title">${subjPill(s.name)}<span>Compass, week ${s.compass ? esc(s.compass.week ?? "") : ""}</span></div>
      ${s.compass.items?.length ? `<ul>${s.compass.items.map(i => `<li>${esc(i)}</li>`).join("")}</ul>` : `<p class="small">${esc(s.compass.summary)}</p>`}</div>`).join("");

  view.innerHTML = `
    <div class="page-head"><div>
      <h1>Programme</h1>
      <p class="sub">Week ${d.week || "–"} of ${esc(d.term)}. Each subject's plan week by week, from your teachers' plans on IntoStudy. Paste this week's programme from Compass and Study Desk follows it instead.</p>
    </div>
    <div class="fc-actions"><button class="btn primary" type="button" data-compass>${icon("copy")}Add from Compass</button></div></div>
    ${compassBlocks ? `<section class="section"><div class="section-head"><h2>From Compass this week</h2></div><div class="pg-cboxes">${compassBlocks}</div></section>` : ""}
    <section class="section">
      <div class="pg-legend small"><span class="pg-chip done">confirmed</span><span class="pg-chip started">studied</span><span class="pg-chip late">behind</span><span class="pg-chip">not studied yet</span></div>
      <div class="pg-scroll"><table class="pg-table">
        <thead><tr><th scope="col">Week</th>${subs.map(s => `<th scope="col">${subjPill(s.name)}</th>`).join("")}</tr></thead>
        <tbody>${d.weeks.map(n => {
          const st = (subs.find(s => byWeek(s, n)) && byWeek(subs.find(s => byWeek(s, n)), n).start) || "";
          return `<tr class="${n === d.week ? "pg-current" : n < (d.week || 0) ? "pg-past" : ""}" ${n === d.week ? `id="pg-now"` : ""}>
            <th scope="row"><b>Week ${n}</b><span>${st ? fmtDay(st) : ""}</span>${n === d.week ? `<span class="tag due">this week</span>` : ""}</th>
            ${subs.map(s => cell(s, n)).join("")}</tr>`;
        }).join("")}</tbody></table></div>
      <p class="small muted" style="margin-top:10px">Plans: ${subs.map(s => `${esc(SHORT[s.name] || s.name)}: ${esc(s.plan_source || "")}`).join(" · ")}.</p>
      ${subs.filter(s => s.plan_note).map(s => `<p class="small muted">${esc(SHORT[s.name] || s.name)}: ${esc(s.plan_note)}</p>`).join("")}
    </section>`;
  setTimeout(() => $("#pg-now", view)?.scrollIntoView({ block: "center" }), 0);   // after route() scrolls to top
}

/* Paste from Compass */
function compassDialog() {
  const dlg = document.createElement("dialog");
  dlg.className = "gem-dialog";
  dlg.innerHTML = `
    <form method="dialog" class="gem-close"><button class="icon-btn" aria-label="Close">${icon("x")}</button></form>
    <h2>Add this week's programme from Compass</h2>
    <p class="muted small">Open the weekly programme your teacher posted in Compass, select all the text and copy it (or take a screenshot). Study Desk reads which topics it covers and uses it for this week instead of the plan.</p>
    <label class="cp-label" for="cp-text">Text from Compass</label>
    <textarea id="cp-text" rows="9" placeholder="Paste here, e.g. Physics week 9: Mon circular motion, Wed momentum…"></textarea>
    <label class="cp-label" for="cp-img">or a screenshot</label>
    <input type="file" id="cp-img" accept="image/*">
    <div class="gem-row" style="margin-top:14px"><button class="btn primary" type="button" data-act="read">${icon("sparkles")}Read it</button></div>`;
  document.body.appendChild(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  $("#cp-text", dlg).addEventListener("paste", e => {          // a pasted screenshot goes to the image
    const f = [...(e.clipboardData?.files || [])].find(f => f.type.startsWith("image/"));
    if (f) { e.preventDefault(); const dt = new DataTransfer(); dt.items.add(f); $("#cp-img", dlg).files = dt.files; toast("Screenshot added"); }
  });
  dlg.addEventListener("click", async e => {
    if (e.target === dlg) return dlg.close();
    const b = e.target.closest("[data-act=read]");
    if (!b) return;
    const text = $("#cp-text", dlg).value.trim();
    let image = null;
    try { image = await readImage($("#cp-img", dlg).files[0]); } catch (err) { return toast(err.message); }
    if (!text && !image) return toast("Paste the text or add a screenshot first");
    busy(b, true, "Reading");
    try {
      const r = await api("/api/compass", { text, image });
      const names = r.topics.map(id => topicById(id)?.name).filter(Boolean).join(", ");
      toast(`${SHORT[r.subject] || r.subject}, week ${r.week}: ${names || r.summary}`);
      dlg.close();
      PG.d = null; route();
    } catch (err) { toast(err.message); busy(b, false); }
  });
  dlg.showModal();
  $("#cp-text", dlg).focus();
}
document.addEventListener("click", e => { if (e.target.closest("[data-compass]")) compassDialog(); });
