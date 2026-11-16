"use strict";
/* Class recorder: record a lesson here (or upload a phone recording). Whisper turns it into text on this Mac,
   then Gemini writes notes into My notes of each Learn subtopic the class covered, plus what the teacher said
   about tests, exams and deadlines. The recording keeps going while you move around the app. */

const REC = { cid: null, mr: null, stream: null, n: 0, chain: Promise.resolve(), t0: 0, tick: null, lock: null,
              classes: [], busy: new Set(), poll: null, pane: null };
const recBusy = c => ["uploading", "queued", "transcribing", "writing"].includes(c.status);
const mmss = ms => { const s = Math.floor(ms / 1000); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const isoDay = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function recGuessTopic() {
  const m = location.hash.match(/^#\/(?:topic|learn)\/([^/]+)/);
  if (m && topicById(decodeURIComponent(m[1]))) return decodeURIComponent(m[1]);
  const all = allTopics();
  return (all.find(thisWeek) || all.filter(taught).pop() || all[0])?.id;
}

function recPaint() {
  const b = $("#rec-btn");
  if (!b) return;
  const work = REC.classes.find(recBusy);
  b.classList.toggle("live", !!REC.mr);
  if (REC.mr) {
    b.innerHTML = `<span class="rec-dot" aria-hidden="true"></span><span class="rec-time">${mmss(Date.now() - REC.t0)}</span><span class="rec-label">Stop</span>`;
    b.dataset.tip = "Recording this class. Click to stop and make notes";
    b.setAttribute("aria-label", `Stop recording (${mmss(Date.now() - REC.t0)})`);
  } else if (REC.cid) {
    b.innerHTML = `<span class="spinner" aria-hidden="true"></span><span class="rec-label">Saving</span>`;
    b.setAttribute("aria-label", "Saving the recording");
  } else if (work) {
    b.innerHTML = `<span class="spinner" aria-hidden="true"></span><span class="rec-label">${esc(work.step || "Making notes")}</span>`;
    b.dataset.tip = `Making notes from the class of ${fmtDay(work.date)}. You can record another one meanwhile`;
    b.setAttribute("aria-label", `Making notes: ${work.step || "working"}. Click to record another class`);
  } else {
    b.innerHTML = `${icon("mic")}<span class="rec-label">Record class</span>`;
    b.dataset.tip = "Record a lesson: Study Desk writes the notes and catches exam dates";
    b.setAttribute("aria-label", "Record class");
  }
}

function recDialog(topic) {
  const sel = topic || recGuessTopic();
  const dlg = document.createElement("dialog");
  dlg.className = "gem-dialog";
  dlg.innerHTML = `
    <form method="dialog" class="gem-close"><button class="icon-btn" aria-label="Close">${icon("x")}</button></form>
    <h2>Record a class</h2>
    <p class="muted small">Study Desk records through this Mac's microphone. When you stop, Whisper turns the recording into text right on this Mac, then Gemini writes notes into My notes of every subtopic the class covered and lists what the teacher said about tests, exams and deadlines.</p>
    <label class="cp-label" for="rec-topic">Topic of this class</label>
    <select id="rec-topic" style="width:100%">${S.subjects.map(s => `<optgroup label="${esc(s.name)}">${s.topics.map(t =>
      `<option value="${t.id}" ${t.id === sel ? "selected" : ""}>${esc(t.name)}</option>`).join("")}</optgroup>`).join("")}</select>
    <p class="small muted" style="margin-top:12px">Ask your teacher before you record. Keep the laptop open and on charge; the recording is saved every few seconds, so nothing is lost if the browser closes.</p>
    <div class="gem-row" style="margin-top:14px;gap:8px;flex-wrap:wrap">
      <button class="btn primary" type="button" data-act="rec">${icon("mic")}Start recording</button>
      <button class="btn" type="button" data-act="file">${icon("file")}Upload a recording</button>
      <input type="file" id="rec-file" accept="audio/*,.m4a,.mp3,.wav,.webm,.ogg,.aac,.caf" hidden>
    </div>
    <p class="small faint" style="margin-top:10px">Recorded on your phone? Send the voice memo to this Mac (AirDrop) and upload it here.</p>`;
  document.body.appendChild(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  const topicNow = () => $("#rec-topic", dlg).value;
  dlg.addEventListener("click", e => {
    if (e.target === dlg) return dlg.close();
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act === "rec") { dlg.close(); recStart(topicNow()); }
    if (act === "file") $("#rec-file", dlg).click();
  });
  $("#rec-file", dlg).addEventListener("change", e => {
    const f = e.target.files[0];
    if (f) recUpload(f, topicNow(), $("[data-act=file]", dlg), dlg);
  });
  dlg.showModal();
}

async function recStart(topic) {
  if (REC.mr || REC.cid || REC.starting) return;           // a second click while the mic prompt is open
  REC.starting = true;
  try { await recBegin(topic); } finally { REC.starting = false; }
}

async function recBegin(topic) {
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: true, autoGainControl: true } }); }
  catch (e) {
    return toast(e.name === "NotAllowedError" ? "Microphone access is blocked. Allow it for this page, and for your browser in macOS Settings › Privacy & Security › Microphone."
      : `Couldn't open the microphone: ${e.message}`);
  }
  const mime = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"].find(m => MediaRecorder.isTypeSupported(m)) || "";
  let cid;
  try { ({ id: cid } = await api("/api/class/start", { topic, ext: mime.includes("mp4") ? ".m4a" : ".webm" })); }
  catch (e) { stream.getTracks().forEach(t => t.stop()); return toast(e.message); }
  const mr = new MediaRecorder(stream, mime ? { mimeType: mime, audioBitsPerSecond: 64000 } : {});
  Object.assign(REC, { cid, mr, stream, n: 0, chain: Promise.resolve(), t0: Date.now() });
  mr.ondataavailable = e => {
    if (!e.data.size) return;
    const n = REC.n++, blob = e.data;
    REC.chain = REC.chain.then(() => recSend(cid, n, blob));        // pieces go up one after another, in order
  };
  stream.getAudioTracks().forEach(t => t.addEventListener("ended", () => {   // mic unplugged or access taken away
    if (REC.mr !== mr) return;
    toast("The microphone was disconnected, so the recording stopped. Saving what was recorded.");
    recStop();
  }));
  mr.start(5000);
  REC.tick = setInterval(recPaint, 1000);
  recPaint();
  await recWakeLock();
  toast("Recording. Press Stop at the top when the class ends.");
}

async function recWakeLock() {                        // keep the screen on; the browser drops the lock when the tab is hidden
  const mr = REC.mr;
  let lock = null;
  try { lock = await navigator.wakeLock?.request("screen"); } catch { lock = null; }
  if (REC.mr !== mr || !mr) { lock?.release?.().catch(() => {}); return; }   // stopped meanwhile
  REC.lock = lock;
}
document.addEventListener("visibilitychange", () => { if (REC.mr && document.visibilityState === "visible") recWakeLock(); });

async function recSend(cid, n, blob) {
  let err = "";
  for (let i = 0; ; i++) {
    try {
      const r = await fetch(`/api/class/chunk?id=${cid}&n=${n}`, { method: "POST", body: blob });
      if (r.ok) return;
      err = (await r.json().catch(() => ({}))).error || r.statusText;
      if (r.status === 400) break;                      // refused for good: retrying won't help
    } catch (e) { err = e.message; }
    if (i >= 5 && !REC.mr) break;                       // after Stop, only a few tries
    if (i === 5) toast("Study Desk's server isn't answering. Keep recording: the pieces wait here and go up when it's back.");
    await new Promise(res => setTimeout(res, Math.min(1500 * (i + 1), 5000)));   // while recording, keep trying
  }
  toast(`Part of the recording couldn't be saved: ${err}`);
}

async function recStop() {
  const { mr, stream, cid } = REC;
  if (!mr || REC.stopping) return;
  REC.stopping = true;
  await new Promise(res => {                           // the last piece arrives before onstop
    if (mr.state === "inactive") return res();         // already stopped by itself (mic gone)
    mr.onstop = res;
    setTimeout(res, 4000);
    try { mr.stop(); } catch { res(); }
  });
  stream.getTracks().forEach(t => t.stop());
  clearInterval(REC.tick);
  REC.lock?.release?.().catch(() => {});
  Object.assign(REC, { mr: null, stream: null, lock: null });
  recPaint();
  await REC.chain;
  try {
    const r = await api("/api/class/stop", { id: cid });
    toast(r.empty ? "Nothing was recorded" : "Recording saved. Notes will be ready in a few minutes.");
  } catch (e) { toast(e.message); }
  REC.cid = null;
  REC.stopping = false;
  recRefresh();
}

function recUpload(file, topic, btn, dlg) {
  const q = new URLSearchParams({ topic, name: file.name, date: isoDay(new Date(file.lastModified || Date.now())) });
  const xhr = new XMLHttpRequest();
  busy(btn, true, "Uploading");
  xhr.upload.onprogress = e => { if (e.lengthComputable) btn.lastChild.textContent = ` Uploading ${Math.round(e.loaded / e.total * 100)}%`; };
  xhr.onload = () => {
    let d = {};
    try { d = JSON.parse(xhr.responseText); } catch { d = { error: "The server sent an unreadable reply." }; }
    if (xhr.status !== 200) { busy(btn, false); return toast(d.error || "Upload failed"); }
    dlg.close();
    toast("Uploaded. Notes will be ready in a few minutes.");
    recRefresh();
  };
  xhr.onerror = () => { busy(btn, false); toast("Upload failed: is Study Desk running?"); };
  xhr.open("POST", `/api/class/upload?${q}`);
  xhr.send(file);
}

async function recRefresh() {
  try { REC.classes = (await api("/api/class/list", {})).classes; } catch { return; }
  const now = new Set(REC.classes.filter(recBusy).map(c => c.id));
  for (const id of REC.busy) {
    if (now.has(id)) continue;
    const c = REC.classes.find(x => x.id === id);
    if (c) toast(c.status === "done" ? `Class notes ready: ${c.title}. They're in My notes of ${topicById(c.topic)?.name || "the topic"}.` : `Couldn't make notes: ${c.error}`);
    if (REC.pane && c && REC.pane.tid === c.topic) paintClassNotes();
  }
  REC.busy = now;
  for (const c of REC.classes.filter(recBusy)) $$(`[data-cstep="${c.id}"]`).forEach(el => { el.textContent = c.step || "Waiting"; });
  recPaint();
  clearTimeout(REC.poll);
  if (now.size) REC.poll = setTimeout(recRefresh, 3000);
}

/* My notes tab of a Learn subtopic: notes from recorded classes for this subtopic, above your own notes */
async function paintClassNotes(box, tid, sid) {
  if (box) REC.pane = { box, tid, sid };
  ({ box, tid, sid } = REC.pane || {});
  if (!box || !box.isConnected) return;
  let list = [];
  try { list = (await api("/api/class/list", { topic: tid })).classes; } catch (e) { box.innerHTML = ""; return; }
  const subject = topicById(tid)?.subject || "";
  const done = list.filter(c => c.status === "done" && c.sections.some(s => s.sub === sid));
  const other = list.filter(c => c.status !== "done" && c.id !== REC.cid);
  const day = d => fmtDay(d, { weekday: "short", day: "numeric", month: "short" });
  const status = c => c.status === "error"
    ? `<div class="cls-status bad">${icon("info")}<span>Class of ${day(c.date)}: ${esc(c.error)}</span><button class="linklike" data-credo="${c.id}">Try again</button><button class="linklike" data-cdel="${c.id}">Delete</button></div>`
    : c.status === "recording"
      ? `<div class="cls-status">${icon("info")}<span>The recording of ${day(c.date)} stopped before you pressed Stop.</span><button class="linklike" data-cstop="${c.id}">Make notes from it</button><button class="linklike" data-cdel="${c.id}">Delete</button></div>`
      : `<div class="cls-status"><span class="spinner" aria-hidden="true"></span><span>Class of ${day(c.date)}: <span data-cstep="${c.id}">${esc(c.step || "Waiting")}</span></span></div>`;
  const news = c => c.announcements.length ? `<div class="cls-news"><h4>What the teacher said</h4><ul>${c.announcements.map(a => `<li>
      <span class="tag ${a.kind === "exam" ? "bad" : a.kind === "other" ? "" : "due"}">${esc(a.kind)}</span><span>${esc(a.text)}${a.date ? ` <span class="faint">${esc(day(a.date))}</span>` : ""}</span>
      ${a.kind === "exam" && a.date && !S.exams.some(e => e.date === a.date && e.subject === subject) ? `<button class="linklike" data-cexam="${esc(JSON.stringify({ subject, name: a.text.slice(0, 80), date: a.date }))}">Add to exam dates</button>` : ""}</li>`).join("")}</ul></div>` : "";
  const card = c => {
    const sec = c.sections.find(s => s.sub === sid);
    const also = c.sections.filter(s => s.sub !== sid).map(s => LS.outline?.find(o => o.id === s.sub)).filter(Boolean);
    return `<article class="cls-note">
      <header><div><p class="learn-kicker">From class · ${esc(day(c.date))}${c.minutes ? ` · ${Math.max(1, Math.round(c.minutes))} min` : ""}</p><h3>${mdi(c.title || "Class notes")}</h3></div>
        <div class="cls-actions"><button class="linklike" data-ctrans="${c.id}">Transcript</button><button class="linklike" data-cdel="${c.id}">Delete</button></div></header>
      ${news(c)}
      <div class="md">${md(sec.notes)}</div>
      ${also.length ? `<p class="small muted cls-also">This class also covered ${also.map(o => `<a href="#/learn/${tid}/${o.id}/mine">${mdi(o.title)}</a>`).join(", ")}.</p>` : ""}
      <div class="cls-trans" data-trans-for="${c.id}" hidden></div>
    </article>`;
  };
  box.innerHTML = `
    <div class="cls-bar"><button class="btn sm" type="button" data-record="${tid}">${icon("mic")}Record a class</button>
      <span class="muted small">${done.length ? "" : "Record a lesson on this topic and its notes appear here, split by subtopic."}</span></div>
    ${other.map(status).join("")}${done.map(card).join("")}`;
  if (done.length && typeof LS !== "undefined" && LS.sid === sid)
    LS.focus = `${LS.focus || ""}\nNotes from the recorded class:\n${done.map(c => c.sections.find(s => s.sub === sid).notes).join("\n").slice(0, 4000)}`;
}

document.addEventListener("click", async e => {
  const t = e.target.closest("#rec-btn, [data-record], [data-credo], [data-cdel], [data-ctrans], [data-cexam], [data-cstop]");
  if (!t) return;
  if (t.id === "rec-btn") return REC.mr ? recStop() : REC.cid ? null : recDialog();
  if (t.dataset.record) return REC.mr ? toast("Already recording: press Stop at the top first") : recDialog(t.dataset.record);
  try {
    if (t.dataset.credo || t.dataset.cstop) {
      await api(t.dataset.credo ? "/api/class/redo" : "/api/class/stop", { id: t.dataset.credo || t.dataset.cstop });
      await recRefresh(); paintClassNotes();
    } else if (t.dataset.cdel) {
      if (!confirm("Delete this class recording, its transcript and notes? This can't be undone.")) return;
      await api("/api/class/delete", { id: t.dataset.cdel });
      toast("Recording deleted"); await recRefresh(); paintClassNotes();
    } else if (t.dataset.ctrans) {
      const box = $(`[data-trans-for="${t.dataset.ctrans}"]`);
      if (!box.hidden) { box.hidden = true; t.textContent = "Transcript"; return; }
      if (!box.innerHTML) box.innerHTML = esc((await api("/api/class/get", { id: t.dataset.ctrans })).transcript).replace(/\n\n/g, "<br><br>");
      box.hidden = false; t.textContent = "Hide transcript";
    } else if (t.dataset.cexam) {
      const ex = JSON.parse(t.dataset.cexam);
      await api("/api/settings", { exams: [...S.exams, ex] });
      await refresh(); toast(`Added to exam dates: ${fmtDay(ex.date)}`); paintClassNotes();
    }
  } catch (err) { toast(err.message); }
});
addEventListener("beforeunload", e => { if (REC.mr) { e.preventDefault(); e.returnValue = ""; } });
addEventListener("DOMContentLoaded", recRefresh);
