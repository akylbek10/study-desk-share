"use strict";
/* File viewer (#/file/<path>): course files open inside Study Desk instead of downloading.
   PDFs and images show as they are; Word files are turned into a page you can fill in (your copy is saved on this
   Mac in data/work and can be downloaded as .docx; the original never changes); slides and the rest show their text. */

const fileHref = p => `#/file/${encodeURIComponent(encodeURIComponent(p))}`;   // the router decodes once, then splits on "/"
const FV = { mammoth: null, timer: null, pending: null };

function loadMammoth() {
  return FV.mammoth ||= new Promise((ok, bad) => {
    const s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/mammoth/1.8.0/mammoth.browser.min.js";
    s.onload = () => ok(window.mammoth);
    s.onerror = () => { FV.mammoth = null; bad(new Error("Couldn't load the Word viewer. Check the internet connection.")); };
    document.head.appendChild(s);
  });
}

async function docxHTML(p) {
  const [m, buf] = await Promise.all([loadMammoth(), fetch(`/file?p=${encodeURIComponent(p)}`).then(r => {
    if (!r.ok) throw new Error("File not found");
    return r.arrayBuffer();
  })]);
  return (await m.convertToHtml({ arrayBuffer: buf }, { ignoreEmptyParagraphs: false })).value;   // blank lines = answer space
}

function workFlush() {                        // save what is still waiting (leaving the page, closing the tab)
  if (!FV.pending) return;
  clearTimeout(FV.timer);
  const body = JSON.stringify(FV.pending);
  FV.pending = null;
  navigator.sendBeacon("/api/work/save", new Blob([body], { type: "application/json" }));
}
addEventListener("hashchange", workFlush);
// what you type is blue, so your answers stand out from the teacher's text (in the .docx too)
document.addEventListener("keydown", e => {
  if (!e.target.closest?.(".fv-page.editing") || e.metaKey || e.ctrlKey || (e.key.length !== 1 && e.key !== "Enter")) return;
  document.execCommand("styleWithCSS", false, true);
  document.execCommand("foreColor", false, "#2563eb");
});
addEventListener("pagehide", workFlush);

const slidesHTML = text => {
  const parts = text.split(/^--- (?:slide|page) (\d+) ---$/m);
  if (parts.length < 3) return `<div class="fv-plain">${esc(text.trim()) || "<p class='muted'>No text in this file.</p>"}</div>`;
  let out = "";
  for (let i = 1; i < parts.length; i += 2) {
    const body = parts[i + 1].trim();
    out += `<section class="fv-slide"><h2>Slide ${parts[i]}</h2>${body ? `<div class="fv-plain">${esc(body)}</div>` : `<p class="muted small">No text (picture only)</p>`}</section>`;
  }
  return out;
};

async function renderFile(p) {
  workFlush();
  view.innerHTML = `<div class="rows empty-note" style="margin-top:30px">Opening file</div>`;
  const f = await api("/api/file/view", { path: p });
  const t = f.topic && topicById(f.topic);
  setNav(t ? "subject:" + t.subject : "today", f.name);
  const src = `/file?p=${encodeURIComponent(p)}`;
  const doc = f.ext === "docx";
  const app = f.ext.startsWith("ppt") ? "PowerPoint" : f.ext.startsWith("doc") ? "Word" : f.ext.startsWith("xls") ? "Excel" : "";
  view.innerHTML = `
    <div class="fv-head">
      <div class="fv-title">
        <a class="fv-back small" href="${t ? `#/topic/${t.id}/files` : "#/today"}">${icon("chevronLeft")}${t ? esc(t.name) : "Today"}</a>
        <h1>${esc(f.name.replace(/\.[^.]+$/, ""))}</h1>
        <p class="small muted">${esc(f.folder)}</p>
      </div>
      <div class="fv-actions">
        ${doc ? `<button class="btn primary" type="button" data-fv="fill">${icon("pen")}<span>Fill in</span></button>` : ""}
        ${doc ? `<a class="btn" data-fv-dl href="/work.docx?p=${encodeURIComponent(p)}" ${f.work ? "" : "hidden"}>${icon("file")}My answers (.docx)</a>` : ""}
        ${f.original && app ? `<button class="btn" type="button" data-fv="open">${icon("external")}Open in ${app}</button>` : ""}
        ${f.original ? `<a class="btn quiet" href="${src}" download>Download</a>` : ""}
      </div>
    </div>
    <div class="fv-status small" id="fv-status" hidden></div>
    <div id="fv-body"></div>`;
  const body = $("#fv-body", view), status = $("#fv-status", view);
  const say = html => { status.hidden = !html; status.innerHTML = html || ""; };

  $(".fv-actions", view).addEventListener("click", async e => {
    const b = e.target.closest("[data-fv]");
    if (!b) return;
    if (b.dataset.fv === "open") { try { await api("/api/file/open", { path: p }); toast(`Opening in ${app}`); } catch (err) { toast(err.message); } }
    if (b.dataset.fv === "fill") setEditing(!body.querySelector(".fv-page")?.isContentEditable);
  });

  if (f.ext === "pdf") { body.innerHTML = `<iframe class="fv-frame" src="${src}" title="${esc(f.name)}"></iframe>`; return; }
  if (/^(png|jpe?g|gif|webp)$/.test(f.ext)) { body.innerHTML = `<div class="fv-imgwrap"><img class="fv-img" src="${src}" alt="${esc(f.name)}"></div>`; return; }
  if (!doc) {
    body.innerHTML = `<article class="fv-page fv-text">${slidesHTML(f.text || "")}</article>`;
    if (app) say(`This shows the text of the ${app} file. For the pictures and layout use <b>Open in ${app}</b>.`);
    return;
  }

  // Word: your filled-in copy if there is one, else the original
  let showingMine = !!f.work;
  const savedNote = () => f.work ? `Your filled-in copy, saved ${fmtTime(f.work.updated)}. <button class="linklike" type="button" data-fv-act="toggle">${showingMine ? "Show the original" : "Show my copy"}</button> · <button class="linklike" type="button" data-fv-act="reset">Start again from the original</button>` : "";
  async function draw() {
    body.innerHTML = `<div class="rows empty-note">Opening the Word file</div>`;
    let html;
    try { html = showingMine ? f.work.html : await docxHTML(p); } catch (err) { body.innerHTML = `<div class="rows empty"><h3>Can't show this file</h3><p>${esc(err.message)}</p></div>`; return; }
    body.innerHTML = `<article class="fv-page" spellcheck="true">${DOMPurify.sanitize(html)}</article>`;
    say(savedNote());
  }
  function setEditing(on) {
    const page = body.querySelector(".fv-page");
    if (!page) return;
    if (on && f.work && !showingMine) { showingMine = true; draw().then(() => setEditing(true)); return; }   // edit your copy, not a second one
    page.contentEditable = on ? "true" : "false";
    page.classList.toggle("editing", on);
    const btn = $("[data-fv=fill]", view);
    btn.querySelector("span").textContent = on ? "Done" : "Fill in";
    btn.classList.toggle("primary", !on);
    if (on) { page.focus(); say("Type your answers anywhere on the page: in the gaps, under the questions, in the tables. It saves by itself."); }
    else saveNow().then(() => say(savedNote()));
  }
  async function saveNow() {
    clearTimeout(FV.timer);
    const job = FV.pending; FV.pending = null;
    if (!job) return;
    try {
      const r = await api("/api/work/save", job);
      f.work = { html: job.html, updated: r.work.updated }; showingMine = true;
      $("[data-fv-dl]", view).hidden = false;
      if (body.querySelector(".fv-page")?.isContentEditable) say(`Saved ${fmtTime(r.work.updated)}. Type anywhere; it saves by itself.`);
    } catch (err) { FV.pending ||= job; say(`Not saved: ${esc(err.message)}`); }
  }
  body.addEventListener("input", () => {
    FV.pending = { path: p, html: body.querySelector(".fv-page").innerHTML };
    clearTimeout(FV.timer);
    say("Saving…");
    FV.timer = setTimeout(saveNow, 700);
  });
  status.addEventListener("click", async e => {
    const a = e.target.closest("[data-fv-act]");
    if (!a) return;
    if (a.dataset.fvAct === "toggle") { showingMine = !showingMine; await draw(); }
    if (a.dataset.fvAct === "reset" && confirm("Delete your answers and start again from the teacher's original?")) {
      await api("/api/work/save", { path: p, html: "" });
      f.work = null; showingMine = false; $("[data-fv-dl]", view).hidden = true; await draw();
    }
  });
  await draw();
}

const fmtTime = iso => {
  const d = new Date(iso);
  return (iso.slice(0, 10) === S.today ? "" : fmtDay(iso.slice(0, 10)) + ", ") + d.toTimeString().slice(0, 5);
};
