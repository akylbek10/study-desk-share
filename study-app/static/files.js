"use strict";
/* Files check: is every IntoStudy file downloaded, readable and in a topic? Fix any file's topic by hand. */

function topicOptions(subject, selected) {
  const s = S.subjects.find(x => x.name === subject);
  const opts = (s ? s.topics : []).map(t => `<option value="${t.id}" ${t.id === selected ? "selected" : ""}>${esc(t.name)}</option>`).join("");
  return `<option value="" ${!selected ? "selected" : ""} disabled>Choose a topic…</option>${opts}<option value="general" ${selected === "general" ? "selected" : ""}>Whole subject (general)</option>`;
}
const fileName = p => p.split("/").pop().replace(/\.txt$/, "");
const fileFolder = p => p.split("/").slice(1, -1).map(x => x.replace(/^\d+\s+/, "")).join(" / ");

async function renderFilesCheck() {
  setNav("files", "Files check");
  view.innerHTML = `<div class="page-head"><div><h1>Files check</h1><p class="sub">Loading…</p></div></div>`;
  const d = await api("/api/files/check");
  const tname = id => id === "general" ? "Whole subject" : (topicById(id)?.name || "No topic");
  const check = d.rows.filter(r => r.by === null || (r.by === "ai" && r.confidence !== "high"));
  const ai = d.rows.filter(r => r.by === "ai");
  const mine = d.rows.filter(r => r.by === "you");
  const unreadable = d.rows.filter(r => !r.readable);
  const inTopics = d.text_files - (d.how.none || 0);
  const taughtEmpty = d.topics.filter(t => !t.files).map(t => topicById(t.id)).filter(t => t && taught(t));
  const soonEmpty = d.topics.filter(t => !t.files).map(t => topicById(t.id)).filter(t => t && !taught(t));
  const ok = !d.missing.length && !d.failed.length && !(d.how.none || 0);

  const stat = (n, label, cls = "") => `<div class="fc-stat ${cls}"><b>${n}</b><span>${label}</span></div>`;
  const row = r => `
    <div class="fc-row" data-path="${esc(r.path)}">
      <div class="fc-main">
        <div class="row-title">${subjPill(r.subject)}<a href="/file?p=${encodeURIComponent(r.path)}" target="_blank" rel="noopener">${esc(fileName(r.path))}</a></div>
        <div class="row-meta"><span>${esc(fileFolder(r.path))}</span>${(r.also || []).length ? `<span>also in ${r.also.map(id => esc(topicById(id)?.name || id)).join(", ")}</span>` : ""}${r.by === "ai" ? `<span>sorted by AI${r.confidence ? `, ${r.confidence} confidence` : ""}${r.reason ? `: ${esc(r.reason)}` : ""}</span>` : r.by === "you" ? "<span>set by you</span>" : r.by === null ? "<span>not in any topic</span>" : ""}${r.readable ? "" : "<span class=\"warn\">no readable text (scanned?)</span>"}</div>
      </div>
      <label class="sr" for="sel-${btoa(unescape(encodeURIComponent(r.path))).replace(/[^a-z0-9]/gi, "")}">Topic for ${esc(fileName(r.path))}</label>
      <select class="fc-select" id="sel-${btoa(unescape(encodeURIComponent(r.path))).replace(/[^a-z0-9]/gi, "")}" data-assign="${esc(r.path)}">${topicOptions(r.subject, r.topic)}</select>
    </div>`;
  const group = list => S.subjects.map(s => {
    const mineS = list.filter(r => r.subject === s.name);
    return mineS.length ? `<h3 class="fc-sub">${esc(s.name)} <span class="muted">${mineS.length}</span></h3><div class="rows">${mineS.map(row).join("")}</div>` : "";
  }).join("");

  view.innerHTML = `
    <div class="page-head"><div>
      <h1>Files check</h1>
      <p class="sub">${ok ? "Everything from IntoStudy is downloaded and every file has a place." : "Some files need attention: see below."} Last update from IntoStudy ${d.last_sync ? syncAgo(d.last_sync) : "never"}.</p>
    </div>
    <div class="fc-actions"><button class="btn" type="button" data-sync="sync">${icon("refresh")}Update files</button><button class="btn" type="button" id="resort">${icon("sparkles")}Sort again</button></div></div>

    <div class="fc-stats">
      ${stat(d.portal_files || "–", "files on IntoStudy")}
      ${stat(d.missing.length + d.failed.length ? d.missing.length + d.failed.length : "All", d.missing.length + d.failed.length ? "not downloaded" : "downloaded", d.missing.length + d.failed.length ? "bad" : "good")}
      ${stat(inTopics, `of ${d.text_files} files have a place`, d.how.none ? "bad" : "good")}
      ${stat(check.length, "for you to check", check.length ? "warn" : "good")}
    </div>

    ${d.missing.length || d.failed.length ? `<section class="section"><div class="section-head"><h2>Not downloaded</h2><p>Press Update files to try again</p></div>
      <div class="rows">${[...d.missing, ...d.failed].map(p => `<div class="row-link"><div class="row-title">${esc(p)}</div></div>`).join("")}</div></section>` : ""}

    ${taughtEmpty.length ? `<section class="section"><div class="section-head"><h2>Topics already taught with no files</h2><p>Teachers may not have uploaded them yet, or they are in a folder the app doesn't know</p></div>
      <div class="rows">${taughtEmpty.map(t => `<a class="row-link" href="#/topic/${t.id}/files"><div class="row-title">${subjPill(t.subject)}<span>${esc(t.name)}</span></div><div class="row-meta"><span>class started ${relDay(t.start)}</span></div><div class="row-side"><span class="tag bad">0 files</span></div></a>`).join("")}</div></section>` : ""}

    <section class="section"><div class="section-head"><h2>For you to check</h2><p>Files the sorter wasn't sure about, or couldn't place. Pick the right topic and it's saved.</p></div>
      ${check.length ? group(check) : `<div class="rows empty-note"><b>Nothing to check</b>The sorter was confident about every file.</div>`}</section>

    <section class="section"><div class="section-head"><h2>How files got their topic</h2><p>${d.how.folder || 0} by folder (topics.json) · ${ai.length} by the AI sorter · ${mine.length} by you · ${(d.how.shared || 0)} shared by the whole subject</p></div>
      <details class="fc-details"><summary>Show all ${ai.length + mine.length} files sorted by AI or by you</summary>${group([...mine, ...ai])}</details></section>

    ${unreadable.length || d.no_text.length ? `<section class="section"><div class="section-head"><h2>Files the AI can't read</h2><p>Scans, images and spreadsheets. They are still in your folders; open them yourself.</p></div>
      <div class="rows">${[...unreadable.map(r => r.path.replace(/\.txt$/, "")), ...d.no_text].map(p => `<div class="row-link"><div class="row-title">${subjPill(p.split("/")[0])}<span>${esc(fileName(p))}</span></div><div class="row-meta"><span>${esc(fileFolder(p))}</span></div></div>`).join("")}</div></section>` : ""}

    ${soonEmpty.length ? `<p class="small muted section">Upcoming topics with no files yet (normal before the class): ${soonEmpty.map(t => esc(t.name)).join(", ")}.</p>` : ""}`;

  $$("[data-assign]", view).forEach(sel => sel.addEventListener("change", async () => {
    try {
      await api("/api/files/assign", { path: sel.dataset.assign, topic: sel.value });
      toast(`Moved to ${tname(sel.value)}`);
      const meta = sel.closest(".fc-row").querySelector(".row-meta");
      meta.insertAdjacentHTML("beforeend", "<span class=\"good\">saved</span>");
    } catch (e) { toast(e.message); }
  }));
  $("#resort").addEventListener("click", async e => {
    busy(e.currentTarget, true, "Sorting");
    try { const r = await api("/api/files/sort", {}); toast(r.sorted ? `Sorted ${plural(r.sorted, "file")}` : "Every file already has a place"); renderFilesCheck(); }
    catch (err) { toast(err.message); busy(e.currentTarget, false); }
  });
}
