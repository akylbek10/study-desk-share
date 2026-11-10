"use strict";
/* Study in Gemini: build a study pack (prompt + course files), hand over to the Gemini app, log the result back. */

const GEMINI_URL = "https://gemini.google.com/app";

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; }
  catch {
    const ta = document.createElement("textarea"); ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    let ok = false; try { ok = document.execCommand("copy"); } catch { /* ignore */ }
    ta.remove(); return ok;
  }
}

async function studyInGemini(btn) {
  const tid = btn.dataset.gemini, sid = btn.dataset.geminiSub || null;
  const t = topicById(tid);
  busy(btn, true, "Preparing");
  let pack;
  try { pack = await api("/api/handoff", { topic: tid, sub: sid }); }
  catch (e) { toast(e.message); busy(btn, false); return; }
  busy(btn, false);
  const copied = await copyText(pack.prompt);
  const what = pack.sub ? `${esc(pack.sub)}` : esc(t.name);

  const dlg = document.createElement("dialog");
  dlg.className = "gem-dialog";
  dlg.innerHTML = `
    <form method="dialog" class="gem-close"><button class="icon-btn" aria-label="Close">${icon("x")}</button></form>
    <h2>Study ${what} in Gemini</h2>
    <p class="muted small">Gemini gets your course files and a plan: what to teach, your progress and weak spots, your exam style. Use it with your own Gemini 3.1 Pro.</p>
    <ol class="gem-steps">
      <li><b>${copied ? "Prompt copied" : "Copy the prompt"}</b>
        <p>It tells Gemini to teach you step by step from your files, with check questions and a final 5-question test.</p>
        <div class="gem-row"><button class="btn sm" type="button" data-act="copy">${icon("copy")}${copied ? "Copy again" : "Copy prompt"}</button>
        <details><summary>See the prompt</summary><pre class="gem-prompt">${esc(pack.prompt)}</pre></details></div></li>
      <li><b>Your files are in a Finder window</b>
        <p>${pack.files.length} files: the text of all your notes and exam questions for this topic, the formula sheet and the main PDFs.</p>
        <div class="gem-row"><button class="btn sm" type="button" data-act="folder">${icon("external")}Show the folder again</button></div></li>
      <li><b>Open Gemini and start</b>
        <p>Pick <b>3.1 Pro</b> in the model menu. Drag all the files from the folder into the chat, press <kbd>⌘</kbd><kbd>V</kbd> to paste the prompt, and send. Ask anything at any time.</p>
        <div class="gem-row"><a class="btn primary" href="${GEMINI_URL}" target="_blank" rel="noopener">${icon("sparkles")}Open Gemini</a></div></li>
      <li><b>When you finish, save your result here</b>
        <p>Gemini ends with “Score: X/5”. Saving it ticks the topic as studied${pack.sub ? ` (${PASS}%+ passes this subtopic)` : ` and ${PASS}%+ confirms it`}, and adds your study time.</p>
        <div class="gem-result">
          <label>Score <input type="number" id="g-correct" min="0" max="50" inputmode="numeric" placeholder="4"></label>
          <label>out of <input type="number" id="g-total" min="1" max="50" value="5" inputmode="numeric"></label>
          <label>Minutes <input type="number" id="g-min" min="0" max="300" inputmode="numeric" placeholder="45"></label>
          <button class="btn primary" type="button" data-act="save">Save result</button>
        </div>
        <p class="small muted" style="margin-top:8px">No test at the end? <button class="linklike" type="button" data-act="mark">Just tick ${pack.sub ? "the topic" : "it"} as studied</button> and confirm it later with a check in Study Desk.</p></li>
    </ol>`;
  document.body.appendChild(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  dlg.addEventListener("click", async e => {
    if (e.target === dlg) return dlg.close();
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act === "copy") toast(await copyText(pack.prompt) ? "Prompt copied" : "Couldn't copy: open “See the prompt” and copy it by hand");
    if (act === "folder") api("/api/handoff/open", { folder: pack.folder }).catch(err => toast(err.message));
    if (act === "mark") {
      try { await api("/api/topic/mark", { topic: tid, studied: true }); toast("Ticked as studied"); dlg.close(); route(); }
      catch (err) { toast(err.message); }
    }
    if (act === "save") {
      const correct = +$("#g-correct", dlg).value, total = +$("#g-total", dlg).value, minutes = +$("#g-min", dlg).value || 0;
      if (!$("#g-correct", dlg).value || !(total > 0) || correct < 0 || correct > total) return toast("Enter your score, for example 4 out of 5");
      try {
        const r = await api("/api/handoff/result", { topic: tid, sub: sid, correct, total, minutes });
        toast(r.newly_passed ? `Saved: ${r.pct}%. Subtopic passed!` : !sid && r.status === "confirmed" ? `Saved: ${r.pct}%. Topic confirmed.` : `Saved: ${r.pct}%. Ticked as studied.`);
        dlg.close(); route();
      } catch (err) { toast(err.message); }
    }
  });
  dlg.showModal();
  if (!copied) toast("Couldn't copy automatically: press “Copy prompt”");
}

document.addEventListener("click", e => {
  const b = e.target.closest("[data-gemini]");
  if (b && !b.disabled) studyInGemini(b);
});
