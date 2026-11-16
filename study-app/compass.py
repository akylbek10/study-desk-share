#!/usr/bin/env python3
"""Read your timetable, class news and learning tasks from Compass, in your own signed-in browser session.

    python compass.py login    open a Chrome window: you sign in yourself, then it reads Compass and closes
    python compass.py          read Compass again with the saved sign-in (opens a window only if needed)

Study Desk never sees or stores your password: you sign in on Compass's own page, in a Chrome profile kept in
data/compass-profile (like the IntoStudy one). It only calls the JSON services the Compass website itself uses,
at a normal pace. It does not get past Cloudflare checks or CAPTCHAs: if Compass asks for one, you solve it
in the window, and if that isn't possible the paste/screenshot fallback in Study Desk still works.
Writes data/compass_raw.json (what Compass returned) and data/compass_status.json (progress).
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

import paths

SITE = os.environ.get("COMPASS_URL", "https://intomanchester-uk.compass.education").rstrip("/")
DATA = paths.DATA
PROFILE = DATA / "compass-profile"
STATUS, RAW = DATA / "compass_status.json", DATA / "compass_raw.json"


def now():
    return datetime.now().isoformat(timespec="seconds")


def load(p, default):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def save(p, obj):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    tmp.replace(p)


def status(**kw):
    st = load(STATUS, {})
    st.update(kw)
    save(STATUS, st)


def browser(p, headless):
    PROFILE.mkdir(parents=True, exist_ok=True)
    opts = dict(user_data_dir=str(PROFILE), headless=headless, viewport={"width": 1100, "height": 820},
                chromium_sandbox=True)   # Chrome's normal sandbox (Playwright turns it off by default)
    try:
        return p.chromium.launch_persistent_context(channel="chrome", **opts)   # the Chrome already on this Mac
    except Exception:
        return p.chromium.launch_persistent_context(**opts)                     # no Chrome: Playwright's Chromium


def user_id(page):
    """Compass puts the signed-in user's id in a global on every page once you are signed in."""
    try:
        uid = page.evaluate("() => (window.Compass && Compass.organisationUserId) || 0")
        return int(uid or 0)
    except Exception:
        return 0


def blocked(page):
    """A Cloudflare check or CAPTCHA page (we never try to get past one ourselves)."""
    try:
        t = (page.title() or "").lower()
        return any(w in t for w in ("just a moment", "verifying", "attention required")) or \
            bool(page.query_selector("iframe[src*='challenges.cloudflare.com'], #challenge-form"))
    except Exception:
        return False


CALL = """async ([url, body]) => {
  const r = await fetch(url, {method: "POST", credentials: "same-origin",
    headers: {"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"},
    body: JSON.stringify(body)});
  const text = await r.text();
  let data = null; try { data = JSON.parse(text); } catch (e) {}
  return {status: r.status, data: data, text: data ? null : text.slice(0, 300)};
}"""


def call(page, service, body):
    r = page.evaluate(CALL, [f"{SITE}/Services/{service}", body])
    time.sleep(0.6)                     # a normal pace, like a person clicking around
    d = r.get("data")
    return {"status": r["status"], "data": d.get("d", d) if isinstance(d, dict) else d, "text": r.get("text")}


def read_all(page, uid):
    monday = date.today() - timedelta(days=date.today().weekday())
    out = {"fetched": now(), "site": SITE, "user_id": uid}
    status(state="running", step="Reading your timetable…")
    out["calendar"] = call(page, "Calendar.svc/GetCalendarEventsByUser", {
        "userId": uid, "homePage": True, "activityId": None, "locationId": None, "staffIds": None,
        "startDate": monday.isoformat(), "endDate": (monday + timedelta(days=20)).isoformat(),
        "page": 1, "start": 0, "limit": 300})
    status(step="Reading class news…")
    out["news"] = call(page, "NewsFeed.svc/GetMyNewsFeedPaged", {"start": 0, "limit": 40})
    status(step="Reading learning tasks…")
    out["tasks"] = call(page, "LearningTasks.svc/GetAllLearningTasksByUserId", {
        "userId": uid, "forceTaskId": 0, "page": 1, "start": 0, "limit": 200,
        "sort": '[{"property":"dueDateTimestamp","direction":"DESC"}]'})
    return out


def run(interactive):
    from playwright.sync_api import sync_playwright
    status(state="login_window" if interactive else "running", error=None, started=now(),
           step="Sign in to Compass in the Chrome window that opened." if interactive else "Opening Compass…")
    with sync_playwright() as p:
        ctx = browser(p, headless=not interactive)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(SITE + "/", wait_until="domcontentloaded", timeout=60_000)
        except Exception:
            pass
        uid, deadline = 0, time.time() + (600 if interactive else 25)
        while time.time() < deadline:
            uid = user_id(page)
            if uid:
                break
            try:
                if not ctx.pages:           # the window was closed
                    break
                page = ctx.pages[-1]
                if interactive and blocked(page):
                    status(step="Compass is checking the browser (security check). If it doesn't finish, Compass "
                                "doesn't allow Study Desk's window: use “Paste a post instead”.", checking=True)
                page.wait_for_timeout(1500)
            except Exception:
                break
        if not uid:
            why = "blocked" if blocked(page) else "login"
            try:
                ctx.close()
            except Exception:
                pass
            status(state=why, signed_in=False, finished=now(),
                   step=("Compass showed a security check. Press “Connect Compass” and complete it in the window."
                         if why == "blocked" else "Sign in to Compass (press “Connect Compass”)."))
            return False
        status(signed_in=True)
        try:
            page.wait_for_timeout(1500)
            raw = read_all(page, uid)
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    save(RAW, raw)
    bad = [k for k in ("calendar", "news", "tasks") if raw[k]["status"] != 200]
    status(state="idle", finished=now(), last_ok=now(), signed_in=True, error=None,
           step="Read Compass." + (f" Not available: {', '.join(bad)}." if bad else ""))
    return True


if __name__ == "__main__":
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        ok = run(interactive=sys.argv[1:] == ["login"])
        if not ok and sys.argv[1:] != ["login"] and "--window" in sys.argv:
            run(interactive=True)
    except Exception as e:
        status(state="error", step="Reading Compass failed.", error=f"{type(e).__name__}: {e}"[:500], finished=now())
        raise
