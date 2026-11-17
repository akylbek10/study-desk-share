"use strict";
/* Topic page, "Videos & links": the videos and sources teachers put on IntoStudy for this topic, in one place.
   mp4 files play here; YouTube, Loom and Vimeo are embedded; IntoStudy's own videos open in IntoStudy. */

const linkHost = u => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
const linkPlayer = l => l.kind === "video" ? `<video controls preload="none" src="${esc(l.url)}"></video>`
  : l.embed ? `<iframe src="${esc(l.embed)}" loading="lazy" allow="fullscreen; picture-in-picture" allowfullscreen title="${esc(l.title)}"></iframe>`
  : `<a class="vid-open" href="${esc(l.open || l.url)}" target="_blank" rel="noopener">${icon("external")}Watch on IntoStudy</a>`;

async function paneLinks(t) {
  const pane = $("#pane");
  pane.innerHTML = loadingHTML("Collecting videos and links", "");
  let d;
  try { d = await api("/api/topic/links", { topic: t.id }); } catch (e) { pane.innerHTML = `<div class="rows empty-note">${esc(e.message)}</div>`; return; }
  const videos = d.links.filter(l => l.kind !== "source"), sources = d.links.filter(l => l.kind === "source");
  if (!d.links.length) { pane.innerHTML = `<div class="rows empty-note"><b>No videos or links for this topic yet</b>When teachers add them on IntoStudy, they appear here after the next check.</div>`; return; }
  const host = linkHost, player = linkPlayer;
  pane.innerHTML = `
    ${videos.length ? `<section class="section"><div class="section-head"><h2>Videos</h2><p>${videos.length}</p></div>
      <div class="vid-grid">${videos.map(l => `<figure class="vid"><div class="vid-frame">${player(l)}</div>
        <figcaption><b>${esc(l.title)}</b><span class="muted small">${esc(l.folder)}</span></figcaption></figure>`).join("")}</div></section>` : ""}
    ${sources.length ? `<section class="section"><div class="section-head"><h2>Sources and readings</h2><p>${sources.length}</p></div>
      <div class="rows">${sources.map(l => `<a class="row-link" href="${esc(l.url)}" target="_blank" rel="noopener">
        <div class="row-title">${icon("external")}<span>${esc(l.title)}</span></div>
        <div class="row-meta"><span>${esc(host(l.url))}</span><span>${esc(l.folder)}</span></div></a>`).join("")}</div></section>` : ""}`;
}
