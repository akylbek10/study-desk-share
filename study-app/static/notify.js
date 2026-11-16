"use strict";
/* The bell: new files from teachers, Compass posts and homework, timetable changes, updates.
   A red dot while there is something you haven't seen; opening the list marks everything as seen. */

const NT = { open: false };
const NT_ICON = { file: "file", post: "sparkles", news: "info", task: "check", timetable: "today", update: "refresh" };

function bellPaint() {
  const b = $("#bell-btn");
  if (!b || !S) return;
  b.hidden = !!S.hosted;
  b.innerHTML = `${icon("bell")}${S.unread ? `<span class="bell-dot" aria-hidden="true"></span>` : ""}`;
  b.setAttribute("aria-label", S.unread ? `Notifications: ${S.unread} new` : "Notifications");
  b.dataset.tip = S.unread ? `${S.unread} new` : "Notifications";
}

async function bellOpen() {
  const panel = $("#bell-panel");
  NT.open = !NT.open;
  panel.hidden = !NT.open;
  $("#bell-btn").setAttribute("aria-expanded", NT.open);
  if (!NT.open) return;
  panel.innerHTML = `<div class="bell-head"><b>Notifications</b></div><div class="bell-empty">Loading…</div>`;
  let d;
  try { d = await api("/api/notify"); } catch (e) { panel.querySelector(".bell-empty").textContent = e.message; return; }
  panel.innerHTML = `<div class="bell-head"><b>Notifications</b>${d.unread ? `<span class="tag due">${d.unread} new</span>` : ""}</div>` + (d.items.length
    ? `<ul class="bell-list">${d.items.map(n => `<li class="${n.unread ? "unread" : ""}"><a href="${esc(n.link || "#/today")}">
        <span class="bell-ic" ${n.subject ? `style="color:${subjColor(n.subject)}"` : ""}>${icon(NT_ICON[n.kind] || "info")}</span>
        <span class="bell-body"><span class="bell-title">${esc(n.title)}</span>${n.text ? `<span class="bell-text">${esc(n.text)}</span>` : ""}
        <span class="bell-time">${syncAgo(n.ts)}</span></span></a></li>`).join("")}</ul>`
    : `<div class="bell-empty">Nothing yet. New files from your teachers, Compass posts and homework appear here.</div>`);
  if (d.unread) { api("/api/notify/seen", {}).catch(() => {}); S.unread = 0; bellPaint(); }
}

document.addEventListener("click", e => {
  if (e.target.closest("#bell-btn")) { bellOpen(); return; }
  if (NT.open && !e.target.closest("#bell-panel")) { NT.open = false; $("#bell-panel").hidden = true; $("#bell-btn").setAttribute("aria-expanded", false); }
  else if (NT.open && e.target.closest("#bell-panel a")) { NT.open = false; $("#bell-panel").hidden = true; }
});
document.addEventListener("keydown", e => { if (e.key === "Escape" && NT.open) { NT.open = false; $("#bell-panel").hidden = true; $("#bell-btn").focus(); } });
setInterval(async () => {                                    // new things arrive while the page is open
  if (document.visibilityState !== "visible" || !S || NT.open) return;
  try { const d = await api("/api/notify"); if (d.unread !== S.unread) { S.unread = d.unread; bellPaint(); } } catch { /* offline */ }
}, 60000);
