#!/usr/bin/env python3
"""Fetch new and changed course files from man.intostudy.com (D2L) and turn them into text.

    .venv/bin/python sync.py          check every course, download what is new or changed, extract text
    .venv/bin/python sync.py login    open a Chrome window so you can sign in once (Microsoft SSO)

Uses its own Chrome profile in data/browser-profile, so your sign-in is kept between runs; no password is
ever stored by Study Desk. Files keep the same folder layout as browser_download.js (courses/<Course>/NN Module/...).
Progress is written to data/sync_status.json; every added file is logged in data/sync_log.json.
"""
import html
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

APP = Path(__file__).resolve().parent
ROOT = APP.parent
COURSES_DIR, TEXT_DIR = ROOT / "courses", ROOT / "text"
DATA = APP / "data"
PROFILE = DATA / "browser-profile"
STATUS, MANIFEST, LOG = DATA / "sync_status.json", DATA / "sync_manifest.json", DATA / "sync_log.json"

SITE = "https://man.intostudy.com"
API_VER = "1.100"
COURSES_FILE = DATA / "courses.json"   # {org unit id: course name}; found on the first sync, edit to drop a course
SKIP = {"mp4", "mov", "m4v", "avi", "webm"}


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


def clean(s):
    """Same file/folder naming as browser_download.js, so existing files are recognised."""
    s = html.unescape(re.sub(r"<[^>]+>", "", s or ""))
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^[ .]+|[ .]+$", "", s)[:120]
    return s or "untitled"


def walk_toc(toc, cname):
    """List the file topics of a course with their paths, plus web links."""
    files, links, seen = [], [], set()

    def walk(m, path):
        for t in m.get("Topics") or []:
            url = t.get("Url") or ""
            if t.get("TypeIdentifier") != "File":
                if url:
                    links.append(f"- [{t.get('Title')}]({url if url.startswith('http') else SITE + url})  ({path})")
                continue
            ext = url.split("?")[0].rsplit(".", 1)[-1].lower() if "." in url.split("?")[0] else ""
            if ext in SKIP:
                continue
            n = clean(t.get("Title"))
            if ext and not n.lower().endswith("." + ext):
                n += "." + ext
            p, k = f"{path}/{n}", 2
            while p in seen:
                p = re.sub(r"(\.[^./]+)?$", lambda mm: f" ({k})" + (mm.group(0) or ""), f"{path}/{n}", count=1)
                k += 1
            seen.add(p)
            files.append({"id": str(t["TopicId"]), "path": p, "modified": t.get("LastModifiedDate") or "",
                          "title": t.get("Title") or n})
        for i, s in enumerate(m.get("Modules") or [], 1):
            walk(s, f"{path}/{i:02d} {clean(s.get('Title'))}")

    for i, m in enumerate(toc.get("Modules") or [], 1):
        walk(m, f"{clean(cname)}/{i:02d} {clean(m.get('Title'))}")
    return files, links


def file_hash(path):
    import hashlib
    return hashlib.sha1(path.read_bytes()).hexdigest()


class Orphans:
    """Files on disk that no IntoStudy item claims yet (e.g. from the first zip download, before teachers
    renumbered folders). A downloaded file identical to one of them is the same file: keep the old copy."""
    def __init__(self, manifest):
        claimed = {v["path"] for v in manifest.values()}
        self.by_size = {}
        for f in COURSES_DIR.rglob("*"):
            if f.is_file() and not f.name.startswith(("_", ".")):
                rel = str(f.relative_to(COURSES_DIR))
                if rel not in claimed:
                    self.by_size.setdefault(f.stat().st_size, []).append(rel)

    def match(self, data):
        import hashlib
        cands = self.by_size.get(len(data), [])
        if not cands:
            return None
        h = hashlib.sha1(data).hexdigest()
        for rel in cands:
            if file_hash(COURSES_DIR / rel) == h:
                cands.remove(rel)
                return rel
        return None

    def claim(self, rel):
        for lst in self.by_size.values():
            if rel in lst:
                lst.remove(rel)


def browser(p, headless):
    PROFILE.mkdir(parents=True, exist_ok=True)
    opts = dict(user_data_dir=str(PROFILE), headless=headless, accept_downloads=False,
                viewport={"width": 1100, "height": 800})
    return p.chromium.launch_persistent_context(channel="chrome", **opts)   # the Chrome already on this Mac


def pause(ctx, ms):
    try:
        ctx.pages[-1].wait_for_timeout(ms)
    except Exception:
        time.sleep(ms / 1000)


def on_d2l(url):
    return url.startswith(SITE + "/d2l/") and "/d2l/login" not in url and "/auth/" not in url


def signed_in(page):
    """Open IntoStudy; if it shows its login page, go through the student Microsoft sign-in, which completes
    on its own while Microsoft remembers this browser ("Stay signed in")."""
    def settle():
        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
    try:
        page.goto(f"{SITE}/d2l/home", wait_until="domcontentloaded", timeout=60_000)
    except Exception:
        pass
    settle()
    if on_d2l(page.url):
        return True
    try:
        links = [a.get_attribute("href") for a in page.query_selector_all('a[href*="saml/initiate-login"]')
                 if "staff" not in (a.inner_text() or "").lower()]
        if not links:
            return False
        page.goto(links[0], wait_until="domcontentloaded", timeout=60_000)
        deadline = time.time() + 60
        clicked = False
        while time.time() < deadline and not page.url.startswith(SITE):   # wait until Microsoft sends us back
            try:
                if not clicked and "login.microsoftonline.com" in page.url:
                    tile = page.query_selector('[data-test-id="list-item-0"]')   # "Pick an account": the remembered one
                    if tile:
                        tile.click()
                        clicked = True
            except Exception:          # the page navigated while we looked at it
                pass
            page.wait_for_timeout(1500)
        settle()
        if page.url.startswith(SITE):
            page.goto(f"{SITE}/d2l/home", wait_until="domcontentloaded", timeout=60_000)
            settle()
    except Exception as e:
        print("sign-in:", type(e).__name__, str(e)[:200])
        return False
    return on_d2l(page.url)


def login():
    from playwright.sync_api import sync_playwright
    status(state="login_window", step="Sign in to IntoStudy in the Chrome window that opened.", error=None)
    with sync_playwright() as p:
        ctx = browser(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"{SITE}/d2l/home")
        deadline = time.time() + 600
        ok = False
        while time.time() < deadline:
            try:
                pages = ctx.pages
            except Exception:
                break
            if not pages:              # window closed by the user
                break
            if any(on_d2l(pg.url) for pg in pages):
                ok = True
                pause(ctx, 2000)       # let the session cookies settle
                break
            pause(ctx, 1500)
        try:
            ctx.close()
        except Exception:
            pass
    if ok:
        status(state="idle", step="Signed in. Checking for new files…", signed_in=True)
        sync()
    else:
        status(state="login", step="Not signed in. Press “Sign in” and finish signing in within 10 minutes.", signed_in=False)


def courses(req):
    """Courses to fetch: topics.json "intostudy_courses", else data/courses.json, else the courses this
    account is enrolled in on IntoStudy (saved to data/courses.json so you can remove any you don't want)."""
    cfg = load(APP / "topics.json", {}).get("intostudy_courses") or load(COURSES_FILE, {})
    if cfg:
        return {int(k): v for k, v in cfg.items()}
    lp = max((v["LatestVersion"] for v in req.get(f"{SITE}/d2l/api/versions/").json() if v["ProductCode"] == "lp"),
             key=lambda s: tuple(map(int, s.split("."))))
    found, bookmark = {}, ""
    while True:
        r = req.get(f"{SITE}/d2l/api/lp/{lp}/enrollments/myenrollments/?orgUnitTypeId=3&bookmark={bookmark}").json()
        for it in r.get("Items", []):
            if it.get("Access", {}).get("CanAccess", True) and it.get("Access", {}).get("IsActive", True):
                found[it["OrgUnit"]["Id"]] = clean(it["OrgUnit"]["Name"])
        if not r.get("PagingInfo", {}).get("HasMoreItems"):
            break
        bookmark = r["PagingInfo"]["Bookmark"]
    intake = re.compile(r"\s+-\s+[A-Z][a-z]+\.? \d{4}$")   # subject courses are named "Physics - Sept 2026"
    if any(intake.search(n) for n in found.values()):           # skip "Student Services", "Welcome to ..." etc.
        found = {k: intake.sub("", n) for k, n in found.items() if intake.search(n)}
    save(COURSES_FILE, found)
    return found


def sync():
    from playwright.sync_api import sync_playwright
    started = now()
    status(state="running", step="Opening IntoStudy…", started=started, error=None)
    manifest = load(MANIFEST, {})
    first_run = not manifest
    added, updated, failed = [], [], []
    with sync_playwright() as p:
        ctx = browser(p, headless=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not signed_in(page):
            ctx.close()
            status(state="login", step="Sign in to IntoStudy so Study Desk can fetch new files.", signed_in=False, finished=now())
            return
        status(signed_in=True)
        req = ctx.request
        orphans = Orphans(manifest)
        known_same = 0
        for ou, cname in courses(req).items():
            status(step=f"Checking {cname}…")
            r = req.get(f"{SITE}/d2l/api/le/{API_VER}/{ou}/content/toc", timeout=60_000)
            if not r.ok:
                failed.append(f"{cname}: contents ({r.status})")
                continue
            files, links = walk_toc(r.json(), cname)
            lp = COURSES_DIR / clean(cname) / "_links.md"
            lp.parent.mkdir(parents=True, exist_ok=True)
            lp.write_text(f"# {cname}: web links\n\n" + "\n".join(links) + "\n")
            todo = []
            for f in files:
                known = manifest.get(f["id"])
                if known:
                    f["path"] = known["path"]            # keep the original place even if teachers reorder modules
                    if known.get("modified") != f["modified"] or not (COURSES_DIR / f["path"]).exists():
                        todo.append((f, "updated"))
                elif (COURSES_DIR / f["path"]).exists() and f["path"] not in {v["path"] for v in manifest.values()}:
                    manifest[f["id"]] = {"path": f["path"], "modified": f["modified"], "course": cname}
                    orphans.claim(f["path"])
                else:
                    todo.append((f, "added"))
            for i, (f, kind) in enumerate(todo, 1):
                status(step=f"{cname}: downloading {i} of {len(todo)}: {f['title'][:60]}")
                dest = (COURSES_DIR / f["path"]).resolve()
                if not str(dest).startswith(str(COURSES_DIR.resolve())):
                    continue
                try:
                    fr = req.get(f"{SITE}/d2l/api/le/{API_VER}/{ou}/content/topics/{f['id']}/file", timeout=180_000)
                    if not fr.ok:
                        raise RuntimeError(fr.status)
                    data = fr.body()
                    same = orphans.match(data) if kind == "added" else None
                    if same:                     # we already have this exact file under its old folder name
                        manifest[f["id"]] = {"path": same, "modified": f["modified"], "course": cname}
                        known_same += 1
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                except Exception as e:
                    failed.append(f"{f['path']} ({e})")
                    continue
                manifest[f["id"]] = {"path": f["path"], "modified": f["modified"], "course": cname}
                (added if kind == "added" else updated).append({"path": f["path"], "course": cname, "kind": kind})
            save(MANIFEST, manifest)
        ctx.close()

    if added or updated:
        status(step="Turning new files into text…")
        sys.path.insert(0, str(ROOT))
        import extract_text
        extract_text.main()
        log = load(LOG, [])
        log += [{**x, "date": now()} for x in added + updated]
        save(LOG, log[-2000:])
    n_new, n_upd = len(added), len(updated)
    step = ("No new files." if not (n_new or n_upd) else
            f"{n_new} new file{'s' * (n_new != 1)}" + (f", {n_upd} updated" if n_upd else "") + ".")
    if first_run:
        step = f"First check done: {len(manifest)} files known. " + step
    status(state="idle", step=step, finished=now(), last_ok=now(), added=added, updated=updated,
           failed=failed[:50], error=None)


if __name__ == "__main__":
    DATA.mkdir(exist_ok=True)
    try:
        login() if sys.argv[1:] == ["login"] else sync()
    except Exception as e:
        status(state="error", step="The check failed.", error=f"{type(e).__name__}: {e}"[:500], finished=now())
        raise
