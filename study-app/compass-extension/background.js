// Study Desk: Compass helper. Runs in your own Chrome, where you are signed in to Compass and Compass's security
// check sees an ordinary browser. Every 2 hours (or when Study Desk asks) it calls the same JSON services the
// Compass website uses, as you, and hands the answers to Study Desk at http://127.0.0.1. It never reads passwords
// and sends nothing anywhere else.
const SITE = "https://intomanchester-uk.compass.education";
const PORTS = [8890, 8891, 8892, 8893];          // Study Desk's usual address (and test copies)
const EVERY_H = 2;

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function desks() {
  const found = [];
  for (const p of PORTS) {
    try {
      const r = await fetch(`http://127.0.0.1:${p}/api/compass/want`, { cache: "no-store" });
      if (r.ok) found.push({ port: p, ...(await r.json()) });
    } catch (e) { /* nothing on this port */ }
  }
  return found;
}

// Everything is read inside a Compass page (same origin, your cookies), exactly like the site itself does.
// Which services to call comes from Study Desk (/api/compass/want), so it can change without reinstalling this helper.
// Only Compass's own "/Services/<Name>.svc/<Method>" addresses are ever called.
async function readInPage(site, calls) {
  const html = document.documentElement.innerHTML;
  const hints = [...html.matchAll(/["']?(\w*user\w*id\w*)["']?\s*[:=]\s*["']?(\d{2,})/gi)].slice(0, 12).map(m => `${m[1]}=${m[2]}`);
  const uid = Number((window.Compass && (Compass.organisationUserId || Compass.userId)) || 0)
    || Number((html.match(/organisationUserId["']?\s*[:=]\s*["']?(\d+)/i) || [])[1] || 0);
  const diag = { url: location.href.split("?")[0], title: document.title.slice(0, 80),
                 compass: window.Compass ? Object.keys(window.Compass).slice(0, 60) : null, hints };
  if (!uid) return { signin: /login|signin/i.test(location.href) || !window.Compass, diag };
  const call = async (service, body) => {
    if (!/^[A-Za-z]+\.svc\/[A-Za-z]+$/.test(service)) return { status: 0, text: "not a Compass service" };
    const r = await fetch(`${site}/Services/${service}`, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json; charset=utf-8", "Accept": "application/json" }, body: JSON.stringify(body) });
    if (r.status === 401 || (r.redirected && /login|signin/i.test(r.url))) return { expired: true };
    let data = null; const text = await r.text();
    try { data = JSON.parse(text); } catch (e) {}
    await new Promise(ok => setTimeout(ok, 500));            // a normal pace
    return { status: r.status, data: data && typeof data === "object" && "d" in data ? data.d : data, text: data ? null : text.slice(0, 300) };
  };
  const d = new Date(), monday = new Date(d.getFullYear(), d.getMonth(), d.getDate() - ((d.getDay() + 6) % 7));
  const day = x => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
  const vars = { uid, monday: day(monday), end: day(new Date(monday.getTime() + 20 * 864e5)) };
  const fill = (v, extra) => {
    if (typeof v === "string") { const m = v.match(/^\{(\w+)\}$/); if (m) return { ...vars, ...extra }[m[1]]; return v; }
    if (Array.isArray(v)) return v.map(x => fill(x, extra));
    if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, fill(x, extra)]));
    return v;
  };
  const out = { fetched: new Date().toISOString(), site, user_id: uid, via: "extension", diag };
  for (const c of calls) {
    if (c.each_activity) {           // once per class seen in the calendar
      const cal = out[c.from || "calendar"]?.data;
      const rows = Array.isArray(cal) ? cal : (cal && cal.data) || [];
      const ids = [...new Set(rows.filter(e => e.activityId && String(e.activityType) === "1").map(e => e.activityId))].slice(0, c.max || 12);
      out[c.key] = {};
      for (const id of ids) out[c.key][id] = await call(c.service, fill(c.body, { activityId: id }));
    } else out[c.key] = await call(c.service, fill(c.body, {}));
    if (out[c.key]?.expired) return { signin: true, diag };   // a tab left open after the Compass session ended
  }
  return out;
}

async function compassTab(openIfNone) {
  const tabs = await chrome.tabs.query({ url: "https://*.compass.education/*" });
  const ok = tabs.find(t => t.status === "complete" && !/login|Account\/Login|\/Services\//i.test(t.url || ""));   // not a file download
  if (ok || !openIfNone) return { tab: ok, made: false };
  const tab = await chrome.tabs.create({ url: SITE + "/", active: false });     // a background tab, closed afterwards
  for (let i = 0; i < 40; i++) {
    await sleep(1000);
    const t = await chrome.tabs.get(tab.id).catch(() => null);
    if (!t) return { tab: null, made: false };
    if (t.status === "complete") { await sleep(2500); break; }
  }
  return { tab, made: true };
}

const DEFAULT_CALLS = [
  { key: "calendar", service: "Calendar.svc/GetCalendarEventsByUser", body: { userId: "{uid}", homePage: true, activityId: null,
    locationId: null, staffIds: null, startDate: "{monday}", endDate: "{end}", page: 1, start: 0, limit: 300 } },
  { key: "news", service: "NewsFeed.svc/GetMyNewsFeedPaged", body: { start: 0, limit: 40 } },
  { key: "tasks", service: "LearningTasks.svc/GetAllLearningTasksByUserId", body: { userId: "{uid}", forceTaskId: 0, page: 1,
    start: 0, limit: 200, sort: '[{"property":"dueDateTimestamp","direction":"DESC"}]' } },
];

async function readCompass(reason) {
  const targets = await desks();
  if (!targets.length) return;                                // Study Desk isn't running
  const calls = targets.find(d => Array.isArray(d.calls))?.calls || DEFAULT_CALLS;
  const site = (await chrome.storage.local.get("site")).site || SITE;
  const { tab, made } = await compassTab(true);
  let raw = { signin: true };
  try {
    const now = tab && await chrome.tabs.get(tab.id).catch(() => null);
    // sent on to a sign-in page elsewhere (Microsoft): Chrome hides that address from us and won't let us read it
    if (now && (!/^https:\/\/[^/]+\.compass\.education\//.test(now.url || "") || /login/i.test(now.url))) raw = { signin: true };
    else if (tab) {
      const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, world: "MAIN", func: readInPage, args: [site, calls] });
      raw = res?.result || { signin: true };
    }
  } catch (e) {
    raw = { error: String(e).slice(0, 300) };
  } finally {
    if (made && tab) chrome.tabs.remove(tab.id).catch(() => {});
  }
  for (const d of targets) {
    try {
      await fetch(`http://127.0.0.1:${d.port}/api/compass/push`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...raw, reason }) });
    } catch (e) { /* that copy stopped meanwhile */ }
  }
  await chrome.action.setBadgeText({ text: raw.signin ? "!" : raw.error ? "×" : "" });
  await chrome.action.setBadgeBackgroundColor({ color: "#C62F2F" });
  await chrome.action.setTitle({ title: raw.signin ? "Study Desk: sign in to Compass in Chrome, then click here"
    : `Study Desk: read Compass at ${new Date().toLocaleTimeString()}. Click to read again.` });
  await chrome.storage.local.set({ last: Date.now(), signin: !!raw.signin });
}

async function tick() {
  const targets = await desks();
  if (!targets.length) return;
  // Study Desk was updated and brought a newer helper into this folder: load it (Chrome re-reads the files)
  const want = targets.find(d => d.helper_version)?.helper_version;
  if (want && want !== chrome.runtime.getManifest().version) {
    const { reloadedFor } = await chrome.storage.local.get("reloadedFor");
    if (reloadedFor !== want) { await chrome.storage.local.set({ reloadedFor: want }); chrome.runtime.reload(); return; }
  }
  const { last = 0 } = await chrome.storage.local.get("last");
  const due = Date.now() - last > EVERY_H * 3600e3;
  if (due || targets.some(d => d.want)) await readCompass(due ? "schedule" : "asked");
}

chrome.runtime.onInstalled.addListener(() => { chrome.alarms.create("tick", { periodInMinutes: 1 }); readCompass("installed"); });
chrome.runtime.onStartup.addListener(() => chrome.alarms.create("tick", { periodInMinutes: 1 }));
chrome.alarms.onAlarm.addListener(a => { if (a.name === "tick") tick(); });
chrome.action.onClicked.addListener(async () => {
  const { signin } = await chrome.storage.local.get("signin");
  if (signin) { chrome.tabs.create({ url: SITE + "/" }); return; }
  readCompass("clicked");
});
