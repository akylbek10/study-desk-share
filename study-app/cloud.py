"""Study Desk data in Supabase: one table `kv` (key, data jsonb, rev). Standard library only.

On when SUPABASE_URL and SUPABASE_SERVICE_KEY are set (environment or .env). The key is a server-side
secret key: it never reaches the browser, and the table has row level security with no policies,
so nothing but this server can read it.

Every write checks `rev`, so a change made on the phone is never silently overwritten by the Mac
(or the other way round): the second writer gets Conflict and the user tries again.
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

URL = KEY = ""
_local = threading.local()


class Conflict(ValueError):
    pass


def setup(url, key):
    global URL, KEY
    URL, KEY = (url or "").rstrip("/"), key or ""


def on():
    return bool(URL and KEY)


def begin():
    """Start of an HTTP request: forget what earlier requests on this thread read."""
    _local.seen, _local.snap, _local.in_request = {}, None, True


def fresh():
    """Start of a locked read-modify-write: the next read is current, not the copy from the start of the request
    (a mini test waits a minute or two for AI, and the page's ping saves activity meanwhile)."""
    _local.seen, _local.snap = {}, None


def _seen():
    if not hasattr(_local, "seen"):
        _local.seen = {}
    return _local.seen


# Process-wide copy of the table: {key: (rev, json text)}. Each request asks Supabase for every key's rev
# (one small query) and fetches only the rows that changed, so a page that reads 40 lessons costs 1-2 requests.
_cache = {}
_cache_lock = threading.Lock()


def _snapshot():
    if getattr(_local, "snap", None) is None:
        revs = {r["key"]: r["rev"] for r in _req("GET", "kv?select=key,rev")}
        with _cache_lock:
            stale = [k for k, r in revs.items() if _cache.get(k, (None,))[0] != r]
        for i in range(0, len(stale), 40):
            names = ",".join('"' + k.replace('"', '\\"') + '"' for k in stale[i:i + 40])
            rows = _req("GET", f"kv?key=in.({_k(names)})&select=key,data,rev")
            with _cache_lock:
                for r in rows:
                    _cache[r["key"]] = (r["rev"], json.dumps(r["data"], ensure_ascii=False))
        with _cache_lock:
            for k in [k for k in _cache if k not in revs]:
                del _cache[k]
            _local.snap = {k: _cache[k] for k in revs if k in _cache}
    return _local.snap


def _remember(key, rev, obj):
    entry = None if obj is None else (rev, json.dumps(obj, ensure_ascii=False))
    with _cache_lock:
        if entry:
            _cache[key] = entry
        else:
            _cache.pop(key, None)
    snap = getattr(_local, "snap", None)
    if snap is not None:
        if entry:
            snap[key] = entry
        else:
            snap.pop(key, None)


def _req(method, path, body=None, prefer=None):
    headers = {"apikey": KEY, "Content-Type": "application/json"}
    if not KEY.startswith("sb_"):               # old-style JWT keys also go in Authorization
        headers["Authorization"] = f"Bearer {KEY}"
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(f"{URL}/rest/v1/{path}", data=data, method=method, headers=headers)
    for attempt in range(3):                   # connections sometimes drop mid-handshake; a retry is safe
        try:                                   # (a write that did land comes back as a Conflict, never doubled)
            with urllib.request.urlopen(req, timeout=20) as r:
                text = r.read()
            return json.loads(text) if text else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code == 409:
                raise Conflict("This changed on another device a moment ago. Try again.") from None
            raise RuntimeError(f"Supabase {e.code}: {detail}") from None
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            if attempt == 2:
                raise RuntimeError(f"Can't reach Supabase ({getattr(e, 'reason', e)}). Check the internet connection.") from None
            time.sleep(0.5 * (attempt + 1))


def _k(key):
    return urllib.parse.quote(key, safe="")


def get(key, default):
    if getattr(_local, "in_request", False):
        rev, text = _snapshot().get(key, (0, None))
        _seen()[key] = rev
        return json.loads(text) if text is not None else default
    rows = _req("GET", f"kv?key=eq.{_k(key)}&select=data,rev")    # background jobs read fresh
    _seen()[key] = rows[0]["rev"] if rows else 0
    return rows[0]["data"] if rows else default


def put(key, obj, force=False):
    """Save obj. Unless force, refuse if someone else saved since this request read the key."""
    now = datetime.now(timezone.utc).isoformat()
    rev = _seen().get(key)
    if rev is None or force:
        rows = _req("GET", f"kv?key=eq.{_k(key)}&select=rev")
        rev = rows[0]["rev"] if rows else 0
    if rev == 0:
        _req("POST", "kv", {"key": key, "data": obj, "rev": 1, "updated_at": now}, prefer="return=minimal")
    else:
        rows = _req("PATCH", f"kv?key=eq.{_k(key)}&rev=eq.{rev}", {"data": obj, "rev": rev + 1, "updated_at": now},
                    prefer="return=representation")
        if not rows:
            raise Conflict("This changed on another device a moment ago. Try again.")
    _seen()[key] = rev + 1
    _remember(key, rev + 1, obj)


def delete(key):
    _req("DELETE", f"kv?key=eq.{_k(key)}")
    _seen().pop(key, None)
    _remember(key, 0, None)


def keys(prefix):
    if getattr(_local, "in_request", False):
        return sorted(k for k in _snapshot() if k.startswith(prefix))
    rows = _req("GET", f"kv?key=like.{_k(prefix)}*&select=key")
    return sorted(r["key"] for r in rows)
