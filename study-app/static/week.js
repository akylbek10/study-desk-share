"use strict";
/* A teaching week as one lesson page: for each subject, the week's topics with everything to study them —
   notes and guided learning (made by the AI from the slides and files), slides, worksheets, readings, videos, sources. */

const WK_GROUPS = [["slides", "Slides"], ["worksheet", "Worksheets"], ["reading", "Readings"], ["answers", "Answers"], ["other", "Other files"]];

async function renderWeek(n) {
  const d = await api("/api/week", { week: n ? Number(n) : null });
  setNav("program", `Week ${d.week}`);
  const nav = d.weeks.length ? `<div class="seg" role="group" aria-label="Week">${[d.weeks.filter(w => w < d.week).pop(), d.weeks.find(w => w > d.week)].map((w, i) =>   // neighbours, even across a gap
    w != null ? `<a class="btn sm" href="#/week/${w}">${i ? "Week " + w + " →" : "← Week " + w}</a>` : "").join("")}</div>` : "";
  view.innerHTML = `
    <div class="page-head"><div>
      <a class="crumb" href="#/program">${icon("chevronLeft")}Programme</a>
      <h1>Week ${d.week}${d.week === d.current ? ` <span class="tag due">this week</span>` : ""}</h1>
      <p class="sub">${d.start ? `From ${fmtDay(d.start, { weekday: "long", day: "numeric", month: "long" })}. ` : ""}Everything for this week's lessons in one place: notes and guided learning made from your teachers' slides, worksheets, readings and videos.</p>
    </div>${nav}</div>
    ${d.subjects.filter(s => s.topics.length || s.assessment || d.week >= d.current).map(s => `
      <section class="section">
        <div class="section-head"><h2>${subjPill(s.name)}</h2><p>${esc(s.focus || "")}</p></div>
        ${s.assessment ? `<div class="notice">${icon("info")}<div><b>${esc(s.assessment)}</b></div></div>` : ""}
        ${s.topics.map(t => weekTopicHTML(t)).join("")}
        ${s.topics.length ? "" : `<div class="rows empty-note"><b>Nothing from your teacher for this week yet</b>Topics and files appear here by themselves as soon as they are added on IntoStudy or OneDrive.</div>`}
      </section>`).join("") || `<div class="rows empty-note"><b>Nothing planned for this week</b>It may be a break or outside the course plan.</div>`}`;
}

function weekTopicHTML(t) {
  const videos = t.links.filter(l => l.kind !== "source"), sources = t.links.filter(l => l.kind === "source");
  const groups = WK_GROUPS.filter(([k]) => (t.files[k] || []).length);
  return `<article class="panel pad wk-topic">
    <div class="wk-head"><h3>${t.id ? `<a href="#/topic/${t.id}/check">${esc(t.name)}</a>` : esc(t.name)}</h3>${!t.id ? "" : `
      <div class="step-actions" style="margin:0">
        <a class="btn sm primary" href="#/learn/${t.id}">${icon("layers")}${t.learn?.built ? `Guided learning ${t.learn.passed}/${t.learn.subs}` : "Guided learning"}</a>
        <a class="btn sm" href="#/topic/${t.id}/review">${icon("book")}Notes</a>
        <a class="btn sm" href="#/topic/${t.id}/check">${icon("target")}Check</a>
      </div>`}</div>
    ${groups.length ? `<div class="wk-files">${groups.map(([k, label]) => `<div><h4>${label}</h4><ul>${t.files[k].map(f =>
      `<li><a href="${fileHref(f.path)}">${icon("file")}${esc(f.name)}</a>${f.onedrive ? ` <span class="tag">OneDrive</span>` : ""}</li>`).join("")}</ul></div>`).join("")}</div>` : ""}
    ${!groups.length && !videos.length && !sources.length ? `<p class="small muted">No files for this topic from your teacher yet. They appear here by themselves; Guided learning already works from the syllabus.</p>` : ""}
    ${t.more > 0 && t.id ? `<p class="small muted"><a href="#/topic/${t.id}/files">All ${t.more + Object.values(t.files).flat().length} files for this topic</a></p>` : ""}
    ${videos.length ? `<h4>Videos</h4><div class="vid-grid">${videos.map(l => `<figure class="vid"><div class="vid-frame">${linkPlayer(l)}</div><figcaption><b>${esc(l.title)}</b></figcaption></figure>`).join("")}</div>` : ""}
    ${sources.length ? `<h4>Sources</h4><ul class="wk-src">${sources.map(l => `<li><a href="${esc(l.url)}" target="_blank" rel="noopener">${icon("external")}${esc(l.title)}</a> <span class="muted small">${esc(linkHost(l.url))}</span></li>`).join("")}</ul>` : ""}
  </article>`;
}
