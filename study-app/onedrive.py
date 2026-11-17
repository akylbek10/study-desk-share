#!/usr/bin/env python3
"""OneDrive / Teams files shared with you (teachers often keep slides and worksheets there, not on IntoStudy).

    python onedrive.py list       find the folders and files teachers shared with you (data/onedrive_found.json)
    python onedrive.py sync       download what is new or changed into courses/<Subject>/OneDrive/… and make text
    python onedrive.py login      open a Chrome window to sign in to Microsoft, if the saved sign-in has expired
    python onedrive.py explore    (development) record which OneDrive services the OneDrive page uses

Uses the same Chrome profile as the IntoStudy sync (data/browser-profile), so the Microsoft sign-in is shared and
no password is ever seen or stored. Writes data/onedrive_explore.json and data/onedrive_status.json.
"""
import json
import os
import sys
import time
from datetime import datetime
from urllib.parse import urlsplit

import paths

DATA = paths.DATA
PROFILE = DATA / "browser-profile"
STATUS, EXPLORE = DATA / "onedrive_status.json", DATA / "onedrive_explore.json"
START = "https://www.microsoft365.com/onedrive"
MY = os.environ.get("ONEDRIVE_URL", "https://iup-my.sharepoint.com/")     # INTO's OneDrive: redirects to your own space
HOSTS = ("sharepoint.com", "graph.microsoft.com", "onedrive.com", "substrate.office.com", "microsoft365.com", "office.com")


def now():
    return datetime.now().isoformat(timespec="seconds")


def status(**kw):
    try:
        st = json.loads(STATUS.read_text())
    except (OSError, ValueError):
        st = {}
    st.update(kw)
    STATUS.write_text(json.dumps(st, ensure_ascii=False, indent=1))


def shape(v, depth=0):
    """The structure of a JSON answer (keys, list lengths, short samples), without dumping everything."""
    if depth > 4:
        return "…"
    if isinstance(v, dict):
        return {k: shape(x, depth + 1) for k, x in list(v.items())[:40]}
    if isinstance(v, list):
        return [len(v)] + ([shape(v[0], depth + 1)] if v else [])
    if isinstance(v, str):
        return v[:120]
    return v


def explore():
    from playwright.sync_api import sync_playwright
    seen = []
    status(state="login_window", step="A Chrome window opened: open “Shared” and your Teams classes there, then close it.",
           error=None, started=now())

    def on_response(r):
        try:
            u = urlsplit(r.url)
            if not any(u.netloc.endswith(h) for h in HOSTS) or r.request.resource_type not in ("fetch", "xhr"):
                return
            try:
                body = r.json()
            except Exception:
                return
            seen.append({"at": now(), "method": r.request.method, "status": r.status, "host": u.netloc,
                         "path": u.path, "query_keys": sorted({p.split("=")[0] for p in u.query.split("&") if p}),
                         "request_body": (r.request.post_data or "")[:1500], "shape": shape(body)})
            EXPLORE.write_text(json.dumps(seen[-400:], ensure_ascii=False, indent=1))
        except Exception:
            pass

    with sync_playwright() as p:
        PROFILE.mkdir(parents=True, exist_ok=True)
        # service_workers="block": the OneDrive page then asks its services directly, where we can see the answers
        opts = dict(user_data_dir=str(PROFILE), headless=False, viewport={"width": 1200, "height": 850}, chromium_sandbox=True,
                    service_workers="block")
        try:
            ctx = p.chromium.launch_persistent_context(channel="chrome", **opts)
        except Exception:
            ctx = p.chromium.launch_persistent_context(**opts)
        ctx.on("response", on_response)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(START, wait_until="domcontentloaded", timeout=60_000)
        except Exception:
            pass
        deadline = time.time() + 1200
        while time.time() < deadline:
            try:
                if not ctx.pages:
                    break
                ctx.pages[-1].wait_for_timeout(2000)
            except Exception:
                break
        try:
            ctx.close()
        except Exception:
            pass
    status(state="idle", step=f"Recorded {len(seen)} OneDrive answers.", finished=now())


FOUND, MAP, MANIFEST = DATA / "onedrive_found.json", DATA / "onedrive_map.json", DATA / "onedrive_manifest.json"
SKIP = {"mp4", "mov", "m4v", "avi", "webm", "mkv", "zip"}
JSONH = {"Accept": "application/json;odata=nometadata"}


def load(p, default):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def save(p, obj):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    tmp.replace(p)


def clean(s):
    import re
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", s or "")
    return re.sub(r"\s+", " ", s).strip(" .")[:120] or "untitled"


def session(p, headless=True):
    PROFILE.mkdir(parents=True, exist_ok=True)
    opts = dict(user_data_dir=str(PROFILE), headless=headless, chromium_sandbox=True, service_workers="block",
                viewport={"width": 1200, "height": 850})
    try:
        return p.chromium.launch_persistent_context(channel="chrome", **opts)
    except Exception:
        return p.chromium.launch_persistent_context(**opts)


def my_space(ctx):
    """Your OneDrive address, or None when the Microsoft sign-in has expired."""
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        page.goto(MY, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(3000)
    except Exception:
        pass
    r = ctx.request.get(MY.rstrip("/") + "/_api/SP.UserProfiles.PeopleManager/GetMyProperties?$select=PersonalUrl", headers=JSONH)
    if not r.ok:
        return None
    try:
        return (r.json().get("PersonalUrl") or "").rstrip("/") or None
    except ValueError:
        return None


def shared_roots(ctx, me):
    """Folders and files shared with you: from your own organisation and from INTO's staff space ("external")."""
    roots = {}
    for q in ("sp.SharedWithMeItemCollection.GetSharedWithMeItems?top=100",
              "sp.SharedWithMeItemCollection.GetSharedWithMeExternalItems?top=100"):
        r = ctx.request.get(f"{me}/_api/{q}", headers=JSONH)
        if not r.ok:
            continue
        try:
            body = r.json()
            items = json.loads(body.get("value") or "[]") if isinstance(body.get("value"), str) else body.get("value", [])
        except ValueError:
            continue
        for it in items:
            f = (it.get("file") or {}).get("SharePointItem") or {}
            if not f.get("FileUrl") or not f.get("SitePath"):
                continue
            from urllib.parse import unquote, urlsplit
            path = unquote(urlsplit(f["FileUrl"]).path)
            vis = it.get("Visualization") or {}
            roots[path] = {"site": f["SitePath"], "path": path, "name": vis.get("Title") or path.rsplit("/", 1)[-1],
                           "kind": vis.get("Type") or ("Folder" if "." not in path.rsplit("/", 1)[-1] else "File"),
                           "by": f.get("ModifiedByDisplayName") or "", "modified": (it.get("file") or {}).get("LastModifiedDateTime")}
    return list(roots.values())


def list_roots():
    from playwright.sync_api import sync_playwright
    status(state="running", step="Looking for files shared with you on OneDrive…", error=None)
    with sync_playwright() as p:
        ctx = session(p)
        me = my_space(ctx)
        if not me:
            ctx.close()
            status(state="login", step="Sign in to Microsoft (OneDrive) once.", finished=now())
            return
        roots = shared_roots(ctx, me)
        ctx.close()
    save(FOUND, {"at": now(), "me": me, "roots": roots})
    status(state="idle", step=f"Found {len(roots)} shared folders and files.", finished=now(), roots=len(roots))


def walk(ctx, site, folder, depth=0, out=None, failed=None):
    """Every file under a shared folder (name, path, modified, size)."""
    from urllib.parse import quote
    out = [] if out is None else out
    if depth > 8 or len(out) > 3000:
        return out
    url = (f"{site}/_api/web/GetFolderByServerRelativePath(decodedurl='{quote(folder.replace(chr(39), chr(39) * 2))}')?$expand=Files,Folders"
           "&$select=Files/Name,Files/ServerRelativeUrl,Files/TimeLastModified,Files/Length,Folders/Name,Folders/ServerRelativeUrl"
           "&$expand=Files/ModifiedBy&$select=Files/ModifiedBy/Email")
    try:
        r = ctx.request.get(url, headers=JSONH)
        j = r.json() if r.ok else None
    except Exception:
        r, j = None, None
    if j is None:
        if failed is not None:
            failed.append(f"{folder.rsplit('/', 1)[-1]} ({r.status if r else 'no answer'})")
        return out
    out += [{"path": f["ServerRelativeUrl"], "modified": f.get("TimeLastModified"), "size": int(f.get("Length") or 0),
             "by": ((f.get("ModifiedBy") or {}).get("Email") or "").lower()} for f in j.get("Files", [])]
    for sub in j.get("Folders", []):
        if sub["Name"] != "Forms":
            walk(ctx, site, sub["ServerRelativeUrl"], depth + 1, out, failed)
        time.sleep(0.2)
    return out


MATERIAL = ("worksheet", "answer", "article", "case", "study", "report", "week", "lesson", "presentation", "group", "table",
            "template", "question", "sqs", "rqs", "task", "notes", "slides", "reading", "essay", "guide", "handout", "quiz",
            "test", "exam", "plan", "brief", "rubric", "criteria", "activity", "exercise", "review", "summary", "glossary")


def named_after_someone_else(stem, my_names):
    """A Word file called only by people's names ("Kevin.docx", "Ezoza Ansariia.docx", "lulu & gheed.docx"):
    a classmate's copy, unless your own name is in it."""
    import re
    low = stem.lower()
    if any(n and n in low for n in my_names):
        return False
    words = re.findall(r"[a-zа-яё']+", low)
    return (0 < len(words) <= 4 and not re.search(r"\d", low) and not any(w.startswith(MATERIAL) for w in words))


def wanted(files, me, my_names=()):
    """Teachers' materials and your own work only. Teachers often copy a worksheet for every student: a file last
    edited by another student (same email domain as yours) is a classmate's work and is never downloaded, and blank
    copies ("Table (3).docx", "Worksheet Copy15.docx") count once."""
    import re
    domain = me.split("@")[-1] if "@" in me else None
    if not domain:                                       # we don't know who you are: download nothing (privacy first)
        return []
    keep, seen = [], set()
    for f in sorted(files, key=lambda f: f["path"]):
        by = f.get("by", "")
        if by and domain and by.endswith("@" + domain) and by != me:
            continue                                     # a classmate's copy
        folder, name = f["path"].rsplit("/", 1)
        stem, _, ext = name.rpartition(".")
        base = re.sub(r"\s*(?:-\s*)?(?:\(\d+\)|copy\s*\d*)\s*$", "", stem or name, flags=re.I).strip().lower()
        if by != me and (folder, base, ext.lower()) in seen:
            continue                                     # another blank copy of the same worksheet
        if ext.lower() in ("docx", "doc") and by != me and named_after_someone_else(base, my_names):
            continue
        seen.add((folder, base, ext.lower()))
        keep.append(f)
    return keep


def sync():
    """Download new and changed files from the shared folders mapped to a subject (data/onedrive_map.json)."""
    from urllib.parse import quote
    from playwright.sync_api import sync_playwright
    status(added=[], updated=[], failed=[])
    found, mapping = load(FOUND, {}), load(MAP, {})
    roots = [r for r in found.get("roots", []) if mapping.get(r["path"])]
    if not roots:
        status(state="idle", step="Nothing shared with you is linked to a subject yet.", finished=now())
        return
    manifest = load(MANIFEST, {})
    added, updated, failed = [], [], []
    status(state="running", step="Checking your OneDrive folders…", error=None, started=now())
    with sync_playwright() as p:
        ctx = session(p)
        if not my_space(ctx):
            ctx.close()
            status(state="login", step="Sign in to Microsoft (OneDrive) once.", finished=now())
            return
        visited, me = set(), {}
        my_names = {str(load(DATA / "progress.json", {}).get("settings", {}).get("name", "")).split(" ")[0].lower()} - {""}
        for root in roots:
            subject = mapping[root["path"]]
            if root["site"] not in visited:      # the staff space signs you in as a guest on the first visit
                visited.add(root["site"])
                try:
                    ctx.pages[0].goto(root["site"], wait_until="domcontentloaded", timeout=90_000)
                    ctx.pages[0].wait_for_timeout(3000)
                except Exception:
                    pass
                try:
                    r = ctx.request.get(root["site"] + "/_api/web/currentuser?$select=Email,Title", headers=JSONH)
                    j = r.json() if r.ok else {}
                    me[root["site"]] = (j.get("Email") or "").lower()
                    if j.get("Title"):
                        my_names.add(j["Title"].split()[0].lower())
                except Exception:
                    me[root["site"]] = ""
            if root["kind"] == "Folder" and "@" not in me.get(root["site"], ""):
                failed.append(f"{root['name']} (couldn't check who you are there; will try again)")
                continue
            files = walk(ctx, root["site"], root["path"], failed=failed) if root["kind"] == "Folder" else \
                [{"path": root["path"], "modified": root.get("modified"), "size": 0}]
            base = root["path"].rsplit("/", 1)[0] if root["kind"] != "Folder" else root["path"]
            files = wanted(files, me.get(root["site"], ""), my_names)
            todo = [f for f in files if f["path"].rsplit(".", 1)[-1].lower() not in SKIP
                    and manifest.get(f["path"], {}).get("modified") != f["modified"]]
            for i, f in enumerate(todo, 1):
                rel = f["path"][len(base):].strip("/") or f["path"].rsplit("/", 1)[-1]
                local = "/".join([clean(subject), "OneDrive", clean(root["name"])] + [clean(x) for x in rel.split("/")])
                status(step=f"OneDrive, {subject}: downloading {i} of {len(todo)}: {rel.rsplit('/', 1)[-1][:60]}")
                try:
                    r = ctx.request.get(f"{root['site']}/_api/web/GetFileByServerRelativePath(decodedurl="
                                        f"'{quote(f['path'].replace(chr(39), chr(39) * 2))}')/$value", timeout=180_000)
                    body = r.body() if r.ok else None
                except Exception as e:
                    failed.append(f"{rel} ({type(e).__name__})")
                    continue
                if body is None:
                    failed.append(f"{rel} ({r.status})")
                    continue
                dest = paths.COURSES / local
                if not dest.resolve().is_relative_to(paths.COURSES.resolve()):
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(body)
                kind = "updated" if f["path"] in manifest else "added"
                manifest[f["path"]] = {"modified": f["modified"], "local": local}
                (added if kind == "added" else updated).append({"path": local, "course": subject, "kind": kind, "source": "onedrive"})
                save(MANIFEST, manifest)
                time.sleep(0.3)
            save(MANIFEST, manifest)
        ctx.close()
    if added or updated:
        status(step="Turning the new files into text…")
        import extract_text
        extract_text.main()
        log = load(DATA / "sync_log.json", [])
        log += [{**x, "date": now()} for x in added + updated]
        save(DATA / "sync_log.json", log[-2000:])
    status(state="idle", finished=now(), last_ok=now(), added=added, updated=updated, failed=failed[:50],
           step=(f"{len(added)} new, {len(updated)} updated from OneDrive." if added or updated else "Nothing new on OneDrive."))


def login():
    """A visible window: sign in to Microsoft yourself; closes once OneDrive opens."""
    from playwright.sync_api import sync_playwright
    status(state="login_window", step="Sign in to Microsoft in the window that opened.", error=None)
    with sync_playwright() as p:
        ctx = session(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(MY, wait_until="domcontentloaded", timeout=90_000)
        deadline = time.time() + 600
        while time.time() < deadline:
            try:
                if not ctx.pages:
                    break
                if "-my.sharepoint.com" in ctx.pages[-1].url and "login" not in ctx.pages[-1].url:
                    ctx.pages[-1].wait_for_timeout(2500)
                    break
                ctx.pages[-1].wait_for_timeout(1500)
            except Exception:
                break
        try:
            ctx.close()
        except Exception:
            pass
    status(state="idle", step="Signed in.")


def probe():
    """Headless: read what is shared with you, in full, for building the sync (uses the saved Microsoft sign-in)."""
    from playwright.sync_api import sync_playwright
    out = {"at": now()}
    with sync_playwright() as p:
        opts = dict(user_data_dir=str(PROFILE), headless=True, chromium_sandbox=True, service_workers="block")
        try:
            ctx = p.chromium.launch_persistent_context(channel="chrome", **opts)
        except Exception:
            ctx = p.chromium.launch_persistent_context(**opts)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(MY, wait_until="domcontentloaded", timeout=90_000)
        for _ in range(40):
            if "-my.sharepoint.com/personal/" in page.url:
                break
            page.wait_for_timeout(1500)
        out["url"] = page.url.split("?")[0]
        if "-my.sharepoint.com/personal/" in page.url:
            u = urlsplit(page.url)
            me = f"{u.scheme}://{u.netloc}" + "/".join(u.path.split("/")[:3])
            out["me"] = me
            for name, q in (("shared", "sp.SharedWithMeItemCollection.GetSharedWithMeItems?top=200"),
                            ("external", "sp.SharedWithMeItemCollection.GetSharedWithMeExternalItems?top=200")):
                r = ctx.request.get(f"{me}/_api/{q}", headers={"Accept": "application/json;odata=verbose"})
                out[name] = {"status": r.status, "body": r.text()[:400_000]}
        ctx.close()
    (DATA / "onedrive_probe.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print("probe:", out.get("url"), {k: v.get("status") for k, v in out.items() if isinstance(v, dict)})


if __name__ == "__main__":
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        {"explore": explore, "probe": probe, "list": list_roots, "sync": sync, "login": login}[(sys.argv[1:] or ["explore"])[0]]()
    except Exception as e:
        status(state="error", error=f"{type(e).__name__}: {e}"[:400], finished=now())
        raise
