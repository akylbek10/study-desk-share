#!/usr/bin/env python3
"""Class emails from your teachers and INTO (Outlook on the web): when a teacher is away, the cover teacher often
sends the slides and homework by email instead of putting them on IntoStudy.

    python outlook.py sync    read new emails from staff (read-only), keep their text and download their attachments
    python outlook.py text    turn the attachments Study Desk has filed into text (after the server sorted them)

Read-only: it never sends, deletes, moves or marks an email (reading through this way leaves "unread" as it is).
Uses the same Chrome profile as the IntoStudy sync and OneDrive (data/browser-profile): Outlook's own page signs in
with the Microsoft sign-in you already have, and its access is used only in memory, never saved or logged.
Only emails from staff addresses are kept (INTO / university, not other students, not Compass or IntoStudy
notifications, which Study Desk reads directly): data/outlook_mail.json. Attachments wait in data/outlook_files/
until the server files them under courses/<Subject>/Outlook/.
"""
import hashlib
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import paths

DATA = paths.DATA
PROFILE = DATA / "browser-profile"
STATUS, MAIL, SEEN = DATA / "outlook_status.json", DATA / "outlook_mail.json", DATA / "outlook_seen.json"
FILES = DATA / "outlook_files"
OWA = "https://outlook.cloud.microsoft"
API = OWA + "/api/v2.0/me"
STAFF = ("intoglobal.com", "iupl.onmicrosoft.com", "man.my-into.com", "manchester.ac.uk", "ncuk.ac.uk")
COMPASS_MAIL = "compass@compass.email"         # Compass's "email the class": no news post, only this email
CLASS_RE = re.compile(r"Re:\s*Class\s*-\s*([A-Z]{2,6}\d{3,5}[A-Z]?)\b")
FILE_LINK = re.compile(r"https://[\w.-]+\.compass\.education/Services/FileDownload/EmailAttachmentHandler\?[^\s<>\"]+")   # names can hold ( )
MAX_FILE = 30 * 1024 * 1024
SKIP_EXT = {".ics", ".vcf", ".p7s", ".eml", ".msg"}


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
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", s or "")
    return re.sub(r"\s+", " ", s).strip(" .")[:100] or "untitled"


def staff(addr, me):
    """A teacher or INTO address: not another student (INTO + digits), not you, not a notification robot."""
    addr = (addr or "").lower()
    local, _, dom = addr.partition("@")
    if not dom or addr == me or re.fullmatch(r"into\d+", local):
        return False
    return any(dom == d or dom.endswith("." + d) for d in STAFF)


def session(p):
    PROFILE.mkdir(parents=True, exist_ok=True)
    opts = dict(user_data_dir=str(PROFILE), headless=True, chromium_sandbox=True, service_workers="block",
                viewport={"width": 1200, "height": 850})
    try:
        return p.chromium.launch_persistent_context(channel="chrome", **opts)
    except Exception:
        return p.chromium.launch_persistent_context(**opts)


def access(ctx):
    """Open Outlook's calendar (not the inbox, so nothing gets opened or marked) and take the access the page itself
    uses. Returns the request headers, or None when the Microsoft sign-in has expired."""
    got = {}

    def seen(r):
        a = r.headers.get("authorization", "")
        if not got and a.startswith("Bearer ") and r.url.startswith(OWA + "/api/"):
            got.update({"Authorization": a, "X-AnchorMailbox": r.headers.get("x-anchormailbox", "")})
    ctx.on("request", seen)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        page.goto(OWA + "/calendar/view/day", wait_until="domcontentloaded", timeout=90_000)
    except Exception:
        pass
    for _ in range(45):
        if got or "login" in page.url.split("/")[2]:
            break
        page.wait_for_timeout(1000)
    return {**got, "Accept": "application/json"} if got else None


def get(ctx, H, url, **kw):
    r = ctx.request.get(url, headers=H, timeout=120_000, **kw)
    if not r.ok:
        raise RuntimeError(f"Outlook answered {r.status}")
    return r


def term_start():
    try:
        return date.fromisoformat(json.loads(paths.TOPICS.read_text())["term"]["week1"])
    except (OSError, ValueError, KeyError):
        return date.today() - timedelta(days=60)


def sync():
    from playwright.sync_api import sync_playwright
    status(state="running", step="Reading class emails from your teachers…", error=None, started=now(), added=0)
    seen, mail = load(SEEN, {}), load(MAIL, [])
    last = load(STATUS, {}).get("last_ok")
    if not load(STATUS, {}).get("admin_v1"):      # 1.0.7 reads staff emails once more (admin deadlines): fetch them again
        seen, last = {k: v for k, v in seen.items() if not v.get("kept")}, None
    if not load(STATUS, {}).get("compass_v1"):    # 1.0.8 keeps teachers' Compass class emails: look at the last 3 weeks again
        cut = (date.today() - timedelta(days=21)).isoformat()
        seen = {k: v for k, v in seen.items() if v.get("kept") or v.get("date", "") < cut}
        last = min(last or now(), (date.today() - timedelta(days=21)).isoformat())
    since = (datetime.fromisoformat(last) - timedelta(days=2)).date() if last else term_start() - timedelta(days=14)
    kept = 0
    with sync_playwright() as p:
        ctx = session(p)
        H = access(ctx)
        if not H:
            ctx.close()
            status(state="login", step="Sign in to Microsoft again (Settings → IntoStudy files → Sign in again).",
                   finished=now())
            return
        me = H.get("X-AnchorMailbox", "").split(":")[-1].lower()
        url = (f"{API}/messages?$top=50&$orderby=ReceivedDateTime desc"
               f"&$filter=ReceivedDateTime ge {since.isoformat()}T00:00:00Z"
               "&$select=Id,InternetMessageId,From,Subject,ReceivedDateTime,HasAttachments")
        todo = []
        while url:
            j = get(ctx, H, url).json()
            for m in j.get("value", []):
                if m["Id"] in seen:
                    continue
                frm = (m.get("From") or {}).get("EmailAddress") or {}
                if staff(frm.get("Address"), me) or (frm.get("Address") or "").lower() == COMPASS_MAIL:
                    todo.append((m, frm))
                else:
                    seen[m["Id"]] = {"date": m["ReceivedDateTime"][:10]}   # only the id: nothing about it is kept
            url = j.get("@odata.nextLink")
        todo.sort(key=lambda x: x[0]["ReceivedDateTime"])
        for i, (m, frm) in enumerate(todo, 1):
            status(step=f"Reading email {i} of {len(todo)} from your teachers…")
            r = ctx.request.get(f"{API}/messages/{m['Id']}?$select=Body", timeout=120_000,
                                headers={**H, "Prefer": 'outlook.body-content-type="text"'})
            body = (r.json().get("Body") or {}).get("Content", "") if r.ok else ""
            text = re.sub(r"\n{3,}", "\n\n", body.replace("\r", "")).strip()
            extra = {}
            if (frm.get("Address") or "").lower() == COMPASS_MAIL:
                code = CLASS_RE.search(text[:600])
                if not code:                      # sign-in codes, digests, reminders: nothing of it is kept
                    seen[m["Id"]] = {"date": m["ReceivedDateTime"][:10]}
                    continue
                files = []
                for u in dict.fromkeys(FILE_LINK.findall(text)):
                    name = clean((parse_qs(urlsplit(u).query).get("fileName") or ["file"])[0])
                    files.append({"url": u, "name": name})
                extra = {"kind": "compass", "class": code.group(1), "files": files[:20]}
            atts, folder = [], FILES / hashlib.sha1(m["Id"].encode()).hexdigest()[:12]
            if m.get("HasAttachments"):
                lst = get(ctx, H, f"{API}/messages/{m['Id']}/attachments?$select=Id,Name,Size,IsInline,ContentType").json()
                for a in lst.get("value", []):
                    name = clean(a.get("Name"))
                    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
                    if not a.get("@odata.type", "").endswith("FileAttachment") or a.get("IsInline") \
                            or ext in SKIP_EXT or not 0 < (a.get("Size") or 0) <= MAX_FILE \
                            or (a.get("ContentType", "").startswith("image/") and a["Size"] < 60_000):
                        continue                   # signatures, logos, calendar invites
                    try:
                        data = get(ctx, H, f"{API}/messages/{m['Id']}/attachments/{a['Id']}/$value").body()
                    except Exception as e:
                        print("attachment:", name, paths.redact(e)[:200])
                        continue
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / name).write_bytes(data)
                    atts.append({"name": name, "file": str((folder / name).relative_to(DATA)), "size": len(data),
                                 "sha1": hashlib.sha1(data).hexdigest()})
            links = sorted(set(re.findall(r"https://[\w.-]*(?:sharepoint\.com|onedrive\.live\.com|1drv\.ms)/[^\s>\")\]]+", text)))[:10]
            mail = [x for x in mail if x["id"] != m["Id"]]
            mail.append({"id": m["Id"], "date": m["ReceivedDateTime"], "from": (frm.get("Address") or "").lower(),
                         "from_name": frm.get("Name") or "", "subject": (m.get("Subject") or "")[:200],
                         "text": text[:8000], "attachments": atts, "links": links, **extra})
            seen[m["Id"]] = {"date": m["ReceivedDateTime"][:10], "kept": True}
            kept += 1
            save(MAIL, mail[-400:])
            save(SEEN, seen)
            time.sleep(0.2)
        ctx.close()
    cutoff = (date.today() - timedelta(days=400)).isoformat()
    save(SEEN, {k: v for k, v in seen.items() if v.get("date", "") >= cutoff})
    save(MAIL, mail[-400:])
    status(state="idle", finished=now(), last_ok=now(), added=kept, admin_v1=True, compass_v1=True,
           step=f"{kept} new emails from your teachers." if kept else "No new emails from your teachers.")


def text():
    import extract_text
    extract_text.main()


if __name__ == "__main__":
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        {"sync": sync, "text": text}[(sys.argv[1:] or ["sync"])[0]]()
    except Exception:
        paths.crash(status, state="error", step="Reading emails failed; will try again.", finished=now())
