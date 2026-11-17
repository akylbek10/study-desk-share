#!/usr/bin/env python3
"""Study Desk: a small local server (Python standard library only).

Run:   python3 server.py        then open http://127.0.0.1:8890
Your Gemini API key is stored in study-app/.env on this Mac only.
"""
import base64
import fnmatch
import json
import mimetypes
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cloud
import paths

APP = paths.APP
# Hosted = the phone version on Vercel: data comes from Supabase, there's no Mac disk, agy, whisper or sync,
# and no AI at all (no Gemini API either, by choice: v1 must never spend API quota).
HOSTED = bool(os.environ.get("VERCEL") or os.environ.get("STUDY_HOSTED"))
TEXT = paths.TEXT
COURSES = paths.COURSES
OVERVIEW = paths.OVERVIEW
STATIC = APP / "static"
DATA = paths.DATA
ENV = paths.ENV
TOPICS_FILE = paths.TOPICS
if HOSTED:
    DATA = Path(tempfile.gettempdir()) / "studydesk"          # the only writable place there; nothing kept
    TEXT = COURSES = OVERVIEW = DATA / "no-course-files-online"
    ENV = DATA / "no.env"
MAC_ONLY = "This needs Study Desk on your Mac. The phone version doesn't make anything new with AI."
PORT = int(os.environ.get("STUDY_PORT") or os.environ.get("PORT") or 8890)
API = "https://generativelanguage.googleapis.com/v1beta"
MAX_CONTEXT_CHARS = 600_000


class _DataLock:
    """Guards every read-modify-write of the data. On entry the cloud forgets what this request read earlier,
    so a request that waited minutes for AI doesn't save over a newer copy (and isn't refused for it)."""
    def __init__(self):
        self._lock = threading.Lock()

    def __enter__(self):
        self._lock.acquire()
        cloud.fresh()

    def __exit__(self, *exc):
        self._lock.release()


LOCK = _DataLock()

DATA.mkdir(parents=True, exist_ok=True)
if not TOPICS_FILE.exists():          # first run: no subjects yet; the setup guide (/setup) builds them
    TOPICS_FILE.write_text(json.dumps({"term": {"name": "Semester 1", "week1": date.today().isoformat()},
                                       "subjects": {}, "exams": [], "timetable": []}, indent=2))
    print("No topics.json yet: wrote an empty one. Open /setup to build your programme.")
CONFIG, TOPICS = {}, {}


def week_start(n):
    """Monday of teaching week n (weeks that start inside a break are skipped)."""
    d, k = date.fromisoformat(CONFIG["term"]["week1"]), 1
    while True:
        in_break = any(b["start"] <= d.isoformat() <= b["end"] for b in CONFIG.get("breaks", []))
        if not in_break:
            if k == n:
                return d
            k += 1
        d += timedelta(days=7)


def week_of(day):
    """Teaching week number of a date (None before week 1 or during a break)."""
    if "term" not in CONFIG or day < date.fromisoformat(CONFIG["term"]["week1"]):
        return None
    for n in range(1, 60):
        if week_start(n) <= day < week_start(n) + timedelta(days=7):
            return n
    return None


def load_config():
    """(Re)read topics.json: at start and after the setup guide or Compass changes it."""
    cfg = json.loads(TOPICS_FILE.read_text())
    CONFIG.clear()
    CONFIG.update(cfg)
    for sub in CONFIG["subjects"].values():          # a topic in the weekly plan starts in its first planned week
        for t in sub["topics"]:
            weeks = [w["week"] for w in sub.get("plan", []) if t["id"] in w["topics"]]
            if weeks and "term" in CONFIG:
                t["start"] = week_start(min(weeks)).isoformat()
        prev = CONFIG.get("term", {}).get("week1", date.today().isoformat())
        for t in sub["topics"]:                      # not in the plan and no date given: after the topic before it
            prev = t.setdefault("start", prev)
    TOPICS.clear()
    TOPICS.update({t["id"]: {**t, "subject": s} for s, sub in CONFIG["subjects"].items() for t in sub["topics"]})


load_config()


# What kind of subject it is decides how questions and notes are written. topics.json can say it per subject
# ("kind": "maths" | "science" | "essay" | "english"); otherwise it is guessed from the name.
_SCIENCE = re.compile(r"physic|chemi|biolog|science|electr|engineer|computing|computer", re.I)
_MATHS = re.compile(r"math|statistic|further|calculus", re.I)
_ENGLISH = re.compile(r"\bEAP\b|english|academic skills|language", re.I)


def subject_kind(name):
    sub = CONFIG["subjects"].get(name, {})
    if sub.get("kind") in ("maths", "science", "essay", "english"):
        return sub["kind"]
    if sub.get("english") or _ENGLISH.search(name):
        return "english"
    if _MATHS.search(name):
        return "maths"
    return "science" if _SCIENCE.search(name) else "essay"


def kind_of(tid):
    return subject_kind(TOPICS[tid]["subject"])


# ---------- storage ----------
# In Supabase (when set up) so the phone sees the same thing: these files plus learn/, review/ and class notes.
# Sync bookkeeping, the file sorter, worksheet copies and audio stay in data/ on the Mac.
SHARED = {"cards.json", "progress.json", "tasks.json", "activity.json", "compass.json"}


def load(name, default):
    if name in SHARED and cloud.on():
        return cloud.get(name, default)
    return read_json(DATA / name, default)


def read_json(p, default):
    name = p.name
    try:
        return json.loads(p.read_text())
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:                  # keep the broken file for recovery instead of overwriting it
        bad = p.with_name(f"{p.name}.corrupt-{datetime.now():%Y%m%d-%H%M%S}")
        try:
            p.replace(bad)
            print(f"{name} was unreadable; moved it to {bad.name}")
        except OSError:
            pass
        return default


def write_atomic(p, text):
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save(name, obj):
    if name in SHARED and cloud.on():
        return cloud.put(name, obj)
    write_atomic(DATA / name, json.dumps(obj, ensure_ascii=False, indent=1))


def read_env():
    env = {}
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


cloud.setup(os.environ.get("SUPABASE_URL") or read_env().get("SUPABASE_URL"),
            os.environ.get("SUPABASE_SERVICE_KEY") or read_env().get("SUPABASE_SERVICE_KEY"))


def api_key():
    return os.environ.get("GEMINI_API_KEY") or read_env().get("GEMINI_API_KEY", "")


def today():
    return date.today().isoformat()


# ---------- activity log (the source of every statistic) ----------
def log_event(kind, topic=None, **extra):
    """Call while holding LOCK."""
    act = load("activity.json", {"events": [], "minutes": {}})
    act["events"].append({"ts": datetime.now().isoformat(timespec="seconds"), "type": kind,
                          **({"topic": topic} if topic else {}), **extra})
    save("activity.json", act)


# ---------- course material ----------
ALL_FILES = sorted(str(p.relative_to(TEXT)) for p in TEXT.rglob("*.txt")) if TEXT.exists() else []


def matches(f, prefixes):
    """A source is a path prefix; one with * is a pattern (teachers renumber week folders: '*Semester 1 Week 4/')."""
    return any(fnmatch.fnmatchcase(f, p + "*") if "*" in p else f.startswith(p) for p in prefixes)


# Files reach a topic by folder (topics.json "sources") or by the sorter (data/file_topics.json):
# {path: {"topic": id | "general", "by": "ai" | "you", "confidence", "reason", "date"}}. Your choice wins.
def sorted_map():
    return load("file_topics.json", {})


def topic_of(f, smap):
    """(topic id | 'general' | None, how): how is 'you', 'folder' or 'ai'."""
    e = smap.get(f)
    if e and e.get("by") == "you":
        return e["topic"], "you"
    for tid, t in TOPICS.items():
        if matches(f, t["sources"]):
            return tid, "folder"
    return (e["topic"], "ai") if e else (None, None)


def is_shared(f):
    return any(matches(f, s.get("shared", [])) for s in CONFIG["subjects"].values())


def topic_counts():
    smap = sorted_map()
    c = Counter(topic_of(f, smap)[0] for f in ALL_FILES)
    for f in ALL_FILES:
        c.update(smap.get(f, {}).get("also", []))
    return c


def topic_files(tid, shared=True):
    t = TOPICS[tid]
    smap = sorted_map()
    extra = CONFIG["subjects"][t["subject"]].get("shared", []) if shared else []
    return [f for f in ALL_FILES if topic_of(f, smap)[0] == tid or tid in smap.get(f, {}).get("also", [])
            or matches(f, extra)]


def topic_material(tid, limit=MAX_CONTEXT_CHARS):
    parts, total = [], 0
    for f in topic_files(tid):
        text = re.sub(r"\n{3,}", "\n\n", (TEXT / f).read_text(errors="ignore")).strip()
        if len(text) < 40:
            continue
        chunk = f"\n\n===== FILE: {f.rsplit('/', 1)[-1].removesuffix('.txt')} =====\n{text}"
        if total + len(chunk) > limit:
            break
        parts.append(chunk)
        total += len(chunk)
    return "".join(parts)


def topic_label(tid):
    t = TOPICS[tid]
    return f"{t['subject']}: {t['name']}" + (f" ({t['code']})" if t.get("code") else "")


def original_for(text_rel):
    """courses/ path of the original file behind a text/ file, if it exists."""
    p = (COURSES / text_rel.removesuffix(".txt")).resolve()
    return p if p.is_relative_to(COURSES.resolve()) and p.is_file() else None


# ---------- Gemini ----------
_model_cache = {"name": None, "chain": None}
# Preferred models, in order. gemini-3.5-flash answered in ~2 s when the newest flash took 50-75 s under load
# (tested 8 Nov 2026). Any other flash models the key offers are used as fallbacks.
PREFERRED = ["gemini-3.5-flash", "gemini-flash-lite-latest", "gemini-3.5-flash-lite"]


def model_chain():
    """The models to try, in order: the one chosen in Settings (if any), then preferred, then the rest."""
    chosen = (load("progress.json", {}).get("settings", {}).get("model")
              or os.environ.get("GEMINI_MODEL") or read_env().get("GEMINI_MODEL"))
    if _model_cache["chain"] is None:
        req = urllib.request.Request(f"{API}/models?pageSize=200", headers={"x-goog-api-key": api_key()})
        with urllib.request.urlopen(req, timeout=30) as r:
            models = json.load(r).get("models", [])
        avail = [m["name"].split("/", 1)[1] for m in models
                 if "generateContent" in m.get("supportedGenerationMethods", []) and "flash" in m["name"]
                 and not re.search(r"image|tts|audio|live|omni|exp|8b", m["name"])]
        rest = sorted((n for n in avail if n not in PREFERRED and "preview" not in n),
                      key=lambda n: tuple(int(x) for x in re.findall(r"\d+", n)) or (0,), reverse=True)
        _model_cache["chain"] = [n for n in PREFERRED if n in avail] + rest
        if not _model_cache["chain"]:
            raise RuntimeError("No Gemini 'flash' model is available for this key. Set one in Settings.")
    chain = list(_model_cache["chain"])
    return ([chosen] + [m for m in chain if m != chosen]) if chosen else chain


def pick_model():
    return model_chain()[0]


class GeminiError(Exception):
    pass


# ---------- Antigravity CLI (agy): your Gemini subscription; the API key is the fallback ----------
# Tested 10 Nov 2026 with agy 1.5.3: print mode reads the prompt as stream-json from stdin (no argv size limit),
# --json-schema returns "structured_output"; images only as files in the working folder.
def find_agy():
    """agy from PATH, Homebrew, or the copy the Mac app downloaded into STUDY_HOME/bin."""
    for c in (os.environ.get("AGY_BIN"), shutil.which("agy"), "/opt/homebrew/bin/agy", "/usr/local/bin/agy",
              str(paths.HOME / "bin" / "agy")):
        if c and os.path.exists(c):
            return c
    return str(paths.HOME / "bin" / "agy")


AGY = find_agy()
AGY_WORK = DATA / "agy-work"
AGY_SLOTS = threading.Semaphore(2)                                    # at most 2 agy runs at once
AGY_TIMEOUT = 480                                                     # seconds; setup_topics.py allows more
AGY_PROJECT = os.environ.get("AGY_PROJECT") or read_env().get("AGY_PROJECT") or "Study Desk"   # keeps these chats apart in Antigravity


AGY_QUOTA = {}      # model -> time.time() when its quota is back


def agy_out_of_quota(model, err):
    m = re.search(r"(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?\s*$", err.split("Resets in", 1)[-1].strip().rstrip(".")) if "Resets in" in err else None
    secs = (int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)) if m else 3600
    AGY_QUOTA[model] = time.time() + max(secs, 600)
    print(f"agy: {model} is out of quota for {secs // 3600}h; using the next model", flush=True)


def agy_chain(think):
    """The chosen model first, then Gemini Flash (high, medium, low): whichever still has quota."""
    order = [agy_model(think), "gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.8-flash-low", agy_model(not think)]
    chain = list(dict.fromkeys(m for m in order if m))
    ok = [m for m in chain if AGY_QUOTA.get(m, 0) < time.time()]
    return ok or chain[-1:]


def agy_model(think):
    """Models come from .env (the setup guide writes them from `agy models`); read on every call."""
    if think:   # tutor, tests, notes
        return os.environ.get("AGY_MODEL") or read_env().get("AGY_MODEL") or "gemini-3.8-flash-high"
    return os.environ.get("AGY_FAST_MODEL") or read_env().get("AGY_FAST_MODEL") or "gemini-3.8-flash-medium"   # sorting


class AgyError(Exception):
    pass


def agy_on():
    global AGY
    if not os.path.exists(AGY):
        AGY = find_agy()                      # installed by the setup guide after the server started
    return os.environ.get("STUDY_ENGINE", read_env().get("STUDY_ENGINE", "agy")) == "agy" and os.path.exists(AGY)


def _no_ordering(s):
    if isinstance(s, dict):
        return {k: _no_ordering(v) for k, v in s.items() if k != "propertyOrdering"}
    return [_no_ordering(x) for x in s] if isinstance(s, list) else s


def agy_call(contents, system=None, schema=None, think=True):
    if HOSTED:
        raise GeminiError(MAC_ONLY)
    AGY_WORK.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(dir=AGY_WORK))
    try:
        turns, n_img = [], 0
        for m in contents:
            chunk = []
            for p in m.get("parts", []):
                if p.get("text"):
                    chunk.append(p["text"])
                d = p.get("inlineData") or p.get("inline_data")
                if d and d.get("data"):
                    n_img += 1
                    mime = d.get("mimeType") or d.get("mime_type") or "image/jpeg"
                    name = f"image{n_img}{mimetypes.guess_extension(mime) or '.jpg'}"
                    (work / name).write_bytes(base64.b64decode(d["data"]))
                    chunk.append(f"[Image attached: open the file {name} in your folder and look at it carefully.]")
            turns.append(("STUDENT" if m.get("role", "user") == "user" else "YOU (your earlier reply)", "\n".join(chunk)))
        body = turns[0][1] if len(turns) == 1 else (
            "CONVERSATION SO FAR:\n\n" + "\n\n".join(f"--- {w} ---\n{t}" for w, t in turns)
            + "\n\nWrite your next reply to the student's last message.")
        prompt = ("You are the AI engine of Study Desk, a study app. Work only from the text below: do not run commands, "
                  "edit files, browse or search" + (", apart from opening the attached image files" if n_img else "")
                  + ". Reply directly with the answer itself.\n\n"
                  + (f"INSTRUCTIONS:\n{system}\n\n" if system else "") + body)
        msg = json.dumps({"event": "user", "message": {"role": "user", "content": prompt}}) + "\n"
        for model in agy_chain(think):         # a model whose quota ran out is skipped until it resets
            cmd = [AGY, "-p=", "--input-format", "stream-json", "--output-format", "stream-json",
                   "--model", model, "--sandbox", "--project", AGY_PROJECT]
            if schema:
                cmd += ["--json-schema", json.dumps(_no_ordering(schema))]
            with AGY_SLOTS:
                try:
                    r = subprocess.run(cmd, input=msg, capture_output=True, text=True, cwd=work, timeout=AGY_TIMEOUT, env=agy_env())
                except subprocess.TimeoutExpired:
                    raise AgyError(f"no answer in {AGY_TIMEOUT // 60} minutes")
            res = next((json.loads(l)["result"] for l in r.stdout.splitlines() if l.startswith('{"event":"result"')), None)
            err = (res or {}).get("error") or ""
            if res and res.get("status") != "SUCCESS" and re.search(r"quota|rate limit|resets in", err, re.I):
                agy_out_of_quota(model, err)
                continue
            break
        if not res or res.get("status") != "SUCCESS":
            raise AgyError((res or {}).get("error") or (r.stderr or r.stdout)[-300:] or f"exit {r.returncode}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if schema:
        out = res.get("structured_output")
        return out if out is not None else json.loads(res.get("response", ""))   # broken JSON: ValueError, retried
    return res.get("response", "").strip()


# The model often writes LaTeX in JSON without doubling the backslash, so "\text" arrives as a tab + "ext",
# "\frac" as a form feed + "rac", "\rightarrow" as a carriage return + "ightarrow". Put the backslash back.
TEX_LOST = [(re.compile(r"\t(?=ext(?:bf|it|rm|sf|tt)?\{|imes|heta|frac|ilde|riangle|(?:an|au|o)(?![a-z]))"), r"\\t"),
            (re.compile(r"\r(?=[a-z])"), r"\\r"), (re.compile("\x0c(?=[a-z])"), r"\\f"), (re.compile("\x08(?=[a-z])"), r"\\b")]


def fix_tex(x):
    if isinstance(x, str):
        for pat, rep in TEX_LOST:
            x = pat.sub(rep, x)
        return x
    if isinstance(x, list):
        return [fix_tex(v) for v in x]
    if isinstance(x, dict):
        return {k: fix_tex(v) for k, v in x.items()}
    return x


def gemini(contents, system=None, schema=None, temperature=0.6, think=True):
    out = _gemini_retry(contents, system, schema, temperature, think)
    return fix_tex(out) if schema else out


def _gemini_retry(contents, system, schema, temperature, think):
    try:
        return _gemini(contents, system, schema, temperature, think)
    except ValueError:                       # cut-off or broken JSON: one more try
        try:
            return _gemini(contents, system, schema, temperature, think)
        except ValueError:
            raise GeminiError("Gemini's answer came back broken. Try again.")


def _gemini(contents, system=None, schema=None, temperature=0.6, think=True):
    """think=True lets the model reason before answering (slower, far fewer mistakes); False for simple sorting jobs."""
    if HOSTED:
        raise GeminiError(MAC_ONLY)
    if agy_on():
        try:
            return agy_call(contents, system, schema, think)
        except AgyError as e:
            print(f"agy failed ({str(e)[:200]}); using the Gemini API instead", flush=True)
            if not api_key():
                raise GeminiError(f"Antigravity didn't answer: {str(e)[:200]}")
    if not api_key():
        raise GeminiError("No Gemini API key yet. Add it in Settings.")
    body = {"contents": contents, "generationConfig": {"temperature": temperature, "maxOutputTokens": 32000}}
    if think and _model_cache.get("thinking", True):
        body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "high"}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if schema:
        body["generationConfig"]["responseMimeType"] = "application/json"
        body["generationConfig"]["responseSchema"] = schema
    try:
        chain = model_chain()[:3]
    except urllib.error.HTTPError as e:
        raise GeminiError(f"Gemini rejected the API key (error {e.code}). Check it in Settings.")
    except (urllib.error.URLError, TimeoutError) as e:
        raise GeminiError(f"Could not reach Gemini: {getattr(e, 'reason', e)}")
    out, last_err = None, ""
    for i, model in enumerate(chain):
        try:
            req = urllib.request.Request(
                f"{API}/models/{model}:generateContent", data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "x-goog-api-key": api_key()})
            with urllib.request.urlopen(req, timeout=120 if i < len(chain) - 1 else 180) as r:
                out = json.load(r)
            break
        except urllib.error.HTTPError as e:
            try:
                msg = json.load(e).get("error", {}).get("message", "")
            except Exception:
                msg = ""
            if e.code == 400 and "thinking" in msg.lower() and "thinkingConfig" in body["generationConfig"]:
                del body["generationConfig"]["thinkingConfig"]      # this model can't think: go on without it
                _model_cache["thinking"] = False
                return _gemini(contents, system, schema, temperature, think=False)
            if e.code in (429, 500, 503, 504):   # busy or over quota: try the next model
                last_err = ("You've hit Gemini's free-tier limit. Wait a minute (or until tomorrow for the daily limit) "
                            "and try again.") if e.code == 429 else f"Gemini is busy right now ({e.code}). Try again in a minute."
                continue
            raise GeminiError(f"Gemini error {e.code}: {msg or e.reason}")
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = f"Gemini took too long or couldn't be reached ({getattr(e, 'reason', e)}). Try again."
            continue
    if out is None:
        raise GeminiError(last_err or "Gemini didn't answer. Try again.")
    cands = out.get("candidates") or []
    if not cands:
        raise GeminiError("Gemini returned no answer (it may have been blocked). Try rephrasing.")
    text = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
    return json.loads(text) if schema else text


def user_parts(text, image=None):
    parts = [{"text": text}]
    if image and image.get("data"):
        parts.append({"inlineData": {"mimeType": image.get("mime", "image/jpeg"), "data": image["data"]}})
    return parts


def history_contents(messages):
    return [{"role": "user" if m["role"] == "user" else "model", "parts": user_parts(m["text"], m.get("image"))}
            for m in messages[-24:]]


STYLE = (
    "You are a patient, encouraging tutor for an International Foundation Year student "
    "(NCUK, INTO Manchester). English is their second language: use clear, simple English and short "
    "paragraphs. If the student writes in Russian, answer in Russian but keep subject terms in English too. "
    "Write maths with LaTeX: $...$ inline and $$...$$ for display. Every piece of maths goes inside $...$, also in "
    "titles, answer options and short phrases (write $\\frac{1}{2}$ and $30^\\circ$, never bare \\frac{1}{2}). "
    "Use Markdown for structure."
)


# Extra rules for subjects that are not maths or science (those keep the prompts as they were).
KIND_RULES = {
    "essay": (
        "\nTHIS SUBJECT IS ASSESSED THROUGH WRITING, NOT CALCULATION (e.g. business, sociology, global studies, "
        "economics, law, politics). Exam and coursework questions are: short definitions and 'explain' questions; "
        "data-response questions on a table, chart or extract; case-study questions on a real or invented "
        "organisation, country or group; source-analysis questions (who wrote it, when, why, how useful and reliable "
        "it is); and essays ('Analyse...', 'Evaluate...', 'To what extent...', 'Discuss...'). Marks reward knowledge "
        "(key terms, theories, theorists and models), application to the case or source, analysis (chains of "
        "reasoning: because... which leads to... so...) and evaluation (weighing both sides, 'it depends on', a "
        "justified conclusion). Use real-world examples and named theories from the course materials, Harvard-style "
        "citations where the course uses them, and model paragraphs (point, explanation, evidence, evaluation) instead "
        "of worked calculations. Use numbers only for data-response questions."),
    "english": (
        "\nTHIS IS AN ENGLISH FOR ACADEMIC PURPOSES (skills) SUBJECT: reading, writing, listening, speaking, "
        "vocabulary, grammar and study skills such as paraphrasing, summarising, referencing and seminar language. "
        "Teach and test the skill itself with real sentences and short texts: identify, choose, correct, rewrite, "
        "paraphrase, structure a paragraph. No calculations and no figures."),
}


def kind_rules(tid):
    return KIND_RULES.get(kind_of(tid), "")


def figure_rules(tid):
    """Drawn figures only for maths and science."""
    return FIGURE_RULES if kind_of(tid) in ("maths", "science") else (
        "\n\nFIGURES: this subject does not use drawn figures; always give kind 'none' (or leave the figure out).")


def system_for(tid, extra=""):
    material = topic_material(tid)
    src = ("There are no lesson files for this topic yet (it may not have been taught). "
           "Use the standard UK A-level / foundation-year syllabus and say so once.") if not TOPICS[tid]["sources"] else ""
    if material:
        src += ("\nHere are the student's own course materials. Base your teaching on them, follow their notation "
                "and methods, and when you use a file mention its name in [square brackets].\n" + material)
    return f"{STYLE}{kind_rules(tid)}\nCurrent topic: {topic_label(tid)}.\n{extra}\n\n{src}"


# ---------- progress ----------
def progress():
    p = load("progress.json", {})
    p.setdefault("topics", {})
    p.setdefault("settings", {})
    p.setdefault("exams", CONFIG["exams"])
    return p


def record_study(p, tid, score=None, max_marks=None):
    t = p["topics"].setdefault(tid, {"confidence": 0, "history": []})
    t["last"] = today()
    if score is not None and max_marks:
        pct = max(0.0, min(1.0, score / max_marks))
        t["history"].append({"date": t["last"], "score": score, "max": max_marks})
        old = t.get("confidence", 0)
        t["confidence"] = round(pct * 5, 1) if not old else round(0.7 * old + 0.3 * pct * 5, 1)


def review_card(card, grade):
    """SM-2, simplified. grade: 0 again, 3 hard, 4 good, 5 easy"""
    if grade < 3:
        card["reps"], card["interval"] = 0, 0
    else:
        card["reps"] = card.get("reps", 0) + 1
        card["interval"] = {1: 1, 2: 3}.get(card["reps"]) or round(card.get("interval", 1) * card.get("ease", 2.5))
        if grade == 3:
            card["interval"] = max(1, round(card["interval"] * 0.6))
    card["ease"] = max(1.3, card.get("ease", 2.5) + 0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02))
    card["due"] = (date.today() + timedelta(days=card["interval"])).isoformat()
    return card


# ---------- statistics (computed only from saved data) ----------
def compute_stats():
    p, cards = progress(), load("cards.json", [])
    act = load("activity.json", {"events": [], "minutes": {}})
    events, minutes = act["events"], act.get("minutes", {})
    t0 = date.today()
    tdy = t0.isoformat()

    # activity per day
    per_day = Counter(e["ts"][:10] for e in events)
    for t in p["topics"].values():
        for h in t.get("history", []):
            per_day.setdefault(h["date"], 0)
    active = {d for d, n in per_day.items() if n} | {d for d, m in minutes.items() if m}
    streak, d = 0, t0
    if tdy not in active:
        d = t0 - timedelta(days=1)
    while d.isoformat() in active:
        streak += 1
        d -= timedelta(days=1)
    best, run, prev = 0, 0, None
    for day in sorted(active):
        cur = date.fromisoformat(day)
        run = run + 1 if prev and (cur - prev).days == 1 else 1
        best, prev = max(best, run), cur
    heat = [{"date": (t0 - timedelta(days=i)).isoformat(),
             "actions": per_day.get((t0 - timedelta(days=i)).isoformat(), 0),
             "minutes": minutes.get((t0 - timedelta(days=i)).isoformat(), 0)} for i in range(111, -1, -1)]

    # practice
    attempts = []
    for tid, t in p["topics"].items():
        if tid in TOPICS:
            for h in t.get("history", []):
                attempts.append({**h, "topic": tid, "subject": TOPICS[tid]["subject"]})
    attempts.sort(key=lambda a: a["date"])
    def acc(rows):
        mx = sum(r["max"] for r in rows)
        return {"answers": len(rows), "earned": round(sum(r["score"] for r in rows), 1), "available": mx,
                "accuracy": round(100 * sum(r["score"] for r in rows) / mx) if mx else None}
    by_topic = defaultdict(list)
    for a in attempts:
        by_topic[a["topic"]].append(a)
    topic_acc = sorted(({"topic": tid, "name": TOPICS[tid]["name"], "subject": TOPICS[tid]["subject"], **acc(r)}
                        for tid, r in by_topic.items()), key=lambda x: x["accuracy"])

    # flashcards
    reviews = [e for e in events if e["type"] == "card_review"]
    recent = [e for e in reviews if e["ts"][:10] >= (t0 - timedelta(days=30)).isoformat()]
    forecast = []
    for i in range(14):
        day = (t0 + timedelta(days=i)).isoformat()
        forecast.append({"date": day, "due": sum((c["due"] <= day) if i == 0 else (c["due"] == day) for c in cards)})

    # subjects & coverage
    subjects = []
    for s, sub in CONFIG["subjects"].items():
        rows = []
        for t in sub["topics"]:
            tp = p["topics"].get(t["id"], {})
            rows.append({"taught": t["start"] <= tdy, "studied": bool(tp.get("last")), "conf": tp.get("confidence", 0),
                         "practised": bool(tp.get("history"))})
        rated = [r["conf"] for r in rows if r["taught"] and r["conf"]]
        sub_attempts = [a for a in attempts if a["subject"] == s]
        subjects.append({
            "name": s, "topics": len(rows), "taught": sum(r["taught"] for r in rows),
            "studied": sum(r["studied"] for r in rows), "rated": len(rated),
            "practised": sum(r["practised"] for r in rows),
            "confidence": round(sum(rated) / len(rated), 1) if rated else None,
            "files": sum(n for tid, n in topic_counts().items() if TOPICS.get(tid, {}).get("subject") == s),
            "cards": sum(TOPICS.get(c["topic"], {}).get("subject") == s for c in cards),
            **{f"practice_{k}": v for k, v in acc(sub_attempts).items()},
        })

    # exam readiness: topics scheduled before the exam that are rated 3+
    exams = []
    for e in sorted(p["exams"], key=lambda e: e["date"]):
        scope = [t for t in TOPICS.values() if t["subject"] == e.get("subject") and t["start"] <= e["date"]]
        st = {t["id"]: topic_standing(p, t["id"]) for t in scope}
        ready = [t for t in scope if st[t["id"]]["status"] == "confirmed"]
        exams.append({**e, "days": (date.fromisoformat(e["date"]) - t0).days, "in_scope": len(scope), "ready": len(ready),
                      "studied": sum(x["status"] == "studied" for x in st.values()),
                      "behind": sum(x["behind"] for x in st.values()),
                      "not_started": sum(not p["topics"].get(t["id"], {}).get("last") for t in scope if t["start"] <= tdy)})

    week = [(t0 - timedelta(days=i)).isoformat() for i in range(7)]
    return {
        "today": tdy,
        "activity": {"streak": streak, "best_streak": best, "active_days": len(active),
                     "minutes_total": sum(minutes.values()), "minutes_week": sum(minutes.get(d, 0) for d in week),
                     "actions_week": sum(per_day.get(d, 0) for d in week), "heat": heat,
                     "first_day": min(active) if active else None},
        "ai": {"tutor": sum(e["type"] == "tutor" for e in events), "assistant": sum(e["type"] == "assistant" for e in events),
               "generated_questions": sum(e.get("count", 0) for e in events if e["type"] in ("practice_generate", "learn_test_generate")),
               "learn_questions": sum(e["type"] == "learn_ask" for e in events)},
        "practice": {**acc(attempts), "by_topic": topic_acc,
                     "trend": [{"date": a["date"], "pct": round(100 * a["score"] / a["max"]), "subject": a["subject"],
                                "topic": TOPICS[a["topic"]]["name"]} for a in attempts if a["max"]][-40:]},
        "cards": {"total": len(cards), "due": forecast[0]["due"] if forecast else 0,
                  "new": sum(c.get("reps", 0) == 0 for c in cards),
                  "learning": sum(0 < c.get("interval", 0) < 21 for c in cards),
                  "mature": sum(c.get("interval", 0) >= 21 for c in cards),
                  "reviews_total": len(reviews), "reviews_week": sum(e["ts"][:10] in week for e in reviews),
                  "retention": round(100 * sum(e.get("grade", 0) >= 3 for e in recent) / len(recent)) if recent else None,
                  "forecast": forecast},
        "subjects": subjects,
        "exams": exams,
        "learn": learn_stats(),
    }


# ---------- figures (diagrams and graphs inside questions) ----------
_PT = {"type": "string"}  # a point name from 'points'
DIAGRAM = {"type": "object", "properties": {
    "points": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"},
        "show": {"type": "boolean"}}, "required": ["name", "x", "y"]}},
    "segments": {"type": "array", "items": {"type": "object", "properties": {
        "a": _PT, "b": _PT, "label": {"type": "string"}, "dashed": {"type": "boolean"},
        "arrow": {"type": "boolean"}}, "required": ["a", "b"]}},
    "angles": {"type": "array", "items": {"type": "object", "properties": {
        "a": _PT, "vertex": _PT, "b": _PT, "label": {"type": "string"},
        "right": {"type": "boolean"}}, "required": ["a", "vertex", "b"]}},
    "arcs": {"type": "array", "items": {"type": "object", "properties": {
        "center": _PT, "a": _PT, "b": _PT, "label": {"type": "string"}}, "required": ["center", "a", "b"]}},
    "circles": {"type": "array", "items": {"type": "object", "properties": {
        "center": _PT, "r": {"type": "number"}, "dashed": {"type": "boolean"}}, "required": ["center", "r"]}},
    "curves": {"type": "array", "items": {"type": "object", "properties": {
        "through": {"type": "array", "items": _PT}, "dashed": {"type": "boolean"},
        "arrow": {"type": "boolean"}}, "required": ["through"]}},
    "ground": {"type": "array", "items": {"type": "object", "properties": {"a": _PT, "b": _PT}, "required": ["a", "b"]}},
    "boxes": {"type": "array", "items": {"type": "object", "properties": {
        "x": {"type": "number"}, "y": {"type": "number"}, "w": {"type": "number"}, "h": {"type": "number"},
        "label": {"type": "string"}}, "required": ["x", "y", "w", "h"]}},
    "texts": {"type": "array", "items": {"type": "object", "properties": {
        "x": {"type": "number"}, "y": {"type": "number"}, "text": {"type": "string"}}, "required": ["x", "y", "text"]}}}}
GRAPH = {"type": "object", "properties": {
    "x_label": {"type": "string"}, "y_label": {"type": "string"},
    "x_min": {"type": "number"}, "x_max": {"type": "number"},
    "y_min": {"type": "number"}, "y_max": {"type": "number"},
    "series": {"type": "array", "items": {"type": "object", "properties": {
        "label": {"type": "string"}, "expr": {"type": "string"},
        "points": {"type": "array", "items": {"type": "object", "properties": {
            "x": {"type": "number"}, "y": {"type": "number"}}, "required": ["x", "y"]}}}}},
    "marks": {"type": "array", "items": {"type": "object", "properties": {
        "x": {"type": "number"}, "y": {"type": "number"}, "label": {"type": "string"}}, "required": ["x", "y"]}}}}
FIGURE = {"type": "object", "properties": {
    "kind": {"type": "string", "enum": ["none", "diagram", "graph"]},
    "caption": {"type": "string"}, "diagram": DIAGRAM, "graph": GRAPH}, "required": ["kind"]}
FIGURE_RULES = (
    "\n\nFIGURES (important): every question (or step) about a shape, triangle, angle, bearing, sector, vector, "
    "force, motion, circuit, wave, apparatus, molecule or graph MUST have a 'figure', just like a printed exam paper; "
    "this includes: triangles and angles, sectors, vectors, force/free-body diagrams, inclined planes, cliffs and "
    "projectiles, pulleys, circuits, waves, ray diagrams, molecules, graphs. Otherwise kind 'none' (always 'none' for "
    "English/EAP). Never put the answer in the figure; the unknown is labelled with its letter (x, θ, F, ?).\n"
    "- kind 'diagram': the app draws it from a spec. Coordinates: x right, y UP, roughly 0-360 by 0-240, drawn to "
    "scale where it matters (a 55° angle should look like 55°). 'points' are named (A, B, O, P1...); show=true prints "
    "the name next to it (use false for helper points). Everything else refers to point names: 'segments' (sides, "
    "rods, strings, surfaces; label = the length/value written beside it, e.g. '6 cm'; arrow=true for a vector or force "
    "pointing from a to b; dashed for construction lines), 'angles' (angle a-vertex-b with label like '55°' or 'θ'; "
    "right=true draws the right-angle square), 'arcs' (circle arc around center from a to b anticlockwise, for "
    "sectors/arc length), 'circles', 'curves' (smooth path through points, e.g. a projectile's path; arrow=true adds a "
    "direction arrow at the end), 'ground' (hatched surface such as a floor, cliff top or slope), 'boxes' (blocks, "
    "masses, resistors, cells; label inside), 'texts' (any other short label at x, y). Forces: one arrow per force "
    "from the object's point, labelled (e.g. 'W = 450 N'), lengths roughly in proportion.\n"
    "- kind 'graph' (the app plots it exactly): for a function give series[].expr in x with explicit * "
    "(e.g. '2*sin(x) + 1', 'x^2 - 3*x', 'exp(-x)'; allowed: + - * / ^ ( ) sin cos tan asin acos atan sqrt abs ln log "
    "exp pi e; radians, or sind/cosd/tand when the axis is in degrees); for data or motion graphs (v-t, s-t, "
    "titration curve) give series[].points. Set axis ranges and labels with units; 'marks' are labelled points. "
    "A one-line 'caption' is optional.\n"
    "Every name used in segments, angles, arcs, curves or ground must be defined in 'points'. "
    "Use 'angles' for every angle (never a plain text label). A launch angle is an angle between the velocity arrow "
    "and a horizontal helper point. In physics, most questions about forces, motion, "
    "projectiles, circuits, waves or optics need a figure; draw the real situation (cliff with ground, slope, "
    "string, pulley) rather than abstract lines. Examples:\n"
    'Triangle: {"kind":"diagram","diagram":{"points":[{"name":"A","x":0,"y":0,"show":true},{"name":"B","x":200,"y":0,'
    '"show":true},{"name":"C","x":60,"y":120,"show":true}],"segments":[{"a":"A","b":"B","label":"8 cm"},{"a":"A","b":"C",'
    '"label":"x"},{"a":"B","b":"C"}],"angles":[{"a":"B","vertex":"A","b":"C","label":"63°"}]}}\n'
    'Block on a 30° slope: {"kind":"diagram","diagram":{"points":[{"name":"O","x":0,"y":0},{"name":"Q","x":260,"y":0},'
    '{"name":"T","x":260,"y":150},{"name":"P","x":150,"y":87},{"name":"W","x":150,"y":-20},{"name":"N","x":110,"y":156},'
    '{"name":"F","x":90,"y":52}],"ground":[{"a":"O","b":"Q"}],"segments":[{"a":"O","b":"T"},{"a":"P","b":"W","arrow":true,'
    '"label":"W = 50 N"},{"a":"P","b":"N","arrow":true,"label":"R"},{"a":"P","b":"F","arrow":true,"label":"F"}],'
    '"angles":[{"a":"Q","vertex":"O","b":"T","label":"30°"}]}}\n'
    'Car leaving a 45 m cliff at 15 m/s: {"kind":"diagram","diagram":{"points":[{"name":"E","x":0,"y":90},'
    '{"name":"T","x":40,"y":90},{"name":"B","x":40,"y":0},{"name":"G","x":300,"y":0},{"name":"V","x":110,"y":90},'
    '{"name":"M","x":150,"y":67.5},{"name":"L","x":260,"y":0,"show":true}],"ground":[{"a":"E","b":"T"},{"a":"B","b":"G"}],'
    '"segments":[{"a":"T","b":"B","label":"45.0 m"},{"a":"T","b":"V","arrow":true,"label":"15.0 m s⁻¹"}],'
    '"curves":[{"through":["T","M","L"],"dashed":true,"arrow":true}],"texts":[{"x":150,"y":-15,"text":"x"}]}}\n'
    'v-t graph: {"kind":"graph","graph":{"x_label":"t / s","y_label":"v / m s⁻¹","series":[{"points":[{"x":0,"y":0},'
    '{"x":4,"y":12},{"x":10,"y":12},{"x":14,"y":0}]}]}}')


def clean_figure(f):
    """Keep a figure only if it has something to draw."""
    if not isinstance(f, dict) or f.get("kind") not in ("diagram", "graph"):
        return None
    cap = str(f.get("caption", ""))[:200]
    if f["kind"] == "diagram":
        d = f.get("diagram") or {}
        if not d.get("points") and not d.get("boxes"):
            return None
        return {"kind": "diagram", "diagram": d, "caption": cap}
    g = f.get("graph") or {}
    if not any(s.get("expr") or s.get("points") for s in g.get("series", [])):
        return None
    return {"kind": "graph", "graph": g, "caption": cap}


def with_figures(items):
    for q in items:
        fig = clean_figure(q.get("figure"))
        if fig:
            q["figure"] = fig
        else:
            q.pop("figure", None)
    return items


# ---------- API handlers ----------
QUESTION_SCHEMA = {"type": "object", "properties": {"questions": {"type": "array", "items": {
    "type": "object", "properties": {"question": {"type": "string"}, "marks": {"type": "integer"},
                                     "mark_scheme": {"type": "string"}, "source": {"type": "string"},
                                     "figure": FIGURE},
    "required": ["question", "marks", "mark_scheme"]}}}, "required": ["questions"]}
MARK_SCHEMA = {"type": "object", "properties": {
    "score": {"type": "number"}, "max_marks": {"type": "number"}, "feedback": {"type": "string"},
    "model_answer": {"type": "string"}}, "required": ["score", "max_marks", "feedback", "model_answer"]}
CARDS_SCHEMA = {"type": "object", "properties": {"cards": {"type": "array", "items": {
    "type": "object", "properties": {"front": {"type": "string"}, "back": {"type": "string"}},
    "required": ["front", "back"]}}}, "required": ["cards"]}


def api_state(_):
    p, cards = progress(), load("cards.json", [])
    tdy = today()
    subjects = []
    counts = topic_counts()
    for s, sub in CONFIG["subjects"].items():
        topics = []
        for t in sub["topics"]:
            tp = p["topics"].get(t["id"], {})
            hist = tp.get("history", [])
            mx = sum(h["max"] for h in hist)
            topics.append({**{k: t[k] for k in ("id", "name", "code", "start")},
                           "files": counts[t["id"]],
                           "confidence": tp.get("confidence", 0), "last": tp.get("last"),
                           "attempts": len(hist),
                           "accuracy": round(100 * sum(h["score"] for h in hist) / mx) if mx else None,
                           "cards": sum(c["topic"] == t["id"] for c in cards),
                           "due": sum(c["topic"] == t["id"] and c["due"] <= tdy for c in cards),
                           "learn": learn_summary(t["id"]), **topic_standing(p, t["id"])})
        subjects.append({"name": s, "short": sub.get("short") or SHORT_SUBJECT.get(s, s), "kind": subject_kind(s),
                         "color": sub.get("color"), "topics": topics})
    readiness = []
    for e in sorted(p["exams"], key=lambda e: e["date"]):
        if e["date"] < tdy:
            continue
        scope = [t for sub in subjects if sub["name"] == e.get("subject") for t in sub["topics"] if t["start"] <= e["date"]]
        readiness.append({**e, "days": (date.fromisoformat(e["date"]) - date.today()).days, "in_scope": len(scope),
                          "confirmed": sum(t["status"] == "confirmed" for t in scope),
                          "studied": sum(t["status"] == "studied" for t in scope),
                          "behind": sum(t["behind"] for t in scope)})
    return {"hosted": HOSTED, "subjects": subjects, "exams": p["exams"], "breaks": CONFIG.get("breaks", []),
            "term": CONFIG.get("term"),
            "assessments": upcoming_assessments(), "readiness": readiness, "pass_mark": PASS_MARK,
            "tasks": tasks_for_app(),
            "timetable": CONFIG.get("timetable", []), "today": tdy, "has_key": bool(api_key()) or agy_on(),
            "model": p["settings"].get("model", ""), "name": p["settings"].get("name", ""),
            "setup_needed": needs_setup(), "app": bool(os.environ.get("STUDY_HOME")), "version": app_version(),
            "compass": compass_public(),
            "telegram": (DATA / "agent_state.json").exists(), "unread": unread_count(), "onedrive": onedrive_public(),      # the Telegram agent (Hermes) has been used here
            "due_total": sum(c["due"] <= tdy for c in cards)}


def api_stats(_):
    return compute_stats()


TUTOR_SCHEMA = {"type": "object", "properties": {
    "answer": {"type": "string"}, "figures": {"type": "array", "items": FIGURE},
    "has_problem": {"type": "boolean"}, "has_calculation": {"type": "boolean"},
    "mistake": {"type": "object", "properties": {"found": {"type": "boolean"}, "front": {"type": "string"},
                                                  "back": {"type": "string"}}, "required": ["found", "front", "back"]}},
    "required": ["answer", "figures", "has_problem", "has_calculation", "mistake"],
    "propertyOrdering": ["figures", "answer", "has_problem", "has_calculation", "mistake"]}
TUTOR_FIGURES = (
    "\nFIGURES IN YOUR ANSWER: 'figures' is a list. When you explain an idea or work through a problem in maths or "
    "science, give 2-3 figures: one for each picture a good teacher would draw on the board while explaining (for "
    "example the real situation, then the forces/vectors or the graph, then the worked example), each complete and "
    "with a one-line caption. Put a line [[FIG 1]], [[FIG 2]], [[FIG 3]] in 'answer' exactly where each figure "
    "belongs, in the same order. A short reply, a hint, or English/EAP: an empty list.")
DRAW_SCHEMA = {"type": "object", "properties": {"figure": FIGURE}, "required": ["figure"]}


def thin_figure(f):
    """A diagram with dots but nothing drawn between them."""
    d = (f or {}).get("diagram") or {}
    return bool(f) and f.get("kind") == "diagram" and not any(d.get(k) for k in ("segments", "circles", "arcs", "curves", "boxes"))
CHECK_SCHEMA = {"type": "object", "properties": {
    "correct": {"type": "boolean"}, "issue": {"type": "string"}, "corrected_answer": {"type": "string"}},
    "required": ["correct"]}
TUTOR_RULES = (
    "\nHOW TO TEACH (you are as careful as the best human tutor):\n"
    "- Explaining: start from what the idea means in plain words, then the rule or formula with every symbol named, "
    "then ONE fully worked example where each line says what you did and why, then the mistake students usually make. "
    "Finish with one short problem for the student to try (do not solve it).\n"
    "- Examples must be concrete, with real numbers and units, in the style of the student's exam questions.\n"
    "- When the student is solving a problem, do NOT give the solution. 'Give me a hint' = one small nudge. "
    "'Show the next step' = only the next line of working. 'Show the full solution' = the whole worked solution with marks-style steps.\n"
    "- When the student sends their working (typed or a photo): read it line by line, say what is right, quote the "
    "FIRST line that is wrong, explain exactly why it is wrong and what rule fixes it, then ask them to redo it from "
    "that line. Do not just give the right answer. If you found a mistake set mistake.found = true, mistake.front = a "
    "short question (one sentence, maths in $...$) that tests the exact thing they got wrong, mistake.back = ONLY the "
    "correct rule or method in 1-3 lines (two separate fields, final wording only, no drafts) "
    "(it becomes a flashcard). Always fill 'mistake'; when there is no student working or no error, mistake.found = false.\n"
    "- Before you answer, work every calculation through yourself and check the final number a second way "
    "(substitute back, check units and size). Never guess a number.\n"
    "- 'answer' is Markdown with LaTeX and MUST be laid out for reading, never one block of text: short paragraphs "
    "separated by blank lines (\\n\\n), '### ' headings for the parts (for example: The idea, The formula, Worked example, "
    "Common mistake, Your turn), each step of working on its own line as a numbered list, inline maths and units in single $...$ (never $$ inside a sentence), display maths in $$...$$ alone on "
    "its own line, **bold** for key terms. has_problem = true when your answer ends with a problem the student must now "
    "solve or they are in the middle of one. has_calculation = true when your answer contains worked numbers or algebra.\n"
    "- 'figure': draw one whenever a picture helps the explanation or the problem (it appears under your answer); "
    "refer to it in the text ('in the diagram below'). Otherwise kind 'none'. A figure must show the whole situation "
    "the way a textbook would, never just two labelled points: e.g. circular motion = the circle (circles), the object "
    "on it, the radius segment labelled r, a velocity arrow along the tangent labelled v and a force arrow pointing to "
    "the centre labelled F; a triangle = all three sides with the known lengths, every given angle as an 'angles' entry with its label (30°) and the right angle with right=true; forces = every force as a "
    "labelled arrow. Example, object on a circle: "
    '{"kind":"diagram","diagram":{"points":[{"name":"O","x":150,"y":120,"show":true},{"name":"P","x":250,"y":120},'
    '{"name":"V","x":250,"y":200},{"name":"F","x":195,"y":120}],"circles":[{"center":"O","r":100}],'
    '"segments":[{"a":"O","b":"P","label":"r","dashed":true},{"a":"P","b":"V","arrow":true,"label":"v"},'
    '{"a":"P","b":"F","arrow":true,"label":"F"}]}}')


TUTOR_RULES_WRITTEN = (
    "\nHOW TO TEACH (you are as careful as the best human tutor):\n"
    "- Explaining: what the idea means in plain words, the key terms defined the way an examiner accepts, the theory "
    "or model with who it comes from if the course names them, ONE real example or short case that shows it, then "
    "the mistake students usually make in exams. Finish with one short exam-style question for the student to try "
    "(do not answer it).\n"
    "- When the student is answering a question, do NOT write the answer for them. 'Give me a hint' = one small nudge. "
    "'Show the next step' = only the next paragraph or point. 'Show the full solution' = a model answer laid out the "
    "way it would earn full marks.\n"
    "- When the student sends their writing (typed or a photo): say what is good, then the FIRST place that loses marks "
    "(missing definition, no application to the case, description instead of analysis, no evaluation or "
    "conclusion, wrong fact, grammar that changes the meaning), why, and how to fix it; ask them to rewrite that part. "
    "If you found a mistake set mistake.found = true, mistake.front = a short question (one sentence) that tests the "
    "exact thing they got wrong, mistake.back = ONLY the correct point or rule in 1-3 lines (it becomes a flashcard). "
    "Always fill 'mistake'; when there is no student work or no error, mistake.found = false.\n"
    "- Never invent facts, statistics, dates or quotes; if you are not sure, say so.\n"
    "- 'answer' is Markdown laid out for reading: short paragraphs separated by blank lines, '### ' headings for the "
    "parts, **bold** key terms, bullet lists where they help. has_problem = true when your answer ends with a question "
    "the student must now answer or they are in the middle of one. has_calculation = true only if it contains worked "
    "numbers. 'figures' is an empty list.")


def tidy_math(text):
    """Repair the LaTeX delimiters the model sometimes gets wrong, so formulas always render."""
    text = re.sub(r"\$[|\]]", "$$", text)                       # "$|" typed instead of "$$"
    for ch, letter in (("\t", "t"), ("\f", "f"), ("\r", "r"), ("\b", "b")):   # "\text" sent as TAB + "ext", etc.
        text = re.sub(re.escape(ch) + r"(?=[a-z])", lambda m: "\\" + letter, text)
    out = []
    for line in text.split("\n"):
        if "$$" in line and re.sub(r"\$\$.+?\$\$", "", line).strip(" .,;:"):   # $$..$$ inside a sentence: inline
            line = line.replace("$$", "$")
        out.append(line)
    text = "\n".join(out)
    if text.count("$$") % 2:                                     # an unclosed display formula
        text = text.replace("$$", "$")
    return text


def tutor_reply(tid, messages, system):
    """One tutor answer: structured (text + figure), double-checked when it contains calculations."""
    contents = history_contents(messages)
    numeric = kind_of(tid) in ("maths", "science")
    rules = FIGURE_RULES + TUTOR_RULES + TUTOR_FIGURES if numeric else figure_rules(tid) + TUTOR_RULES_WRITTEN
    r = gemini(contents, system + rules, TUTOR_SCHEMA, 0.4)
    answer, checked = str(r.get("answer", "")).strip(), False
    if not answer:
        raise GeminiError("Gemini returned an empty answer. Try again.")
    if (r.get("has_calculation") or r.get("has_problem")) and numeric:
        try:                                   # a second, independent pass over the numbers
            question = messages[-1].get("text", "") if messages else ""
            c = gemini([{"role": "user", "parts": user_parts(
                "You are checking a tutor's answer before a student sees it. Solve the problem yourself from scratch, "
                "then compare. Check every number, sign, unit, formula, algebra step and any word that makes no sense. If the tutor sets a problem for the student, solve "
                "it too: it must be fully specified, physically possible and give a sensible answer (no negative "
                "force, mass or length, no impossible triangle); if not, change its numbers so it works. correct = true only if "
                "everything is right. If anything is wrong: issue = what is wrong (one line), corrected_answer = the "
                "full answer rewritten correctly in the same style, language and Markdown/LaTeX, keeping every [[FIG n]] line.\n\n"
                f"STUDENT ASKED:\n{question[:3000]}\n\nTUTOR'S ANSWER:\n{answer}",
                messages[-1].get("image") if messages else None)}],
                f"{STYLE}\nTopic: {topic_label(tid)}.", CHECK_SCHEMA, 0.1)
            checked = True
            if not c.get("correct") and len(str(c.get("corrected_answer", ""))) > 40:
                answer = c["corrected_answer"].strip()
        except GeminiError:
            pass                               # over quota or busy: show the first answer unchecked
    raw = [x for x in (r.get("figures") or [])[:4] if isinstance(x, dict) and x.get("kind") in ("diagram", "graph")]
    figures = [f for f in map(clean_figure, raw) if f and not thin_figure(f)]
    figure = None if figures else clean_figure(raw[0]) if raw else None
    if thin_figure(figure):                    # the drawing came out empty: ask for it on its own
        try:
            figure = clean_figure(gemini([{"role": "user", "parts": [{"text":
                "Draw the ONE figure that best illustrates this tutor's answer (the situation in its worked example "
                "or its problem), complete like a textbook diagram: every line, circle, arrow, angle and label.\n\n"
                f"TUTOR'S ANSWER:\n{answer}"}]}], "You draw exam-paper figures." + FIGURE_RULES, DRAW_SCHEMA, 0.2).get("figure"))
        except GeminiError:
            figure = None
        if thin_figure(figure):
            figure = None
    if figure:
        figures = [figure]
    out = {"answer": tidy_math(answer), "figures": figures, "figure": figures[0] if figures else None,
           "problem": bool(r.get("has_problem")),
           "checked": checked, "card_added": False}
    m = r.get("mistake") or {}
    if m.get("found") and "Back:" in str(m.get("front", "")):     # both sides landed in one field: take the last pair
        tail = str(m["front"]).rsplit("Front:", 1)[-1]
        m["front"], m["back"] = (x.strip() for x in tail.split("Back:", 1))
    if m.get("found") and m.get("front") and m.get("back"):
        with LOCK:
            cards = load("cards.json", [])
            cards.append({"id": uuid.uuid4().hex[:10], "topic": tid, "front": str(m["front"])[:600],
                          "back": str(m["back"])[:1200], "ease": 2.5, "interval": 0, "reps": 0, "due": today(),
                          "created": today(), "from": "mistake"})
            save("cards.json", cards)
            log_event("cards_from_mistakes", tid, count=1)
        out["card_added"] = True
    return out


def api_chat(b):
    tid = b["topic"]
    out = tutor_reply(tid, b.get("messages", []), system_for(tid))
    with LOCK:
        p = progress()
        record_study(p, tid)
        save("progress.json", p)
        log_event("tutor", tid)
    return out


def assistant_context(current_topic=None):
    s = compute_stats()
    p = progress()
    lines = []
    counts = topic_counts()
    for subj, sub in CONFIG["subjects"].items():
        for t in sub["topics"]:
            tp = p["topics"].get(t["id"], {})
            hist = tp.get("history", [])
            mx = sum(h["max"] for h in hist)
            lines.append(f"- [{subj}] {t['name']} {t.get('code', '')} | class starts {t['start']} | "
                         f"confidence {tp.get('confidence', 0)}/5 | last studied {tp.get('last') or 'never'} | "
                         f"practice {len(hist)} answers" + (f", {round(100 * sum(h['score'] for h in hist) / mx)}% marks" if mx else "") +
                         f" | {counts[t['id']]} course files" +
                         (lambda ls: f" | learn mode: {ls['passed']}/{ls['subs']} subtopics passed (pass = {PASS_MARK}%+ in mini test)"
                          if ls["built"] else " | learn mode: not started")(learn_summary(t["id"])))
    overview = OVERVIEW.read_text() if OVERVIEW.exists() else ""
    summary = {k: s[k] for k in ("activity", "practice", "cards", "exams")}
    summary["activity"] = {k: v for k, v in summary["activity"].items() if k != "heat"}
    summary["practice"] = {k: v for k, v in summary["practice"].items() if k != "trend"}
    summary["cards"] = {k: v for k, v in summary["cards"].items() if k != "forecast"}
    days = ["", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    tt = "\n".join(f"- {days[c['day']]} {c['start']}-{c['end']} {c['subject']} ({c['code']}), room {c['room']}, teacher {c['teacher']}"
                   for c in CONFIG.get("timetable", []))
    ctx = (f"Today is {today()} ({date.today().strftime('%A')}).\n\nWEEKLY CLASS TIMETABLE (no classes during breaks: "
           f"{json.dumps(CONFIG.get('breaks', []))}):\n{tt}\n\nPROGRAMME OVERVIEW (from the student's course files):\n{overview}\n\n"
           f"WHERE THE CLASS IS NOW:\n{week_now_text()}\n\n"
           f"THE STUDENT'S STANDING (studied = ticked after studying in Gemini; confirmed = passed a check):\n{brief_text()}\n\n"
           f"TOPICS AND THE STUDENT'S PROGRESS:\n" + "\n".join(lines) +
           f"\n\nSTUDY STATISTICS (JSON, saved data only):\n{json.dumps(summary, ensure_ascii=False)}")
    if current_topic in TOPICS:
        ctx += f"\n\nThe student currently has this topic open: {topic_label(current_topic)}.\n" + topic_material(current_topic, 250_000)
    return ctx


def api_assistant(b):
    system = (STYLE + "\nYou are the student's personal study assistant inside the Study Desk app. You know their "
              "programme, schedule, exams and progress (below). Help them plan, decide what to study, explain things "
              "and stay motivated. Base any claim about their progress ONLY on the data below; if the data is empty, "
              "say so plainly. Keep answers focused and practical. The app has: Today page, subject pages, topic pages "
              "(Tutor, Practice questions, Flashcards, Files), Flashcards review, Profile (statistics), Settings.\n\n"
              + assistant_context(b.get("topic")))
    answer = gemini(history_contents(b.get("messages", [])), system, temperature=0.5)
    with LOCK:
        log_event("assistant", b.get("topic") if b.get("topic") in TOPICS else None)
    return {"answer": answer}


def api_insights(_):
    system = (STYLE + "\nYou are analysing a student's study data. Use ONLY the numbers and facts provided. Never "
              "invent scores, times or habits. If there is too little data for a conclusion, say what data is missing "
              "and how to get it (e.g. 'mark some practice answers').")
    prompt = ("Write a short progress report in Markdown with these sections:\n"
              "### Where you stand\n### Biggest risks before the exams\n### Plan for the next 7 days\n"
              "Make the plan concrete: day by day, naming topics and actions available in the app "
              "(tutor, practice questions, flashcards). Max ~250 words.\n\n" + assistant_context())
    answer = gemini([{"role": "user", "parts": [{"text": prompt}]}], system, temperature=0.4)
    with LOCK:
        log_event("insights")
    return {"answer": answer}


def api_practice_generate(b):
    tid, n = b["topic"], max(1, min(int(b.get("count", 3)), 8))
    prompt = (f"Write {n} new practice questions on {topic_label(tid)} at {b.get('difficulty', 'exam')} difficulty. "
              "Copy the style, wording and mark allocation of the exam questions and past-paper questions in the "
              "materials (but do not copy them word for word). " + (
                  "Mix short and multi-step questions. For each, give a "
                  "detailed mark scheme listing what earns each mark (M1, A1, B1 style for maths), and the name of the "
                  "material it is modelled on as 'source'." if kind_of(tid) in ("maths", "science") else
                  "Mix question types: short 'define/explain' questions, a data-response or case-study question (put the "
                  "data, case or source extract in the question itself) and an extended 'analyse/evaluate/to what "
                  "extent' question. For each, give a mark scheme listing the points that earn marks and, for longer "
                  "answers, what each mark band needs (knowledge, application, analysis, evaluation), and the name of "
                  "the material it is modelled on as 'source'.") + figure_rules(tid))
    out = gemini([{"role": "user", "parts": [{"text": prompt}]}], system_for(tid), QUESTION_SCHEMA, 0.9)
    with_figures(out.get("questions", []))
    with LOCK:
        log_event("practice_generate", tid, count=len(out.get("questions", [])))
    return out


def api_practice_mark(b):
    tid = b["topic"]
    prompt = (f"Mark this answer strictly but fairly, like an exam marker using the mark scheme"
              + (" (for written answers: credit knowledge, application, analysis and evaluation; ignore small "
                 "grammar slips unless they change the meaning)" if kind_of(tid) not in ("maths", "science") else "")
              + ".\n\n"
              f"QUESTION ({b['marks']} marks):\n{b['question']}\n\nMARK SCHEME:\n{b['mark_scheme']}\n\n"
              f"STUDENT ANSWER:\n{b.get('answer') or '(see attached photo of working)'}\n\n"
              "Give score (can be partial), max_marks, feedback (what was right, what lost marks and why, one tip), "
              "and a full model_answer.")
    result = gemini([{"role": "user", "parts": user_parts(prompt, b.get("image"))}], system_for(tid), MARK_SCHEMA, 0.2)
    with LOCK:
        p = progress()
        record_study(p, tid, result["score"], result["max_marks"])
        save("progress.json", p)
        log_event("practice_mark", tid, score=result["score"], max=result["max_marks"])
        result["confidence"] = p["topics"][tid]["confidence"]
    return result


def api_cards_generate(b):
    tid, n = b["topic"], max(3, min(int(b.get("count", 10)), 25))
    existing = [c["front"] for c in load("cards.json", []) if c["topic"] == tid]
    prompt = (f"Make {n} flashcards for {topic_label(tid)} from the materials: key definitions, formulae, rules, "
              "methods and common exam facts. Front: a short question or prompt. Back: a concise answer (LaTeX "
              "allowed). One idea per card."
              + (f"\nDo not repeat these existing cards: {json.dumps(existing[:200], ensure_ascii=False)}" if existing else ""))
    new = gemini([{"role": "user", "parts": [{"text": prompt}]}], system_for(tid), CARDS_SCHEMA, 0.5)["cards"]
    with LOCK:
        cards = load("cards.json", [])
        for c in new:
            cards.append({"id": uuid.uuid4().hex[:10], "topic": tid, "front": c["front"], "back": c["back"],
                          "ease": 2.5, "interval": 0, "reps": 0, "due": today(), "created": today()})
        save("cards.json", cards)
        log_event("cards_generate", tid, count=len(new))
    return {"added": len(new)}


def api_cards_due(b):
    cards = [c for c in load("cards.json", []) if c["due"] <= today() and c["topic"] in TOPICS
             and (not b.get("topic") or c["topic"] == b["topic"])]
    for c in cards:
        c["topic_name"] = topic_label(c["topic"])
        c["subject"] = TOPICS[c["topic"]]["subject"]
    return {"cards": cards}


def api_cards_all(b):
    return {"cards": sorted((c for c in load("cards.json", []) if c["topic"] == b["topic"]), key=lambda c: c["due"])}


def api_cards_review(b):
    with LOCK:
        cards = load("cards.json", [])
        for c in cards:
            if c["id"] == b["id"]:
                review_card(c, int(b["grade"]))
                p = progress()
                record_study(p, c["topic"])
                save("progress.json", p)
                log_event("card_review", c["topic"], grade=int(b["grade"]))
        save("cards.json", cards)
    return {"ok": True}


def api_cards_delete(b):
    with LOCK:
        save("cards.json", [c for c in load("cards.json", []) if c["id"] != b["id"]])
    return {"ok": True}


def api_confidence(b):
    with LOCK:
        p = progress()
        p["topics"].setdefault(b["topic"], {"confidence": 0, "history": []})["confidence"] = float(b["level"])
        save("progress.json", p)
        log_event("confidence", b["topic"], level=float(b["level"]))
    return {"ok": True}


def api_ping(_):
    """One call per active minute from the open app: real study time."""
    with LOCK:
        act = load("activity.json", {"events": [], "minutes": {}})
        act.setdefault("minutes", {})[today()] = act["minutes"].get(today(), 0) + 1
        save("activity.json", act)
    return {"ok": True}


def api_topic_files(b):
    files = []
    for f in topic_files(b["topic"], shared=False):
        parts = f.split("/")
        size = (TEXT / f).stat().st_size
        files.append({"path": f, "name": parts[-1].removesuffix(".txt"),
                      "folder": " / ".join(re.sub(r"^\d+\s+", "", x) for x in parts[1:-1]),
                      "readable": size > 60, "original": bool(original_for(f))})
    return {"files": files}


def topic_links(tid):
    """Videos, sources and other links teachers put on IntoStudy for this topic (from the last sync).
    A link belongs to the topic whose folder it sits in, or else to the topic most files of its folder went to."""
    smap, out = sorted_map(), []
    sorted_links = load("link_topics.json", {})          # what the AI decided for links no folder rule covers
    folder_topic = {}
    for f in ALL_FILES:                                  # folder -> the topic most of its files belong to
        tp = topic_of(f, smap)[0]
        if tp:
            folder_topic.setdefault(f.rsplit("/", 1)[0], Counter())[tp] += 1
    for course, block in load("links.json", {}).items():
        for l in block.get("items", []) if isinstance(block, dict) else []:
            if l["kind"] == "file":
                continue                                 # downloaded: it is among the course files
            tp = topic_of(l["path"] + "/" + l["title"], {})[0]
            if not tp and l["path"] in folder_topic:
                tp = folder_topic[l["path"]].most_common(1)[0][0]
            if not tp:
                tp = sorted_links.get(f"{course}|{l['id']}")
            if tp == tid:
                out.append({**l, "course": course, "folder": " / ".join(re.sub(r"^\d+\s+", "", x) for x in l["path"].split("/")[1:])})
    return out


def sort_links():
    """Ask the AI (fast model) which topic each still-unplaced link belongs to; remembered in data/link_topics.json."""
    if not (api_key() or agy_on()):
        return
    done = load("link_topics.json", {})
    placed = {(l.get("course"), l["id"]) for tid in TOPICS for l in topic_links(tid)}
    for course, block in load("links.json", {}).items():
        subj = course if course in CONFIG["subjects"] else None
        if not subj or not isinstance(block, dict):
            continue
        todo = [l for l in block.get("items", []) if l["kind"] != "file" and (course, l["id"]) not in placed
                and f"{course}|{l['id']}" not in done]
        ids = [t["id"] for t in CONFIG["subjects"][subj]["topics"]]
        menu = "\n".join(f"- {t['id']}: {t['name']}" for t in CONFIG["subjects"][subj]["topics"])
        for i in range(0, len(todo), 40):
            chunk = todo[i:i + 40]
            try:
                out = gemini([{"role": "user", "parts": [{"text":
                    f"Put each of these {subj} videos and links under the topic it belongs to, judging by its title and "
                    f"the course folder it sits in. TOPICS:\n{menu}\n- general: the whole course or none of them\n\nLINKS:\n"
                    + "\n".join(f"{n}. {l['title']} (folder: {l['path']})" for n, l in enumerate(chunk, 1))}]}],
                    None, SORT_SCHEMA, 0.1, think=False)
            except GeminiError as e:
                print("sort links:", e)
                return
            for it in out.get("items", []):
                n = it.get("n")
                if isinstance(n, int) and 1 <= n <= len(chunk):
                    done[f"{course}|{chunk[n - 1]['id']}"] = it.get("topic") if it.get("topic") in ids else "general"
            save("link_topics.json", done)


def api_topic_links(b):
    links = topic_links(b["topic"])
    for l in links:
        if l["kind"] == "youtube":
            m = re.search(r"(?:v=|youtu\.be/|embed/)([\w-]{6,})", l["url"])
            l["embed"] = f"https://www.youtube-nocookie.com/embed/{m.group(1)}" if m else None
        elif l["kind"] == "loom":
            m = re.search(r"loom\.com/(?:share|embed)/(\w+)", l["url"])
            l["embed"] = f"https://www.loom.com/embed/{m.group(1)}" if m else None
        elif l["kind"] == "vimeo":
            m = re.search(r"vimeo\.com/(\d+)", l["url"])
            l["embed"] = f"https://player.vimeo.com/video/{m.group(1)}" if m else None
        elif l["kind"] == "video-d2l":
            l["open"] = f"https://man.intostudy.com/d2l/le/content/{load('links.json', {}).get(l['course'], {}).get('ou', '')}/viewContent/{l['id']}/View"
    return {"links": links}


def file_kind(f):
    name = f.rsplit("/", 1)[-1].lower().removesuffix(".txt")
    if name.endswith((".pptx", ".ppt")):
        return "slides"
    if re.search(r"answer|mark ?scheme|\bms\b|solution", name):
        return "answers"
    if name.endswith((".docx", ".doc")) and re.search(r"worksheet|sqs|rqs|question|task|activity|template|table|case study|w\d+l\d+", name):
        return "worksheet"
    if name.endswith(".pdf"):
        return "reading"
    return "other"


def api_week(b):
    """One teaching week as a lesson page: per subject the week's topics, their slides, worksheets, readings,
    videos and sources (files from that week's folders first)."""
    n = int(b.get("week") or week_of(date.today()) or 1)
    prog = api_program(None)
    wk = re.compile(rf"(?:week\s*0?{n}\b|\bw{n}l\d|\bwk\s*0?{n}\b)", re.I)
    subjects = []
    for s in prog.get("subjects", []):
        w = next((x for x in s["weeks"] if x["week"] == n), None)
        ids = (s["now"] if n == prog.get("week") and s["now"] else (w or {}).get("topics", []))
        topics = []
        for tid in ids:
            if tid not in TOPICS:
                continue
            files = [f for f in topic_files(tid, shared=False) if (TEXT / f).stat().st_size > 60]
            this_week = [f for f in files if wk.search(f)]
            pick = this_week or files[:15]
            groups = defaultdict(list)
            for f in pick:
                groups[file_kind(f)].append({"path": f, "name": f.rsplit("/", 1)[-1].removesuffix(".txt"),
                                             "onedrive": "/OneDrive/" in f})
            topics.append({"id": tid, "name": TOPICS[tid]["name"], "files": groups, "more": len(files) - len(pick),
                           "links": api_topic_links({"topic": tid})["links"], "learn": learn_summary(tid),
                           "review": bool(review_load(tid).get("md"))})
        if not topics:                           # the plan says nothing for this week: the files in its "Week n" folders
            loose = [f for f in ALL_FILES if f.startswith(s["name"] + "/") and wk.search(f) and (TEXT / f).stat().st_size > 60]
            if loose:
                groups = defaultdict(list)
                for f in loose[:30]:
                    groups[file_kind(f)].append({"path": f, "name": f.rsplit("/", 1)[-1].removesuffix(".txt"),
                                                 "onedrive": "/OneDrive/" in f})
                tid = Counter(topic_of(f, sorted_map())[0] for f in loose).most_common(1)[0][0]
                topics.append({"id": tid if tid in TOPICS else None, "name": f"Week {n} files", "files": groups,
                               "more": max(0, len(loose) - 30), "links": [], "learn": learn_summary(tid) if tid in TOPICS else None,
                               "review": False})
        subjects.append({"name": s["name"], "focus": (s["compass"] or {}).get("summary") if n == prog.get("week") and s.get("compass") else (w or {}).get("focus"),
                         "assessment": (w or {}).get("assessment"), "topics": topics})
    return {"week": n, "current": prog.get("week"), "start": week_start(n).isoformat() if "term" in CONFIG else None,
            "weeks": prog.get("weeks", []), "subjects": subjects}


def api_topic_search(b):
    """Find a file by its name or by words in its text (all words must be there).
    Searches one topic ("topic"), one subject ("subject") or every file (neither)."""
    words = [w for w in re.findall(r"\w+", str(b.get("q", "")).lower()) if len(w) > 1][:6]
    out = []
    if not words:
        return {"results": out}
    smap = sorted_map()
    if b.get("topic"):
        pool = topic_files(b["topic"], shared=False)
    else:
        pool = [f for f in ALL_FILES if not b.get("subject") or f.split("/")[0] == b["subject"]]
    for f in pool:
        name = f.rsplit("/", 1)[-1].removesuffix(".txt")
        in_name = all(w in name.lower() for w in words)
        text = (TEXT / f).read_text(errors="ignore")
        low = text.lower()
        if not in_name and not all(w in low for w in words):
            continue
        i = low.find(words[0])
        snippet = re.sub(r"\s+", " ", text[max(0, i - 70):i + 130]).strip() if i >= 0 else ""
        out.append({"path": f, "name": name, "in_name": in_name, "hits": sum(low.count(w) for w in words),
                    "snippet": snippet, "folder": " / ".join(re.sub(r"^\d+\s+", "", x) for x in f.split("/")[1:-1]),
                    "original": bool(original_for(f)), "subject": f.split("/")[0],
                    "topic": topic_of(f, smap)[0]})
    whole = lambda r: all(re.search(rf"\b{re.escape(w)}\b", r["name"].lower()) for w in words)   # "mole" before "molecules"
    out.sort(key=lambda r: (not r["in_name"], not whole(r), -r["hits"]))
    return {"results": out[:40], "words": words}


def api_settings(b):
    with LOCK:
        if b.get("key"):
            key = b["key"].strip()
            if not re.fullmatch(r"[A-Za-z0-9_\-.]{20,300}", key):
                raise GeminiError("That doesn't look like a Gemini API key.")
            env = read_env()
            env["GEMINI_API_KEY"] = key
            ENV.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
            os.chmod(ENV, 0o600)
            _model_cache["chain"] = None
        p = progress()
        if "model" in b:
            p["settings"]["model"] = b["model"].strip()
            _model_cache["chain"] = None
        if "name" in b:
            p["settings"]["name"] = str(b["name"]).strip()[:40]
        if isinstance(b.get("exams"), list):
            p["exams"] = [{"subject": e.get("subject", ""), "name": str(e["name"])[:80], "date": e["date"]}
                          for e in b["exams"] if e.get("name") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", e.get("date", ""))]
        save("progress.json", p)
    if b.get("key"):
        try:
            return {"ok": True, "model": pick_model()}
        except urllib.error.HTTPError as e:
            raise GeminiError(f"Google rejected this key (error {e.code}). Check you copied the whole key.")
        except urllib.error.URLError as e:
            raise GeminiError(f"Key saved, but Google couldn't be reached to check it: {e.reason}")
    return {"ok": True}


# =====================================================================
# LEARN MODE: subtopics, guided lessons, notes, slides, ask, mini tests
# =====================================================================
LEARN_DIR = DATA / "learn"
LEARN_DIR.mkdir(exist_ok=True)
PASS_MARK = 80  # percent needed in a mini test to pass a subtopic
DIFFICULTY = {
    "easy": "easy: recall of definitions and one-step questions",
    "medium": "medium: standard textbook questions with two or three steps",
    "hard": "hard: multi-step problems and tricky distractors",
    "exam": "exam style: exactly like the NCUK IFY exam papers in the materials, mixed difficulty",
}


def learn_load(tid):
    if cloud.on():
        return cloud.get(f"learn/{tid}.json", None) or {"outline": None, "subs": {}}
    return read_json(LEARN_DIR / f"{tid}.json", None) or {"outline": None, "subs": {}}


def learn_save(tid, obj):
    if cloud.on():
        return cloud.put(f"learn/{tid}.json", obj)
    write_atomic(LEARN_DIR / f"{tid}.json", json.dumps(obj, ensure_ascii=False, indent=1))


def learn_summary(tid):
    L = learn_load(tid)
    outline = L.get("outline") or []
    subs = L.get("subs", {})
    passed = [s for s in outline if subs.get(s["id"], {}).get("passed")]
    started = [s for s in outline if subs.get(s["id"], {}).get("step") or subs.get(s["id"], {}).get("tests")]
    # next = the unpassed subtopic you worked on most recently, otherwise the first unpassed one
    open_subs = [s for s in outline if not subs.get(s["id"], {}).get("passed")]
    worked = sorted((s for s in open_subs if subs.get(s["id"], {}).get("last")),
                    key=lambda s: subs[s["id"]]["last"], reverse=True)
    nxt = worked[0] if worked else (open_subs[0] if open_subs else None)
    tests = [t for s in subs.values() for t in s.get("tests", [])]
    return {"built": bool(outline), "subs": len(outline), "passed": len(passed), "started": len(started),
            "next": {"id": nxt["id"], "title": nxt["title"]} if nxt else None,
            "complete": bool(outline) and len(passed) == len(outline),
            "tests": len(tests), "last_learned": max((s.get("last", "") for s in subs.values()), default="") or None}


def find_sub(tid, sid):
    L = learn_load(tid)
    for i, s in enumerate(L.get("outline") or []):
        if s["id"] == sid:
            return L, s, i
    raise KeyError(f"subtopic {sid} (build the lesson plan first)")


def sub_material(tid, sub, limit=350_000):
    """The subtopic's own files first, then the rest of the topic, up to the limit."""
    wanted = [w.lower() for w in sub.get("files", []) if w]
    files = topic_files(tid)
    first = [f for f in files if any(w in f.lower() for w in wanted)]
    parts, total = [], 0
    for f in first + [f for f in files if f not in first]:
        text = re.sub(r"\n{3,}", "\n\n", (TEXT / f).read_text(errors="ignore")).strip()
        if len(text) < 40:
            continue
        chunk = f"\n\n===== FILE: {f.rsplit('/', 1)[-1].removesuffix('.txt')} =====\n{text}"
        if total + len(chunk) > limit:
            break
        parts.append(chunk)
        total += len(chunk)
    return "".join(parts)


def learn_system(tid, sub=None, extra=""):
    material = sub_material(tid, sub) if sub else topic_material(tid, 350_000)
    no_lessons = "" if TOPICS[tid]["sources"] else (
        "There are no lesson files for this topic yet (it may not have been taught). Use the standard UK A-level / "
        "NCUK foundation-year syllabus.\n")
    focus = (f"\nCurrent subtopic: {sub['title']}. Summary: {sub.get('summary', '')}. "
             f"Objectives: {'; '.join(sub.get('objectives', []))}.") if sub else ""
    return (f"{STYLE}{kind_rules(tid)}\nTopic: {topic_label(tid)}.{focus}\n{extra}\n{no_lessons}"
            + (f"\nThe student's course materials (base everything on them and follow their notation and methods):\n{material}" if material else ""))


OUTLINE_SCHEMA = {"type": "object", "properties": {"subtopics": {"type": "array", "items": {"type": "object", "properties": {
    "title": {"type": "string"}, "summary": {"type": "string"},
    "objectives": {"type": "array", "items": {"type": "string"}},
    "files": {"type": "array", "items": {"type": "string"}},
    "minutes": {"type": "integer"}}, "required": ["title", "summary", "objectives"]}}}, "required": ["subtopics"]}
MCQ = {"type": "object", "properties": {
    "question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}},
    "answer_index": {"type": "integer"}, "explanation": {"type": "string"}, "figure": FIGURE},
    "required": ["question", "options", "answer_index", "explanation"]}
LESSON_SCHEMA = {"type": "object", "properties": {
    "intro": {"type": "string"},
    "steps": {"type": "array", "items": {"type": "object", "properties": {
        "title": {"type": "string"}, "explain": {"type": "string"}, "figure": FIGURE, "check": MCQ}, "required": ["title", "explain", "check"]}},
    "summary": {"type": "string"}}, "required": ["intro", "steps", "summary"]}
SLIDES_SCHEMA = {"type": "object", "properties": {"slides": {"type": "array", "items": {"type": "object", "properties": {
    "title": {"type": "string"}, "bullets": {"type": "array", "items": {"type": "string"}},
    "notes": {"type": "string"}}, "required": ["title", "bullets"]}}}, "required": ["slides"]}
TEST_SCHEMA = {"type": "object", "properties": {"questions": {"type": "array", "items": {**MCQ, "properties": {
    **MCQ["properties"], "skill": {"type": "string"}}}}}, "required": ["questions"]}


def clean_mcqs(items):
    out = []
    for q in items:
        opts = [str(o) for o in q.get("options", [])][:5]
        ai = q.get("answer_index")
        if len(opts) >= 2 and isinstance(ai, int) and 0 <= ai < len(opts):
            if not any(re.search(r"\b(above|below|both|neither|all of|none of)\b", o, re.I) for o in opts):
                order = random.sample(range(len(opts)), len(opts))     # the model likes to put the answer first
                opts, ai = [opts[i] for i in order], order.index(ai)
            out.append({**q, "options": opts, "answer_index": ai})
    return with_figures(out)


def slugify(s, i):
    return f"s{i + 1}-" + (re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "part")


def api_learn_outline(b):
    tid = b["topic"]
    L = learn_load(tid)
    if L.get("outline") and not b.get("refresh"):
        return {"outline": L["outline"], "subs": L.get("subs", {}), "summary": learn_summary(tid), "pass_mark": PASS_MARK}
    t = TOPICS[tid]
    folders = sorted({" / ".join(f.split("/")[1:-1]) for f in topic_files(tid, shared=False)})
    prompt = (f"Split the topic '{t['name']}' ({t['subject']}{', ' + t['code'] if t.get('code') else ''}) into 3 to 8 "
              "lesson-sized subtopics in the order they should be learned, the way a teacher would plan separate lessons. "
              "Each needs a short clear title, a one-sentence summary, 2-4 measurable learning objectives, the names of the "
              "course files that cover it (exact file names from the materials, max 6), and an estimate of minutes to learn it. "
              "Cover everything in the materials that could be examined. Skip admin, homework logistics and teacher-only notes."
              + (f"\nCourse folders for this topic: {json.dumps(folders, ensure_ascii=False)}" if folders else ""))
    out = gemini([{"role": "user", "parts": [{"text": prompt}]}], learn_system(tid), OUTLINE_SCHEMA, 0.3)
    outline = [{"id": slugify(s["title"], i), "title": s["title"].strip(), "summary": s.get("summary", ""),
                "objectives": s.get("objectives", [])[:5], "files": s.get("files", [])[:6],
                "minutes": int(s.get("minutes") or 20)} for i, s in enumerate(out["subtopics"][:8])]
    with LOCK:
        L = learn_load(tid)
        L["outline"] = outline
        L.setdefault("subs", {})
        learn_save(tid, L)
        log_event("learn_outline", tid, count=len(outline))
    return {"outline": outline, "subs": L["subs"], "summary": learn_summary(tid), "pass_mark": PASS_MARK}


def _cached_part(b, part, make):
    tid, sid = b["topic"], b["sub"]
    L, sub, _ = find_sub(tid, sid)
    cached = L["subs"].get(sid, {}).get(part)
    if cached and not b.get("refresh"):
        return {part: cached}
    value = make(tid, sub)
    with LOCK:
        L = learn_load(tid)
        L["subs"].setdefault(sid, {})[part] = value
        learn_save(tid, L)
        log_event(f"learn_{part}", tid, sub=sid)
    return {part: value}


def make_lesson(tid, sub):
    prompt = ("Create a guided lesson for this subtopic, like a patient one-to-one tutor. 4 to 7 steps in a logical "
              "order. Each step: a short title; 'explain' in Markdown (100-220 words) that teaches ONE idea, with a worked "
              "example where useful; and one multiple-choice 'check' question (4 options, answer_index 0-3, explanation "
              "of why the answer is right and why the most tempting wrong option is wrong) that tests that step. "
              "Also a 2-3 sentence 'intro' (why this matters, what they'll be able to do) and a short 'summary'."
              + figure_rules(tid))
    out = gemini([{"role": "user", "parts": [{"text": prompt}]}], learn_system(tid, sub), LESSON_SCHEMA, 0.5)
    steps = []
    for s in out.get("steps", []):
        check = clean_mcqs([s["check"]])
        if check:
            steps.append({**with_figures([s])[0], "check": check[0]})
    return {"intro": out.get("intro", ""), "steps": steps, "summary": out.get("summary", "")}


def make_notes(tid, sub):
    sections = ("## Key ideas\n## Definitions\n## Formulas and rules (with units)\n## Method, step by step\n"
                "## Worked example\n## Common mistakes\n## Exam tips\n") if kind_of(tid) in ("maths", "science") else (
                "## Key ideas\n## Key terms\n## Theories, models and who they come from\n## Examples and case studies\n"
                "## Arguments for and against (evaluation)\n## How to answer the exam questions\n## Common mistakes\n"
                if kind_of(tid) == "essay" else
                "## Key ideas\n## Useful language and structures\n## Examples\n## Common mistakes\n## Exam tips\n")
    prompt = ("Write revision notes for this subtopic in Markdown, ready to revise from before the exam:\n"
              + sections + "Skip a section only if it truly doesn't apply. "
              "Be concise and precise; use tables where they help.")
    return gemini([{"role": "user", "parts": [{"text": prompt}]}], learn_system(tid, sub), temperature=0.4)


def make_slides(tid, sub):
    prompt = ("Make a teaching slide deck for this subtopic: 6 to 12 slides. First slide: title and what you'll learn. "
              "Then one idea per slide, max 5 short bullets each (LaTeX allowed), including worked-example slides. "
              "Last slide: summary and 3 quick self-check questions. Add brief speaker 'notes' to each slide "
              "explaining it in more detail.")
    return gemini([{"role": "user", "parts": [{"text": prompt}]}], learn_system(tid, sub), SLIDES_SCHEMA, 0.5)["slides"]


def api_learn_lesson(b):
    return _cached_part(b, "lesson", make_lesson)


def api_learn_notes(b):
    return _cached_part(b, "notes", make_notes)


def api_learn_slides(b):
    return _cached_part(b, "slides", make_slides)


def api_learn_ask(b):
    tid = b["topic"]
    _, sub, _ = find_sub(tid, b["sub"])
    focus = b.get("focus") or ""
    extra = ("The student is in the middle of learning this subtopic and has paused to ask a question. Answer it directly "
             "(keep it focused on what they asked), then (if useful) check their understanding with one short question. "
             + (f"\nThey are currently looking at:\n{focus[:4000]}" if focus else ""))
    out = tutor_reply(tid, b.get("messages", []), learn_system(tid, sub, extra))
    with LOCK:
        L = learn_load(tid)
        L["subs"].setdefault(sub["id"], {})["last"] = today()
        learn_save(tid, L)
        p = progress()
        record_study(p, tid)
        save("progress.json", p)
        log_event("learn_ask", tid, sub=sub["id"])
    return out


TEST_STYLE = {
    "maths": "Calculations should need working, not guessing.",
    "science": "Calculations should need working, not guessing.",
    "essay": ("Test understanding and application, not just recall: give a short case, data or source extract in the "
              "question and ask which concept, explanation, strength, limitation or conclusion fits it best."),
    "english": "Use real sentences and short passages; test the skill (choose, correct, paraphrase, structure).",
}
SOLVE_SCHEMA = {"type": "object", "properties": {"answers": {"type": "array", "items": {"type": "object", "properties": {
    "n": {"type": "integer"}, "index": {"type": "integer"}}, "required": ["n", "index"]}}}, "required": ["answers"]}


def verify_mcqs(tid, qs):
    """A second pass solves every question without seeing the key; questions whose key it disagrees with are dropped."""
    if kind_of(tid) == "english" or len(qs) < 3:
        return qs
    listing = "\n\n".join(f"Q{i}. {q['question']}\n" + "\n".join(f"  ({j}) {o}" for j, o in enumerate(q["options"]))
                          + (f"\n  [figure: {json.dumps(q['figure'], ensure_ascii=False)[:1500]}]" if q.get("figure") else "")
                          for i, q in enumerate(qs))
    try:
        r = gemini([{"role": "user", "parts": [{"text":
            "Solve each multiple-choice question carefully yourself, with full working in your head. For each give "
            "'index' = the number of the one correct option, or -1 if no option is correct, more than one is correct, "
            f"or the question cannot be answered as written.\n\n{listing}"}]}],
            f"You are a careful examiner for {topic_label(tid)}.", SOLVE_SCHEMA, 0.1)
    except GeminiError:
        return qs                              # over quota or busy: keep the test as written
    solved = {a.get("n"): a.get("index") for a in r.get("answers", [])}
    good = [q for i, q in enumerate(qs) if solved.get(i, q["answer_index"]) == q["answer_index"]]
    return good if len(good) >= max(3, len(qs) // 2) else qs


def api_learn_test(b):
    tid = b["topic"]
    _, sub, _ = find_sub(tid, b["sub"])
    n = max(3, min(int(b.get("count", 10)), 25))
    level = b.get("difficulty", "medium")
    prompt = (f"Write a {n + 2}-question multiple-choice test on this subtopic. Difficulty: {DIFFICULTY.get(level, DIFFICULTY['medium'])}. "
              "Cover every learning objective. 4 options each with exactly one correct answer (answer_index 0-3), "
              "plausible distractors based on common mistakes, and an explanation of the correct answer. Vary where the "
              "correct option sits. Put the objective being tested in 'skill'. " + TEST_STYLE.get(kind_of(tid), TEST_STYLE["science"])
              + figure_rules(tid))
    out = gemini([{"role": "user", "parts": [{"text": prompt}]}], learn_system(tid, sub), TEST_SCHEMA, 0.8)
    qs = verify_mcqs(tid, clean_mcqs(out.get("questions", [])))[:n]
    if not qs:
        raise GeminiError("Gemini didn't return usable questions. Try again.")
    with LOCK:
        log_event("learn_test_generate", tid, sub=sub["id"], count=len(qs))
    return {"questions": qs, "pass_mark": PASS_MARK}


def api_learn_submit(b):
    tid = b["topic"]
    total, correct = int(b["total"]), int(b["correct"])
    if total <= 0 or not 0 <= correct <= total:
        raise ValueError("bad score")
    pct = round(100 * correct / total)
    with LOCK:
        L, sub, _ = find_sub(tid, b["sub"])
        st = L["subs"].setdefault(sub["id"], {})
        st.setdefault("tests", []).append({"date": today(), "difficulty": b.get("difficulty", "medium"),
                                           "total": total, "correct": correct, "pct": pct})
        st["best"] = max(st.get("best", 0), pct)
        st["last"] = today()
        newly = pct >= PASS_MARK and not st.get("passed")
        if pct >= PASS_MARK:
            st["passed"] = st.get("passed") or today()
        learn_save(tid, L)
        p = progress()
        record_study(p, tid, correct, total)  # feeds topic confidence the same way as marked practice
        save("progress.json", p)
        log_event("learn_test", tid, sub=sub["id"], score=correct, max=total, pct=pct, difficulty=b.get("difficulty"))
    return {"pct": pct, "passed": pct >= PASS_MARK, "newly_passed": newly, "pass_mark": PASS_MARK,
            "summary": learn_summary(tid), "confidence": p["topics"][tid]["confidence"]}


def api_learn_save(b):
    """Saves lesson position and personal notes."""
    tid = b["topic"]
    with LOCK:
        L, sub, _ = find_sub(tid, b["sub"])
        st = L["subs"].setdefault(sub["id"], {})
        if "step" in b:
            st["step"] = max(0, int(b["step"]))
            st["last"] = today()
            if b.get("lesson_done") and not st.get("lesson_done"):
                st["lesson_done"] = today()
                log_event("learn_lesson_done", tid, sub=sub["id"])
        if "my_notes" in b:
            st["my_notes"] = str(b["my_notes"])[:50_000]
        learn_save(tid, L)
    return {"ok": True}


def api_learn_mistakes(b):
    """Turns wrong test answers into flashcards (no AI needed)."""
    tid = b["topic"]
    items = b.get("items", [])[:30]
    with LOCK:
        cards = load("cards.json", [])
        for it in items:
            back = f"**{it['answer']}**\n\n{it.get('explanation', '')}".strip()
            cards.append({"id": uuid.uuid4().hex[:10], "topic": tid, "front": it["question"], "back": back,
                          "ease": 2.5, "interval": 0, "reps": 0, "due": today(), "created": today(), "from": "mistake"})
        save("cards.json", cards)
        log_event("cards_from_mistakes", tid, count=len(items))
    return {"added": len(items)}


def learn_stats():
    rows, tests = [], []
    for tid, t in TOPICS.items():
        s = learn_summary(tid)
        rows.append({"topic": tid, "name": t["name"], "subject": t["subject"], **s})
        for sid, st in learn_load(tid).get("subs", {}).items():
            tests += [{**x, "topic": tid, "subject": t["subject"]} for x in st.get("tests", [])]
    tests.sort(key=lambda x: x["date"])
    built = [r for r in rows if r["built"]]
    return {"topics_built": len(built), "subs_total": sum(r["subs"] for r in built),
            "subs_passed": sum(r["passed"] for r in built), "topics_complete": sum(r["complete"] for r in built),
            "tests": len(tests), "tests_passed": sum(x["pct"] >= PASS_MARK for x in tests),
            "avg_test": round(sum(x["pct"] for x in tests) / len(tests)) if tests else None,
            "by_subject": {s: {"subs": sum(r["subs"] for r in built if r["subject"] == s),
                               "passed": sum(r["passed"] for r in built if r["subject"] == s)} for s in CONFIG["subjects"]},
            "recent_tests": [{**x, "name": TOPICS[x["topic"]]["name"]} for x in tests[-12:]], "pass_mark": PASS_MARK}


# ---------- study in Gemini: a pack (prompt + course files) for a session in the Gemini app ----------
PACKS = paths.PACKS
PACK_MAX_PDFS = 7          # Gemini takes up to 10 files per message: PDFs + 1 notes file + room to spare
PACK_TEXT_LIMIT = 250_000


def pack_rank(f):
    n = f.rsplit("/", 1)[-1].lower()
    if re.search(r"notes|^pp |slides|worked|example|lesson|summary", n):
        return 0
    if re.search(r"exam|question|\bms\b|mark scheme|worksheet|practice", n):
        return 1
    return 2


def api_handoff(b):
    import shutil
    import subprocess
    tid = b["topic"]
    t = TOPICS[tid]
    L = learn_load(tid)
    sub = next((s for s in L.get("outline") or [] if s["id"] == b.get("sub")), None)
    files = [f for f in topic_files(tid) if (TEXT / f).stat().st_size > 60]
    shared = [f for f in files if matches(f, CONFIG["subjects"][t["subject"]].get("shared", []))]   # formula sheet etc.
    wanted = [w.lower() for w in (sub or {}).get("files", []) if w]
    first = [f for f in files if any(w in f.lower() for w in wanted) and f not in shared]
    rest = sorted((f for f in files if f not in first and f not in shared), key=pack_rank)
    ordered = first + shared + rest

    # folder: Study packs/<Subject> - <Topic>[ - <Subtopic>]
    name = re.sub(r'[\\/:*?"<>|]+', " ", f"{SHORT_SUBJECT.get(t['subject'], t['subject'])} - {t['name']}"
                  + (f" - {sub['title']}" if sub else ""))
    name = re.sub(r"\s+", " ", name).strip()[:110]
    folder = (PACKS / name).resolve()
    if not str(folder).startswith(str(PACKS.resolve())):
        raise ValueError("bad name")
    if folder.exists():
        shutil.rmtree(folder)       # a pack is rebuilt every time; nothing else lives there
    folder.mkdir(parents=True)

    import hashlib
    pdfs, seen = [], set()
    for f in ordered:
        o = original_for(f)
        if o and o.suffix.lower() == ".pdf" and len(pdfs) < PACK_MAX_PDFS and o.stat().st_size < 40_000_000:
            h = hashlib.sha1(o.read_bytes()).hexdigest()
            if h in seen:           # the same file posted in two teachers' folders
                continue
            seen.add(h)
            pdfs.append(o)
            shutil.copy2(o, folder / f"{len(pdfs):02d} {o.name}")
    parts, total, seen_text = [], 0, set()
    for f in ordered:
        text = re.sub(r"\n{3,}", "\n\n", (TEXT / f).read_text(errors="ignore")).strip()
        h = hashlib.sha1(text.encode()).hexdigest()
        if h in seen_text:
            continue
        seen_text.add(h)
        chunk = f"\n\n===== FILE: {f.rsplit('/', 1)[-1].removesuffix('.txt')} =====\n{text}"
        if total + len(chunk) > PACK_TEXT_LIMIT:
            break
        parts.append(chunk)
        total += len(chunk)
    notes_name = "00 Course notes (text of all files).txt"
    (folder / notes_name).write_text(f"Course files for {topic_label(tid)}" + (f" / {sub['title']}" if sub else "")
                                     + " (text extracted from the student's IntoStudy files)." + "".join(parts))

    prompt = handoff_prompt(tid, sub, L, [notes_name] + [f"{i:02d} {o.name}" for i, o in enumerate(pdfs, 1)])
    (folder / "Prompt.md").write_text(prompt)
    with LOCK:
        log_event("gemini_handoff", tid, sub=(sub or {}).get("id"))
    if not b.get("no_open"):
        subprocess.run(["open", str(folder)], check=False)
    return {"prompt": prompt, "folder": str(folder), "files": [notes_name] + [f"{i:02d} {o.name}" for i, o in enumerate(pdfs, 1)],
            "sub": (sub or {}).get("title")}


SHORT_SUBJECT = {"Technical Maths": "Maths"}


def handoff_prompt(tid, sub, L, files):
    t = TOPICS[tid]
    numeric = kind_of(tid) in ("maths", "science")
    p = progress()
    tp = p["topics"].get(tid, {})
    hist = tp.get("history", [])
    mx = sum(h["max"] for h in hist)
    exams = sorted((e for e in p["exams"] if e.get("subject") == t["subject"] and e["date"] >= today()), key=lambda e: e["date"])
    exam = (f"{exams[0]['name']} on {exams[0]['date']} ({(date.fromisoformat(exams[0]['date']) - date.today()).days} days away)"
            if exams else "end of Semester 1 exams")
    lines = [
        "You are my personal tutor for this study session. Use Guided Learning: teach me step by step, one idea at a time, "
        "and make me think. Do not dump everything at once.",
        "",
        "## Who I am",
        f"- Student on the NCUK International Foundation Year at INTO Manchester. Subject: {t['subject']}.",
        f"- Next exam: {exam}.",
        f"- Where my class is now: {next((l for l in week_now_text().splitlines() if l.startswith('- ' + t['subject'] + ':')), 'unknown').removeprefix('- ')}",
        "- English is my second language: explain in simple, clear English (if I write in Russian, answer in Russian, but "
        "keep the subject terms in English as they appear in my exam).",
        "",
        "## What we are studying",
        f"- Topic: **{t['name']}**" + (f" ({t['code']})" if t.get("code") else "") + f", taught in class from {t['start']}.",
    ]
    if sub:
        lines += [f"- Today's part: **{sub['title']}**. {sub.get('summary', '')}",
                  "- By the end I must be able to:"] + [f"  - {o}" for o in sub.get("objectives", [])]
    outline = L.get("outline") or []
    if not sub and outline:
        lines += ["- We cover the whole topic: go through the parts below in order, starting with the first one I haven't passed."]
    elif not sub:
        lines += ["- We cover the whole topic. First, from my files, list the 4-7 things I must be able to do for the exam "
                  "on this topic (short objectives), then teach them in that order."]
    if outline:
        lines += ["", "## The whole topic and my progress (pass = 80%+ in a mini test)"]
        for s in outline:
            st = L.get("subs", {}).get(s["id"], {})
            state = (f"passed {st['passed']}" if st.get("passed") else f"best test {st['best']}%" if st.get("tests")
                     else "started" if st.get("step") else "not started")
            lines.append(f"- {'→ ' if sub and s['id'] == sub['id'] else ''}{s['title']}: {state}")
    lines += ["", "## How I'm doing (from my study app)",
              f"- My confidence in this topic: {tp.get('confidence') or 'not rated'}/5"
              + (f"; marked practice answers: {round(100 * sum(h['score'] for h in hist) / mx)}% of marks over {len(hist)} answers" if mx else "; no marked practice yet") + "."]
    if sub:
        tests = L.get("subs", {}).get(sub["id"], {}).get("tests", [])
        if tests:
            lines.append("- My mini tests on this part: " + ", ".join(f"{x['pct']}% ({x['difficulty']}, {x['date']})" for x in tests[-5:]) + ".")
    cards = [c for c in load("cards.json", []) if c["topic"] == tid and c.get("ease", 2.5) < 2.3]
    if cards:
        lines.append("- Flashcards I keep getting wrong: " + "; ".join(c["front"][:120] for c in cards[:8]) + ".")
    lines += [
        "",
        "## My course files (attached)",
        f"I've attached {len(files)} files from my course: " + ", ".join(files) + ". "
        "The .txt file has the text of all my lesson notes, slides, worksheets, exam questions and the formula/data "
        "sheet for this topic; the PDFs are the originals with diagrams. Teach from THESE materials: follow their notation, methods, formula sheet and "
        "the exam style of their questions. If something isn't in them, say so before using outside knowledge.",
        "",
        "## Plan for the session",
        "1. Start with 2 quick diagnostic questions to find what I already know. Wait for my answers.",
        "2. Teach each objective in order: a short explanation, a worked example from my files' style, then one question "
        "for me to try. Wait for my answer, check it, and explain my mistakes before moving on.",
        "3. Use diagrams or tables when they help (triangles, graphs, forces...)." if numeric else
        "3. Use real examples, short cases and tables (theories, for and against) when they help.",
        "4. When I'm ready, give me one full exam-style question with marks, then mark my answer like an examiner "
        + ("(M1/A1/B1 style for maths) and show the model answer." if numeric else
           "(knowledge, application, analysis, evaluation) and show a model answer."),
        "5. Finish with a 5-question mini test. At the end tell me my score as “Score: X/5”, list what I still need to "
        "revise, and give me 3 flashcards (question → answer) for my weak points.",
        "6. Last, write a block titled “Notes for Study Desk”: 8 to 12 short plain-text bullets with what I learned in "
        "this session, the mistakes I made and how to avoid them. I paste it into my study app so I don't have to "
        "search this chat later.",
        "",
        "I can interrupt at any time with a question. Start with step 1 now.",
    ]
    return "\n".join(lines)


def api_handoff_open(b):
    import subprocess
    folder = Path(b["folder"]).resolve()
    if not str(folder).startswith(str(PACKS.resolve()) + os.sep) or not folder.is_dir():
        raise ValueError("unknown folder")
    subprocess.run(["open", str(folder)], check=False)
    return {"ok": True}


def api_handoff_result(b):
    """Score from the Gemini session's final mini test, entered by the student."""
    tid = b["topic"]
    total, correct = int(b["total"]), int(b["correct"])
    if not 0 < total <= 100 or not 0 <= correct <= total:
        raise ValueError("bad score")
    minutes = max(0, min(int(b.get("minutes") or 0), 300))
    out = {}
    if b.get("sub"):
        out = api_learn_submit({"topic": tid, "sub": b["sub"], "total": total, "correct": correct, "difficulty": "gemini"})
    with LOCK:
        p = progress()
        if not b.get("sub"):
            record_study(p, tid, correct, total)
        tp = p["topics"].setdefault(tid, {"confidence": 0, "history": []})
        tp.setdefault("studied", today())                       # a Gemini session counts as studied
        if not b.get("sub") and 100 * correct / total >= PASS_MARK:
            tp["confirmed"] = tp.get("confirmed") or today()    # 80%+ in Gemini's final test on the whole topic
        save("progress.json", p)
        act = load("activity.json", {"events": [], "minutes": {}})
        if minutes:
            act.setdefault("minutes", {})[today()] = act["minutes"].get(today(), 0) + minutes
            save("activity.json", act)
        log_event("gemini_session", tid, sub=b.get("sub"), score=correct, max=total, minutes=minutes)
    return {"pct": round(100 * correct / total), **{k: out[k] for k in ("passed", "newly_passed") if k in out},
            "status": topic_standing(progress(), tid)["status"]}



# ---------- course file sync (sync.py, run in .venv with Playwright) ----------
SYNC = {"proc": None}
VENV_PY = Path(os.environ.get("STUDY_PYTHON") or APP / ".venv" / "bin" / "python")   # Playwright lives here
SYNC_EVERY_H = 2


def reload_files():
    global ALL_FILES
    ALL_FILES = sorted(str(p.relative_to(TEXT)) for p in TEXT.rglob("*.txt")) if TEXT.exists() else []


def sync_running():
    p = SYNC["proc"]
    return p is not None and p.poll() is None


SYNC_START = threading.Lock()


def start_sync(mode="sync"):
    if not VENV_PY.exists():
        raise ValueError("Sync isn't installed: run  python3 -m venv .venv && .venv/bin/pip install playwright pypdf python-pptx python-docx  in study-app")
    args = [str(VENV_PY), str(APP / "sync.py")] + ([mode] if mode in ("login", "courses") else [])
    with SYNC_START:                              # the auto loop and the button can fire together
        if sync_running():
            return False
        log = open(DATA / "sync.log", "a")
        proc = SYNC["proc"] = subprocess.Popen(args, cwd=str(APP), stdout=log, stderr=log)

    def wait():
        proc.wait()
        log.close()
        reload_files()
        try:
            auto_sort()
        except Exception as e:
            print("sort:", e)
        st = load("sync_status.json", {})
        if mode == "sync" or st.get("added") or st.get("updated"):
            _quiet(notify_files, st.get("added"), st.get("updated"))
        if mode == "sync" and st.get("state") == "idle":       # teachers' OneDrive folders, every time IntoStudy is checked
            _quiet(sort_links)
            _quiet(onedrive_cycle)
    threading.Thread(target=wait, daemon=True).start()
    return True


def topic_for_path(path):
    tid = topic_of(path + ".txt" if path + ".txt" in ALL_FILES else path, sorted_map())[0]
    return tid if tid in TOPICS else None


def api_sync_status(_):
    st = load("sync_status.json", {})
    if sync_running() and st.get("state") not in ("running", "login_window"):
        st["state"] = "running"
    elif not sync_running() and st.get("state") in ("running", "login_window"):
        st["state"] = "idle"   # the process ended without updating the status
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    recent = [x for x in load("sync_log.json", []) if x.get("date", "") >= cutoff][::-1][:60]
    for x in recent:
        x["topic"] = topic_for_path(x["path"])
        x["name"] = x["path"].rsplit("/", 1)[-1]
        txt = x["path"] + ".txt"
        x["text"] = txt if txt in ALL_FILES else None
    return {**st, "running": sync_running(), "recent": recent, "every_hours": SYNC_EVERY_H,
            "installed": VENV_PY.exists()}


def api_sync_start(b):
    started = start_sync("login" if b.get("mode") == "login" else "sync")
    return {"started": started}


def auto_sync_loop():
    """Check IntoStudy for new files on start and then every few hours while the app is open."""
    time.sleep(20)
    while True:
        try:
            st = load("sync_status.json", {})
            last = st.get("last_ok") or st.get("finished") or ""
            due = not last or datetime.fromisoformat(last) < datetime.now() - timedelta(hours=SYNC_EVERY_H)
            chosen = CONFIG.get("intostudy_courses") or load("courses.json", {})   # a new install: after the guide
            if VENV_PY.exists() and chosen and due and st.get("state") not in ("login", "login_window", "choose") \
                    and not sync_running():
                start_sync()
        except Exception as e:
            print("auto sync:", e)
        time.sleep(1200)


# ---------- sorter: put files that no folder rule covers into the right topic ----------
SORT_SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
    "n": {"type": "integer"}, "topic": {"type": "string"}, "also": {"type": "array", "items": {"type": "string"}},
    "confidence": {"type": "string", "enum": ["high", "medium", "low"]}, "reason": {"type": "string"}},
    "required": ["n", "topic", "confidence"]}}}, "required": ["items"]}
SORT_LOCK = threading.Lock()


def auto_sort(paths=None):
    """Ask Gemini where each unsorted file belongs. Returns how many files it sorted."""
    if not (api_key() or agy_on()) or not SORT_LOCK.acquire(blocking=False):
        return 0
    try:
        smap = sorted_map()
        todo = [f for f in (paths or ALL_FILES) if f in ALL_FILES and topic_of(f, smap)[0] is None and not is_shared(f)]
        done = 0
        for subj, sub in CONFIG["subjects"].items():
            mine = [f for f in todo if f.startswith(subj + "/")]
            ids = [t["id"] for t in sub["topics"]]
            menu = "\n".join(f"- {t['id']}: {t['name']} ({t.get('code', '')}), class starts {t['start']}" for t in sub["topics"])
            for i in range(0, len(mine), 40):
                chunk = mine[i:i + 40]
                listing = "\n".join(
                    f"{n}. {f.removesuffix('.txt')}\n   begins: " +
                    re.sub(r"\s+", " ", (TEXT / f).read_text(errors="ignore"))[:500]
                    for n, f in enumerate(chunk, 1))
                prompt = (f"Sort these {subj} course files into the topic each one mainly teaches or practises.\n"
                          f"TOPICS:\n{menu}\n- general: the whole subject or many topics at once (syllabus, module "
                          "overview, timetable, past papers or mock exams covering several topics, revision checklist, "
                          "lab safety, study skills, booking posters)\n\n"
                          "Judge by the file name, the folder and the beginning of the text. Class-note numbers like "
                          "'7.3' are lesson numbers, not topics: go by the title and content. Use 'general' only when "
                          "no single topic fits. If a file clearly covers two or more topics (unit notes can cover a whole unit) (e.g. 'Exam Qs Quadratics and Cubics'), "
                          "put the main one in 'topic' and the others in 'also', so no topic misses it. "
                          "confidence: high if obvious, medium if fairly sure, low if guessing. "
                          "reason: a few words.\n\nFILES:\n" + listing)
                out = gemini([{"role": "user", "parts": [{"text": prompt}]}], None, SORT_SCHEMA, 0.1, think=False)
                with LOCK:
                    smap = sorted_map()
                    for it in out.get("items", []):
                        n, tid = it.get("n"), it.get("topic")
                        if isinstance(n, int) and 1 <= n <= len(chunk) and (tid in ids or tid == "general"):
                            if smap.get(chunk[n - 1], {}).get("by") == "you":
                                continue
                            also = [a for a in it.get("also") or [] if a in ids and a != tid][:8]
                            smap[chunk[n - 1]] = {"topic": tid, "also": also, "by": "ai", "confidence": it.get("confidence", "low"),
                                                  "reason": str(it.get("reason", ""))[:120], "date": today()}
                            done += 1
                    save("file_topics.json", smap)
        return done
    finally:
        SORT_LOCK.release()


def api_files_sort(_):
    return {"sorted": auto_sort()}


def api_files_assign(b):
    path, tid = b["path"], b["topic"]
    if path not in ALL_FILES or not (tid in TOPICS or tid == "general"):
        raise ValueError("unknown file or topic")
    with LOCK:
        smap = sorted_map()
        also = [a for a in smap.get(path, {}).get("also", []) if a != tid] if tid != "general" else []
        smap[path] = {"topic": tid, "also": also, "by": "you", "date": today()}
        save("file_topics.json", smap)
    return {"ok": True}


def api_files_check(_):
    smap = sorted_map()
    manifest = load("sync_manifest.json", {})
    st = load("sync_status.json", {})
    missing = sorted(v["path"] for v in manifest.values() if not (COURSES / v["path"]).exists())
    readable_ext = {".pdf", ".pptx", ".docx"}
    unreadable_kind = sorted(str(f.relative_to(COURSES)) for f in COURSES.rglob("*")
                             if f.is_file() and f.suffix.lower() not in readable_ext
                             and not f.name.startswith(("_", ".")) and f.suffix.lower() not in (".txt", ".md"))
    rows, how = [], Counter()
    for f in ALL_FILES:
        tid, by = topic_of(f, smap)
        if is_shared(f):
            tid, by = "general", "shared"
        how[by or "none"] += 1
        size = (TEXT / f).stat().st_size
        e = smap.get(f, {})
        if by in ("ai", "you", None) or size <= 60:
            rows.append({"path": f, "topic": tid, "by": by, "confidence": e.get("confidence"), "also": e.get("also", []),
                         "reason": e.get("reason", ""), "readable": size > 60, "subject": f.split("/")[0]})
    counts = topic_counts()
    topics = [{"id": t["id"], "files": counts[t["id"]]} for t in TOPICS.values()]
    return {"portal_files": len(manifest), "missing": missing, "failed": st.get("failed", []),
            "last_sync": st.get("last_ok"), "text_files": len(ALL_FILES), "how": how, "rows": rows,
            "topics": topics, "no_text": unreadable_kind, "sorting": SORT_LOCK.locked()}



# ---------- programme: where you are in each subject, week by week ----------
COMPASS_SCHEMA = {"type": "object", "properties": {
    "subject": {"type": "string"}, "week": {"type": "integer"},
    "topics": {"type": "array", "items": {"type": "string"}},
    "next_topics": {"type": "array", "items": {"type": "string"}},
    "summary": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}},
    "assessments": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string"}, "date": {"type": "string"}}, "required": ["name", "date"]}},
    "todo": {"type": "array", "items": {"type": "string"}},
    "lessons": {"type": "array", "items": {"type": "object", "properties": {
        "weekday": {"type": "integer"}, "topic": {"type": "string"}, "what": {"type": "string"}},
        "required": ["weekday", "topic", "what"]}}},
    "required": ["subject", "week", "topics", "summary"]}


def fresh_on_intostudy(days=8, edited_only=False):
    """{topic id: newest date} for files teachers changed (or, unless edited_only, opened to you) recently."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    out = {}
    seen = [((v.get("modified") or "")[:10], v["path"]) for v in load("sync_manifest.json", {}).values()]
    if not edited_only:      # teachers often open old files ahead of time: shown, but not used for the pace
        seen += [(x.get("date", "")[:10], x["path"]) for x in load("sync_log.json", [])]
    for d, path in seen:
        if d >= cutoff:
            tid = topic_for_path(path)
            if tid and d > out.get(tid, ""):
                out[tid] = d
    return out


def api_program(_):
    if "term" not in CONFIG:
        return {"subjects": []}
    tdy = date.today()
    cur = week_of(tdy)
    fresh, edited = fresh_on_intostudy(), fresh_on_intostudy(edited_only=True)
    compass = load("compass.json", {})
    weeks_all = sorted({w["week"] for sub in CONFIG["subjects"].values() for w in sub.get("plan", [])})
    subjects = []
    for name, sub in CONFIG["subjects"].items():
        plan = {w["week"]: w for w in sub.get("plan", [])}
        week_of_topic = {}
        for w in sub.get("plan", []):
            for tid in w["topics"]:
                week_of_topic.setdefault(tid, w["week"])
        c = compass.get(name)
        c = c if c and cur and c.get("week") == cur else None
        now = (c["topics"] if c else plan.get(cur, {}).get("topics", [])) if cur else []
        nxt = (c.get("next_topics") if c and c.get("next_topics") else
               plan.get(cur + 1, {}).get("topics", [])) if cur else []
        fresh_here = [t for t in fresh if TOPICS[t]["subject"] == name]
        # are the newest uploads ahead of or behind the plan?
        fw = [week_of_topic[t] for t in edited if t in week_of_topic]
        pace = None
        if cur and fw and plan:
            pace = "ahead" if max(fw) > cur else "behind" if max(fw) < cur - 1 else "on plan"
        # did the teacher replace the plan file after Study Desk first downloaded it?
        changed = max((x["date"][:10] for x in load("sync_log.json", []) if x.get("kind") == "updated"
                       and x.get("path") == sub.get("plan_file")), default=None)
        subjects.append({
            "name": name, "plan_source": sub.get("plan_source"), "plan_note": sub.get("plan_note"), "plan_changed": changed,
            "weeks": [{**plan[n], "start": week_start(n).isoformat()} for n in sorted(plan)],
            "now": now, "next": nxt, "source": "compass" if c else "plan" if plan else None,
            "compass": c, "fresh": {t: fresh[t] for t in fresh_here}, "pace": pace})
    return {"week": cur, "week_start": week_start(cur).isoformat() if cur else None,
            "term": CONFIG["term"]["name"], "weeks": weeks_all, "subjects": subjects}


def week_now_text():
    """A few lines for AI prompts: this week's topics in each subject."""
    d = api_program(None)
    if not d.get("week"):
        return "Not in a teaching week (break or before term)."
    out = [f"Teaching week {d['week']} of {d['term']} (w/c {d['week_start']})."]
    for s in d["subjects"]:
        if s["now"] or s["next"]:
            names = lambda ids: ", ".join(TOPICS[i]["name"] for i in ids) or "nothing listed"
            src = "Compass" if s["source"] == "compass" else "course plan"
            out.append(f"- {s['name']}: now {names(s['now'])}; next {names(s['next'])} (from the {src})"
                       + (f". Compass says: {s['compass']['summary']} {'; '.join(s['compass'].get('items', []))}" if s["compass"] else ""))
    return "\n".join(out)


def api_compass(b):
    """Read a pasted Compass weekly programme (text or screenshot) and save which topics it covers."""
    text, image = (b.get("text") or "").strip(), b.get("image")
    if not text and not (image and image.get("data")):
        raise ValueError("Paste the text from Compass or add a screenshot.")
    cur = week_of(date.today())
    topics = "\n".join(f"{tid}: {t['subject']}: {t['name']}" for tid, t in TOPICS.items())
    plans = json.dumps({n: s.get("plan", []) for n, s in CONFIG["subjects"].items()}, ensure_ascii=False)
    prompt = (f"Today is {today()}, teaching week {cur} (week 1 began {CONFIG['term']['week1']}).\n"
              f"A student copied this weekly programme from their school's Compass page. Work out which subject it is "
              f"for (one of {list(CONFIG['subjects'])}), which teaching week it describes (use dates in it; if none, "
              f"assume this week), which of the topic ids below it covers this week, and which come next if it says. "
              f"Use ONLY these topic ids:\n{topics}\n\nThe course plans, for reference:\n{plans}\n\n"
              f"summary: one short sentence on what this week is about. items: the lessons, homework, tests and "
              f"deadlines it lists, each a short line with its day or date if given. lessons: one per class day it lists, weekday 1 = Monday ... 5 = Friday, the topic id taught that day and what is covered (short). assessments: every quiz, test, assessment or homework deadline it gives a day or date for, date as YYYY-MM-DD. todo: each thing the teacher asks the student to DO (complete a homework, read, bring, prepare), one short line each, keeping names like Hwk10; empty if none.\n\nCOMPASS TEXT:\n{text or '(see the screenshot)'}")
    r = gemini([{"role": "user", "parts": user_parts(prompt, image)}], schema=COMPASS_SCHEMA, temperature=0.2, think=False)
    if r.get("subject") not in CONFIG["subjects"]:
        raise ValueError("Couldn't tell which subject this is. Paste a bit more of the page.")
    ok = lambda ids: [t for t in ids or [] if t in TOPICS and TOPICS[t]["subject"] == r["subject"]]
    entry = {"week": r.get("week") or cur, "topics": ok(r.get("topics")), "next_topics": ok(r.get("next_topics")),
             "summary": r.get("summary", ""), "items": (r.get("items") or [])[:20], "date": today(),
             "assessments": [{"name": str(a["name"])[:120], "date": a["date"]} for a in r.get("assessments") or []
                             if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(a.get("date", "")))][:10],
             "lessons": [{"date": (week_start(r.get("week") or cur) + timedelta(days=l["weekday"] - 1)).isoformat(),
                          "topic": l["topic"], "what": l.get("what", "")}
                         for l in r.get("lessons") or [] if 1 <= l.get("weekday", 0) <= 7 and ok([l.get("topic")])],
             "text": text[:5000]}
    with LOCK:
        c = load("compass.json", {})
        c[r["subject"]] = entry
        save("compass.json", c)
    todo = [] if b.get("no_tasks") else [str(x).strip() for x in r.get("todo") or [] if str(x).strip()][:8]
    added = add_tasks("\n".join(todo), "compass", r["subject"]) if todo else []
    return {"subject": r["subject"], **entry, "tasks_added": [t["text"] for t in added]}


# ---------- notifications (the bell): new files from teachers, Compass posts, homework, timetable changes ----------
def notify(items):
    """Add notifications: {id, kind, title, text, link, subject}; an id that is already there is skipped."""
    items = [i for i in items if i and i.get("id")]
    if not items:
        return
    with LOCK:
        have = load("notifications.json", [])
        ids = {n["id"] for n in have}
        stamp = datetime.now().isoformat(timespec="seconds")
        for i in items:
            if i["id"] not in ids:
                have.append({"ts": i.get("ts") or stamp, "text": "", "link": "", "subject": "", **i})
                ids.add(i["id"])
        save("notifications.json", sorted(have, key=lambda n: n["ts"])[-300:])


def notify_seen():
    return load("notify_seen.json", {}).get("seen", "")


def unread_count():
    seen = notify_seen()
    return sum(n["ts"] > seen for n in load("notifications.json", []))


def api_notify(_):
    seen = notify_seen()
    items = load("notifications.json", [])[::-1][:60]
    return {"items": [{**n, "unread": n["ts"] > seen} for n in items], "unread": unread_count()}


def api_notify_seen(_):
    with LOCK:
        save("notify_seen.json", {"seen": datetime.now().isoformat(timespec="seconds")})
    return {"ok": True}


def notify_files(added, updated):
    """After a sync: one line per new file, or one per course when a teacher uploaded a lot at once."""
    rows = [(x, "new") for x in added or []] + [(x, "updated") for x in updated or []]
    by = defaultdict(list)
    for x, kind in rows:
        by[x.get("course", "")].append((x, kind))
    out, stamp = [], datetime.now().strftime("%Y%m%d%H%M")
    for course, xs in by.items():
        subj = course if course in CONFIG["subjects"] else ""
        if len(xs) > 8:
            out.append({"id": f"files:{course}:{stamp}", "kind": "file", "subject": subj, "link": "#/files",
                        "title": f"{SHORT_SUBJECT.get(course, course)}: {len(xs)} files on IntoStudy",
                        "text": ", ".join(x["path"].rsplit("/", 1)[-1] for x, _ in xs[:3]) + " …"})
            continue
        for x, kind in xs:
            tid = topic_for_path(x["path"])
            name = x["path"].rsplit("/", 1)[-1]
            out.append({"id": f"file:{x['path']}:{stamp}", "kind": "file", "subject": subj,
                        "link": f"#/topic/{tid}/files" if tid else "#/files",
                        "title": f"{SHORT_SUBJECT.get(course, course)}: {'new' if kind == 'new' else 'updated'} file",
                        "text": name + (f" ({TOPICS[tid]['name']})" if tid else "")})
    notify(out)


# ---------- Compass, automatically: compass.py reads it in your own browser session, this turns it into
# your timetable, the week's topics (from class news), tasks and assessments ----------
COMPASS = {"proc": None}
COMPASS_EVERY_H = 2
COMPASS_MAP_SCHEMA = {"type": "object", "properties": {"pairs": {"type": "array", "items": {"type": "object", "properties": {
    "activity": {"type": "string"}, "subject": {"type": "string"}}, "required": ["activity", "subject"]}}}, "required": ["pairs"]}


def compass_running():
    p = COMPASS["proc"]
    return p is not None and p.poll() is None


def compass_connected():
    st = load("compass_status.json", {})
    return bool(st.get("last_ok")) and st.get("signed_in") is not False


def compass_public():
    st = load("compass_status.json", {})
    auto = load("compass_auto.json", {})
    if not compass_running() and st.get("state") in ("running", "login_window"):
        st["state"] = "idle"
    return {**{k: st.get(k) for k in ("state", "step", "signed_in", "last_ok", "error", "via", "seen")},
            "helper": str(HELPER), "asked": time.time() - COMPASS_WANT["at"] < 600,
            "helper_seen": time.time() - COMPASS_WANT["seen"] < 150,
            "running": compass_running(), "connected": compass_connected(), "every_hours": COMPASS_EVERY_H,
            "classes": len(auto.get("timetable", [])), "tasks": auto.get("tasks_added", 0),
            "news": auto.get("news_read", 0), "applied": auto.get("applied"), "installed": VENV_PY.exists()}


def start_compass(mode="fetch"):
    if not VENV_PY.exists():
        raise ValueError("Compass needs the browser part of Study Desk (it comes with the app).")
    with SYNC_START:
        if compass_running():
            return False
        log = open(DATA / "compass.log", "a")
        COMPASS["proc"] = proc = subprocess.Popen([str(VENV_PY), str(APP / "compass.py")] + (["login"] if mode == "login" else []),
                                                  cwd=str(APP), stdout=log, stderr=log, env=os.environ)

    def wait():
        proc.wait()
        log.close()
        if proc.returncode == 0 and load("compass_status.json", {}).get("state") == "idle":
            _quiet(compass_apply)
    threading.Thread(target=wait, daemon=True).start()
    return True


TIMETABLE_SCHEMA = {"type": "object", "properties": {"timetable": {"type": "array", "items": {"type": "object", "properties": {
    "day": {"type": "integer"}, "start": {"type": "string"}, "end": {"type": "string"}, "subject": {"type": "string"},
    "code": {"type": "string"}, "room": {"type": "string"}, "teacher": {"type": "string"}},
    "required": ["day", "start", "end", "subject"]}}}, "required": ["timetable"]}


def api_timetable_image(b):
    """The fallback when Compass won't let Study Desk read it: the weekly timetable from a screenshot."""
    image = b.get("image")
    if not (image and image.get("data")):
        raise ValueError("Add a screenshot of your weekly timetable.")
    if not CONFIG["subjects"]:
        raise ValueError("Build your programme first (step 5), then add the timetable.")
    subjects = list(CONFIG["subjects"])
    r = gemini([{"role": "user", "parts": user_parts(
        "This is a student's weekly class timetable (a screenshot from Compass or similar). List every weekly class: "
        f"day 1=Monday..5=Friday, start/end HH:MM (24 h), subject = the closest of {json.dumps(subjects, ensure_ascii=False)} "
        "(skip anything that is none of them), plus course code, room and teacher if shown.", image)}],
        None, TIMETABLE_SCHEMA, 0.0)
    tt = [{k: str(c.get(k, "")) if k != "day" else int(c["day"]) for k in ("day", "start", "end", "subject", "code", "room", "teacher")}
          for c in r.get("timetable", []) if c.get("subject") in subjects and 1 <= int(c.get("day") or 0) <= 7
          and re.fullmatch(r"\d{1,2}:\d{2}", str(c.get("start", ""))) and re.fullmatch(r"\d{1,2}:\d{2}", str(c.get("end", "")))]
    if not tt:
        raise ValueError("Couldn't read any classes from that picture. Try a clearer screenshot of the whole week.")
    with LOCK:
        cfg = json.loads(TOPICS_FILE.read_text())
        cfg["timetable"] = tt
        write_atomic(TOPICS_FILE, json.dumps(cfg, ensure_ascii=False, indent=2))
        load_config()
    return {"classes": len(tt)}


# The Chrome helper (compass-extension/): Compass's security check stops any browser that a program drives, so the
# reading happens in the student's own Chrome, by a small extension they install once. It asks /api/compass/want
# every minute and posts what Compass returned to /api/compass/push.
HELPER = paths.HOME / "Chrome helper"
COMPASS_WANT = {"at": 0, "seen": 0}


def ensure_helper():
    """Keep a copy of the extension outside the app's code folder, so updates don't move it under Chrome's feet."""
    src = APP / "compass-extension"
    if not src.exists():
        return None
    if not HELPER.exists() or any(not (HELPER / f.name).exists() or (HELPER / f.name).read_bytes() != f.read_bytes()
                                  for f in src.iterdir() if f.is_file()):
        HELPER.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            if f.is_file():
                shutil.copy2(f, HELPER / f.name)
    return HELPER


# What the helper reads, in order ({uid}, {monday}, {end}, {activityId} are filled in by it). Kept here so a fix
# reaches students with an app update instead of a new extension.
COMPASS_CALLS = [
    {"key": "calendar", "service": "Calendar.svc/GetCalendarEventsByUser", "body": {
        "userId": "{uid}", "homePage": True, "activityId": None, "locationId": None, "staffIds": None,
        "startDate": "{monday}", "endDate": "{end}", "page": 1, "start": 0, "limit": 300}},
    {"key": "news", "service": "NewsFeed.svc/GetMyNewsFeedPaged", "body": {"start": 0, "limit": 40}},
    {"key": "tasks", "service": "LearningTasks.svc/GetAllLearningTasksByUserId", "body": {
        "userId": "{uid}", "forceTaskId": 0, "page": 1, "start": 0, "limit": 200,
        "sort": '[{"property":"dueDateTimestamp","direction":"DESC"}]'}},
    # class news ("Class news: FSPH1102 (Physics)…"): each class has its own feed
    {"key": "class_news", "each_activity": True, "max": 12, "service": "NewsFeed.svc/GetActivityNewsFeedPaged",
     "body": {"activityId": "{activityId}", "start": 0, "limit": 20}},
]


def helper_version():
    try:
        return json.loads((APP / "compass-extension" / "manifest.json").read_text()).get("version")
    except (OSError, ValueError):
        return None


def api_compass_want(_):
    COMPASS_WANT["seen"] = time.time()            # only the helper asks this: so we know it's installed
    return {"want": time.time() - COMPASS_WANT["at"] < 600, "every_hours": COMPASS_EVERY_H, "calls": COMPASS_CALLS,
            "helper_version": helper_version()}


def api_compass_push(b):
    """What the Chrome helper read from Compass (as the student, in their own browser)."""
    COMPASS_WANT["at"] = 0
    now_ = datetime.now().isoformat(timespec="seconds")
    st = load("compass_status.json", {})
    if b.get("diag"):
        save("compass_diag.json", {"at": now_, **b["diag"]})   # what the helper saw, for fixing problems
    if b.get("signin") or (not b.get("calendar") and not b.get("error")):
        st.update(state="login", signed_in=False, via="extension", seen=now_, finished=now_,
                  step="Sign in to Compass in Chrome (the Study Desk helper reads it from there).")
        save("compass_status.json", st)
        return {"ok": True}
    if b.get("error") or not isinstance(b.get("calendar"), dict):
        st.update(state="error", via="extension", seen=now_, error=str(b.get("error") or "nothing came back")[:300])
        save("compass_status.json", st)
        return {"ok": True}
    raw = {k: b.get(k) for k in ["fetched", "site", "user_id"] + [c["key"] for c in COMPASS_CALLS]}
    raw["fetched"] = now_
    save("compass_raw.json", raw)
    bad = [k for k in ("calendar", "news", "tasks") if (raw.get(k) or {}).get("status") != 200]
    st.update(state="idle", signed_in=True, via="extension", seen=now_, finished=now_, last_ok=now_, error=None,
              step="Read Compass in Chrome." + (f" Not available: {', '.join(bad)}." if bad else ""))
    save("compass_status.json", st)
    threading.Thread(target=lambda: _quiet(compass_apply), daemon=True).start()
    return {"ok": True}


def api_compass_helper(b):
    """Setup help for the Chrome helper: show its folder, open Chrome's extensions page, or ask for a read now."""
    act = b.get("action")
    if act == "read":
        COMPASS_WANT["at"] = time.time()
        return {"ok": True}
    folder = ensure_helper()
    if not folder:
        raise ValueError("The Chrome helper is missing from this copy of Study Desk.")
    if act in ("install", "extensions"):           # Chrome's extensions page first, then Finder on top of it
        subprocess.run(["open", "-a", "Google Chrome", "chrome://extensions"], timeout=20)
    if act in ("install", "folder"):               # the "Chrome helper" folder, selected, ready to drag
        time.sleep(1.5 if act == "install" else 0)
        subprocess.run(["open", "-R", str(folder)], timeout=20)
        subprocess.run(["pbcopy"], input=str(folder), text=True, timeout=10)
    return {"folder": str(folder)}


ONEDRIVE = {"proc": None, "busy": False}
ONEDRIVE_MAP_SCHEMA = {"type": "object", "properties": {"pairs": {"type": "array", "items": {"type": "object", "properties": {
    "n": {"type": "integer"}, "subject": {"type": "string"}}, "required": ["n", "subject"]}}}, "required": ["pairs"]}


def onedrive_run(mode):
    """onedrive.py list | sync | login | explore, one at a time and never together with the IntoStudy sync
    (they share the Microsoft browser profile). Returns the exit code."""
    log = open(DATA / "onedrive.log", "a")
    ONEDRIVE["proc"] = proc = subprocess.Popen([str(VENV_PY), str(APP / "onedrive.py"), mode], cwd=str(APP),
                                               stdout=log, stderr=log, env=os.environ)
    code = proc.wait()
    log.close()
    return code


def onedrive_map():
    """Which subject each shared folder belongs to: by name ("Sociology/…"), otherwise asked once."""
    found, mapping = load("onedrive_found.json", {}), load("onedrive_map.json", {})
    subjects = list(CONFIG["subjects"])
    todo = [r for r in found.get("roots", []) if r["path"] not in mapping]
    if not todo or not subjects:
        return mapping
    ask = []
    for r in todo:
        low = r["path"].lower()
        hit = [n for n in subjects if n.lower() in low or (CONFIG["subjects"][n].get("short") or "~").lower() in low]
        if len(hit) == 1:
            mapping[r["path"]] = hit[0]
        else:
            ask.append(r)
    if ask:
        try:
            out = gemini([{"role": "user", "parts": [{"text":
                f"A student's subjects: {json.dumps(subjects, ensure_ascii=False)}. Teachers shared these OneDrive folders "
                "with them (path, who shared it). For each, which subject is it for? (Politics or international relations "
                "= Global Studies if there is no Politics subject; English or academic skills = EAP.) \"\" if none.\n"
                + "\n".join(f"{i}. {r['path']} (shared by {r.get('by', '')})" for i, r in enumerate(ask))}]}],
                None, ONEDRIVE_MAP_SCHEMA, 0.0, think=False)
            for x in out.get("pairs", []):
                if isinstance(x.get("n"), int) and 0 <= x["n"] < len(ask):
                    mapping[ask[x["n"]]["path"]] = x["subject"] if x.get("subject") in subjects else ""
        except GeminiError as e:
            print("onedrive map:", e)
    save("onedrive_map.json", mapping)
    return mapping


def onedrive_cycle(full=True):
    """Find what is shared, link it to subjects, download what is new; then sort and announce the new files."""
    if ONEDRIVE["busy"] or not VENV_PY.exists():
        return
    ONEDRIVE["busy"] = True
    try:
        if full or not load("onedrive_found.json", {}):
            if onedrive_run("list") or load("onedrive_status.json", {}).get("state") == "login":
                return
        onedrive_map()
        onedrive_run("sync")
        st = load("onedrive_status.json", {})
        if st.get("added") or st.get("updated"):
            reload_files()
            _quiet(auto_sort)
            _quiet(notify_files, st.get("added"), st.get("updated"))
    finally:
        ONEDRIVE["busy"] = False


def onedrive_soon():
    """Shortly after start (also right after an update): OneDrive for anyone who already picked their courses."""
    time.sleep(90)
    while sync_running():
        time.sleep(30)
    if CONFIG["subjects"] and (CONFIG.get("intostudy_courses") or load("courses.json", {})) \
            and load("onedrive_status.json", {}).get("state") != "login":
        _quiet(onedrive_cycle)


def api_onedrive(b):
    """OneDrive / Teams files shared with the student: connect (find + link + download), sign in, or record (dev)."""
    if not VENV_PY.exists():
        raise ValueError("This needs the browser part of Study Desk (it comes with the app).")
    if ONEDRIVE["busy"] or sync_running():
        raise ValueError("Study Desk is busy with IntoStudy or OneDrive right now. Try again in a minute.")
    act = b.get("action", "connect")
    if act in ("login", "explore"):
        ONEDRIVE["busy"] = True
        threading.Thread(target=lambda: (_quiet(onedrive_run, act), ONEDRIVE.update(busy=False),
                                         act == "login" and _quiet(onedrive_cycle)), daemon=True).start()
    else:
        threading.Thread(target=lambda: _quiet(onedrive_cycle), daemon=True).start()
    return {"started": True}


def onedrive_public():
    st, found, mapping = load("onedrive_status.json", {}), load("onedrive_found.json", {}), load("onedrive_map.json", {})
    roots = found.get("roots", [])
    return {**{k: st.get(k) for k in ("state", "step", "error", "last_ok")}, "running": ONEDRIVE["busy"],
            "roots": len(roots), "linked": sorted({v for v in mapping.values() if v}),
            "files": len(load("onedrive_manifest.json", {}))}


def api_compass_connect(b):
    return {"started": start_compass("login" if b.get("mode") == "login" else "fetch")}


def strip_html(s):
    import html
    s = re.sub(r"<(br|/p|/div|/li|/h\d)[^>]*>", "\n", str(s or ""), flags=re.I)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", html.unescape(re.sub(r"<[^>]+>", "", s)))).strip()


def _ts(v):
    """Compass dates: ISO strings (often UTC, with Z) or /Date(ms)/. Local datetime or None."""
    if not v:
        return None
    m = re.search(r"/Date\((\d+)", str(v))
    try:
        if m:
            return datetime.fromtimestamp(int(m.group(1)) / 1000)
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d.astimezone().replace(tzinfo=None) if d.tzinfo else d
    except ValueError:
        return None


def _rows(block):
    d = (block or {}).get("data")
    if isinstance(d, dict):
        d = d.get("data") or d.get("Data") or d.get("items") or []
    return d if isinstance(d, list) else []


def compass_subjects(activities):
    """Which of your subjects each Compass class name means (asked once, remembered in data/compass_map.json)."""
    known = load("compass_map.json", {})
    subjects = list(CONFIG["subjects"])
    todo = sorted({a for a in activities if a and a not in known})
    if todo and subjects:
        try:
            r = gemini([{"role": "user", "parts": [{"text":
                f"A student's school timetable names classes like this: {json.dumps(todo, ensure_ascii=False)}.\n"
                f"Their subjects are: {json.dumps(subjects, ensure_ascii=False)}. For each class name give the subject it "
                "is (codes like FSPH = Physics, FSCH = Chemistry, FEAP = EAP/English, FSBS/FBUS = Business, SOC = "
                "Sociology, GS = Global Studies help), or \"\" if it is none of them (tutorial, assembly, wellbeing)."}]}],
                None, COMPASS_MAP_SCHEMA, 0.0, think=False)
            for x in r.get("pairs", []):
                if x.get("activity") in todo:
                    known[x["activity"]] = x["subject"] if x.get("subject") in subjects else ""
        except GeminiError as e:
            print("compass map:", e)
        for a in todo:                       # anything still unknown: by name
            known.setdefault(a, next((s for s in subjects if s.lower() in a.lower()), ""))
        save("compass_map.json", known)
    return known


def _event_name(e):
    return str(e.get("subjectLongName") or e.get("title") or e.get("longTitleWithoutTime") or "").strip()


def compass_timetable(events):
    """One typical teaching week of classes from the calendar (the first week that has classes)."""
    rows = []
    for e in events:
        st, fi = _ts(e.get("start")), _ts(e.get("finish"))
        if not st or not fi or e.get("allDay") or st.isoweekday() > 5:
            continue
        rows.append((st, fi, e))
    if not rows:
        return []
    names = compass_subjects([_event_name(e) for _, _, e in rows])
    first = min(st for st, _, _ in rows)
    monday = (first - timedelta(days=first.weekday())).date()
    out, seen = [], set()
    for st, fi, e in sorted(rows, key=lambda x: x[0]):
        if not monday <= st.date() < monday + timedelta(days=7):
            continue
        subj = names.get(_event_name(e), "")
        if not subj:
            continue
        key = (st.isoweekday(), st.strftime("%H:%M"), subj)
        if key in seen:
            continue
        seen.add(key)
        parts = [x.strip() for x in str(e.get("longTitleWithoutTime") or "").split(" - ")]
        room = ", ".join(l.get("locationName", "") for l in e.get("locations") or [] if l.get("locationName")) or \
            (parts[2] if len(parts) > 2 else "")
        teacher = ", ".join(str(m.get("managerImportIdentifier") or "") for m in e.get("managers") or []
                            if m.get("managerImportIdentifier")) or (parts[3] if len(parts) > 3 else "")
        out.append({"day": st.isoweekday(), "start": st.strftime("%H:%M"), "end": fi.strftime("%H:%M"), "subject": subj,
                    "code": str(e.get("title") or (parts[1] if len(parts) > 1 else "")), "room": room, "teacher": teacher})
    merged = []                         # Compass lists one entry per hour: back-to-back lessons become one block
    for c in sorted(out, key=lambda c: (c["day"], c["start"])):
        prev = merged[-1] if merged else None
        if prev and prev["day"] == c["day"] and prev["subject"] == c["subject"] and prev["room"] == c["room"] and prev["end"] == c["start"]:
            prev["end"] = c["end"]
        else:
            merged.append(dict(c))
    return merged


def compass_apply():
    """Turn the last Compass read into the timetable, the week's topics, tasks and assessments."""
    raw = load("compass_raw.json", {})
    if not raw or not CONFIG["subjects"]:
        return                                  # nothing read yet, or no programme yet (setup runs this again)
    auto = load("compass_auto.json", {})
    # 1. timetable
    tt = compass_timetable(_rows(raw.get("calendar")))
    if tt and auto.get("timetable") and tt != auto["timetable"]:
        notify([{"id": f"timetable:{datetime.now():%Y%m%d%H%M}", "kind": "timetable", "link": "#/timetable",
                 "title": "Your timetable changed in Compass", "text": "Open Timetable to see this week's classes."}])
    if tt:
        with LOCK:
            cfg = json.loads(TOPICS_FILE.read_text())
            cfg["timetable"] = tt
            write_atomic(TOPICS_FILE, json.dumps(cfg, ensure_ascii=False, indent=2))
            load_config()
        auto["timetable"] = tt
    # 2. learning tasks: open ones with a due date become tasks; tests and assessments also go on the list of dates
    seen = set(auto.get("task_ids", []))
    tasks = _rows(raw.get("tasks"))
    names = compass_subjects([str(t.get("activityName") or t.get("subjectName") or "") for t in tasks])
    new, ass = [], []
    for t in tasks:
        mine = next((x for x in t.get("students") or [] if isinstance(x, dict) and x.get("dueDateTimestamp")), {})
        due = _ts(t.get("dueDateTimestamp") or t.get("dueDate") or mine.get("dueDateTimestamp"))
        name = strip_html(t.get("name") or t.get("title"))[:200]
        subj = names.get(str(t.get("activityName") or t.get("subjectName") or ""), "")
        if not name or not due or due.date() < date.today() - timedelta(days=1):
            continue
        if re.search(r"\b(test|exam|assessment|quiz|essay|presentation|coursework|portfolio|mock)\b", name, re.I):
            ass.append({"name": name, "date": due.date().isoformat(), "subject": subj})
        tid = str(t.get("id") or t.get("taskId") or name)
        if tid not in seen:
            seen.add(tid)
            new.append({"text": name, "subject": subj, "due": due.date().isoformat(), "due_kind": "date"})
    if new:
        with LOCK:
            have = load("tasks.json", [])
            for t in new:
                have.append({**t, "id": uuid.uuid4().hex[:8], "created": today(), "done": None, "source": "compass"})
            save("tasks.json", have)
            log_event("task_add", count=len(new), source="compass")
        notify([{"id": f"task:{t['text']}:{t['due']}", "kind": "task", "subject": t["subject"], "link": "#/today",
                 "title": f"{SHORT_SUBJECT.get(t['subject'], t['subject']) + ': ' if t['subject'] else ''}new task in Compass",
                 "text": f"{t['text']} (due {t['due']})"} for t in new])
    auto["task_ids"] = sorted(seen)[-500:]
    auto["tasks_added"] = auto.get("tasks_added", 0) + len(new)
    auto["assessments"] = ass
    # 3. class news from the last three weeks: which topics this week, homework, test dates (one AI read each)
    read = set(auto.get("news_ids", []))
    cutoff = datetime.now() - timedelta(days=21)
    classes = {str(e.get("activityId")): _event_name(e) for e in _rows(raw.get("calendar")) if e.get("activityId")}
    nid_of = lambda n: str(n.get("NewsItemId") or n.get("newsItemId") or n.get("Id") or "")
    found = {nid_of(n): ("", n) for n in _rows(raw.get("news"))[:40]}
    for aid, block in (raw.get("class_news") or {}).items():        # each class's own feed; a teacher's post often
        for n in _rows(block)[:20]:                                   # shows on the home feed too: the class copy wins
            found[nid_of(n)] = (classes.get(str(aid), ""), n)
    posts = sorted(found.values(), key=lambda x: str(x[1].get("PostDateTime") or ""))   # oldest first: newest wins
    done_now = 0
    for cls, n in posts:
        nid = str(n.get("NewsItemId") or n.get("newsItemId") or n.get("Id") or "")
        when = _ts(n.get("PostDateTime") or n.get("postDateTime"))
        if not nid or nid in read or (when and when < cutoff) or done_now >= 15:
            continue
        read.add(nid)
        done_now += 1
        body = strip_html(n.get("Content1")) + "\n" + strip_html(n.get("Content2"))
        who, title = str(n.get("UserName") or "").strip(), strip_html(n.get("Title"))
        subj = compass_subjects([cls]).get(cls, "") if cls else ""
        notify([{"id": f"post:{nid}", "ts": when.isoformat(timespec="seconds") if when else None,
                 "kind": "post" if cls else "news", "subject": subj, "link": "#/program" if cls else "#/today",
                 "title": (f"{who} posted in {SHORT_SUBJECT.get(subj, subj or cls)}" if cls else f"{who or 'Compass'}: school news"),
                 "text": (title or body.strip().split("\n")[0])[:160]}])
        if not cls:
            continue                            # school-wide news: only a notification, not a subject's plan
        text = "\n".join(x for x in (f"Class news: {cls}", title, f"from {who}" if who else "",
                                     f"posted {when:%A %d %B %Y}" if when else "", body) if x)
        if len(text) < 40:
            continue
        try:                                    # homework only from posts of the last 8 days; older ones are history
            r = api_compass({"text": text[:6000], "no_tasks": bool(when and when < datetime.now() - timedelta(days=8))})
            auto["news_read"] = auto.get("news_read", 0) + 1
            notify([{"id": f"task:{x}", "kind": "task", "subject": r["subject"], "link": "#/today",
                     "title": f"{SHORT_SUBJECT.get(r['subject'], r['subject'])}: homework from {who or 'Compass'}", "text": x}
                    for x in r.get("tasks_added", [])])
        except (ValueError, GeminiError, KeyError):
            pass
    auto["news_ids"] = sorted(read)[-500:]
    auto["applied"] = datetime.now().isoformat(timespec="seconds")
    save("compass_auto.json", auto)


def compass_loop():
    """Read Compass again every couple of hours, like the IntoStudy sync (only once it has been connected)."""
    time.sleep(60)
    while True:
        try:
            st = load("compass_status.json", {})
            last = st.get("last_ok") or ""
            due = not last or datetime.fromisoformat(last) < datetime.now() - timedelta(hours=COMPASS_EVERY_H)
            if last and due and st.get("via") != "extension" and st.get("state") not in ("login", "login_window", "blocked") \
                    and not compass_running():
                start_compass()
        except Exception as e:
            print("compass loop:", e)
        time.sleep(1200)


# ---------- standing: what you have studied, confirmed, and where you are behind ----------
CHECK_QUESTIONS = 8


def topic_standing(p, tid):
    """Status of one topic.
    studied   = you marked it done (after a Gemini session), or you worked on it here.
    confirmed = you proved it: 80%+ in the check test here or in Gemini's final test, or every subtopic passed.
    behind    = its class weeks are over and you have not studied it.  to_confirm = over and studied, not confirmed."""
    tp, ls, t = p["topics"].get(tid, {}), learn_summary(tid), TOPICS[tid]
    confirmed = tp.get("confirmed") or (ls["last_learned"] if ls["complete"] else None)
    studied = tp.get("studied") or (tp.get("last") if (tp.get("history") or ls["started"] or ls["passed"]) else None)
    status = "confirmed" if confirmed else "studied" if studied else "none"
    weeks = [w["week"] for w in CONFIG["subjects"][t["subject"]].get("plan", []) if tid in w["topics"]]
    ended = bool(weeks) and "term" in CONFIG and week_start(max(weeks)) + timedelta(days=7) <= date.today()
    checks = tp.get("checks", [])
    return {"status": status, "studied_on": studied, "confirmed_on": confirmed,
            "weeks": [min(weeks), max(weeks)] if weeks else None, "ended": ended,
            "behind": ended and status == "none", "to_confirm": ended and status == "studied",
            "check_best": max((c["pct"] for c in checks), default=None), "checks": len(checks)}


def standing():
    """Everything the Today page, the assistant and the Telegram brief need about where you stand."""
    p = progress()
    rows = [{"id": tid, "name": t["name"], "subject": t["subject"], **topic_standing(p, tid)} for tid, t in TOPICS.items()]
    ended = [r for r in rows if r["ended"]]
    return {"rows": rows,
            "behind": sorted((r for r in rows if r["behind"]), key=lambda r: r["weeks"][1]),
            "to_confirm": sorted((r for r in rows if r["to_confirm"]), key=lambda r: r["weeks"][1]),
            "confirmed": sum(r["status"] == "confirmed" for r in ended), "ended": len(ended)}


def api_topic_mark(b):
    """You tick a topic as studied (or untick it)."""
    tid = b["topic"]
    with LOCK:
        p = progress()
        tp = p["topics"].setdefault(tid, {"confidence": 0, "history": []})
        if b.get("studied", True):
            tp["studied"] = today()
            tp["last"] = today()
        else:
            tp.pop("studied", None)
        save("progress.json", p)
        log_event("mark_studied" if b.get("studied", True) else "unmark_studied", tid)
        return topic_standing(p, tid)


def api_check_test(b):
    """A short test over the whole topic: pass it to confirm you know the topic."""
    tid = b["topic"]
    n = CHECK_QUESTIONS
    prompt = (f"Write a {n + 2}-question multiple-choice check on the WHOLE topic, to confirm the student really knows it "
              "after studying it elsewhere. Spread the questions over all the main ideas and skills of the topic in the "
              "course materials; exam-style, most need working or real understanding, not recall of a definition. "
              "4 options each with exactly one correct answer (answer_index 0-3), distractors from common mistakes, and "
              "an explanation of the correct answer. Vary where the correct option sits. 'skill' = the idea being tested. "
              + TEST_STYLE.get(kind_of(tid), TEST_STYLE["science"]) + figure_rules(tid))
    out = gemini([{"role": "user", "parts": [{"text": prompt}]}], learn_system(tid), TEST_SCHEMA, 0.8)
    qs = verify_mcqs(tid, clean_mcqs(out.get("questions", [])))[:n]
    if len(qs) < 4:
        raise GeminiError("Gemini didn't return enough usable questions. Try again.")
    with LOCK:
        log_event("check_generate", tid, count=len(qs))
    return {"questions": qs, "pass_mark": PASS_MARK}


def api_check_submit(b):
    tid = b["topic"]
    total, correct = int(b["total"]), int(b["correct"])
    if total <= 0 or not 0 <= correct <= total:
        raise ValueError("bad score")
    pct = round(100 * correct / total)
    with LOCK:
        p = progress()
        record_study(p, tid, correct, total)
        tp = p["topics"][tid]
        tp.setdefault("checks", []).append({"date": today(), "pct": pct, "correct": correct, "total": total})
        tp.setdefault("studied", today())
        newly = pct >= PASS_MARK and not tp.get("confirmed")
        if pct >= PASS_MARK:
            tp["confirmed"] = tp.get("confirmed") or today()
        save("progress.json", p)
        log_event("check", tid, score=correct, max=total, pct=pct)
        return {"pct": pct, "passed": pct >= PASS_MARK, "newly_confirmed": newly, "pass_mark": PASS_MARK,
                **topic_standing(p, tid)}


def upcoming_assessments():
    """Dated things to prepare for: exams (Settings), assessments in the course plans, and what Compass listed."""
    tdy, out = today(), []
    for e in progress()["exams"]:
        out.append({"date": e["date"], "name": e["name"], "subject": e.get("subject", ""), "kind": "exam"})
    for name, sub in CONFIG["subjects"].items():
        for w in sub.get("plan", []):
            if w.get("assessment") and "term" in CONFIG:
                out.append({"date": week_start(w["week"]).isoformat(), "week": w["week"], "name": w["assessment"],
                            "subject": name, "kind": "plan"})
    for name, c in load("compass.json", {}).items():
        for a in c.get("assessments", []):
            out.append({"date": a["date"], "name": a["name"], "subject": name, "kind": "compass"})
    known = {(a["date"], a["name"]) for a in out}
    for a in load("compass_auto.json", {}).get("assessments", []):      # tests and deadlines from Compass tasks
        if (a["date"], a["name"]) not in known:
            out.append({"date": a["date"], "name": a["name"], "subject": a.get("subject", ""), "kind": "compass"})
    # a plan assessment is "in the week of": keep it until that week is over
    keep = lambda a: (date.fromisoformat(a["date"]) + timedelta(days=6 if a["kind"] == "plan" else 0)).isoformat() >= tdy
    return sorted((a for a in out if keep(a)), key=lambda a: a["date"])


def brief_text():
    """Plain-text status for Telegram (Russian labels, topic names as in the course)."""
    tdy = date.today()
    s, prog = standing(), api_program(None)
    days = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    short = lambda name: SHORT_SUBJECT.get(name, name)
    in_break = lambda d: any(b["start"] <= d.isoformat() <= b["end"] for b in CONFIG.get("breaks", []))

    def day_topic(subject, d):
        sub = next((x for x in prog.get("subjects", []) if x["name"] == subject), None)
        if not sub:
            return ""
        for l in (sub.get("compass") or {}).get("lessons", []):
            if l["date"] == d.isoformat() and l["topic"] in TOPICS:
                name = TOPICS[l["topic"]]["name"]
                same = l.get("what", "").lower().replace(" and ", " & ") in name.lower()
                return name if not l.get("what") or same else f"{name} ({l['what']})"
        w = next((w for w in sub["weeks"] if w["week"] == week_of(d)), None)
        return ", ".join(TOPICS[i]["name"] for i in w["topics"] if i in TOPICS) if w else ""

    def classes(d):
        if in_break(d):
            return ["пар нет (каникулы)"]
        cs = sorted((c for c in CONFIG.get("timetable", []) if c["day"] == d.isoweekday()), key=lambda c: c["start"])
        return [f"{c['start']}–{c['end']} {short(c['subject'])}, {c['room']}: {day_topic(c['subject'], d) or 'тема не указана'}"
                for c in cs] or ["пар нет"]

    nxt = tdy + timedelta(days=1)
    while not [c for c in CONFIG.get("timetable", []) if c["day"] == nxt.isoweekday()] and (nxt - tdy).days < 7:
        nxt += timedelta(days=1)
    L = [f"Study Desk · {days[tdy.weekday()]} {tdy.day}.{tdy.month:02d}"
         + (f" · неделя {prog['week']} ({prog['term']})" if prog.get("week") else "")]
    L += ["", "СЕГОДНЯ"] + [f"• {x}" for x in classes(tdy)]
    L += ["", f"ДАЛЬШЕ ({days[nxt.weekday()]} {nxt.day}.{nxt.month:02d})"] + [f"• {x}" for x in classes(nxt)]
    tasks = open_tasks()
    if tasks:
        L += ["", f"ЗАДАЧИ ({len(tasks)})"] + [f"• {task_line(t)}" for t in tasks[:10]] + \
             ([f"• и ещё {len(tasks) - 10}"] if len(tasks) > 10 else [])
    beh = s["behind"][::-1]                                # most recent first
    L += ["", f"ОТСТАЁШЬ: пройдено в классе, не изучено ({len(beh)})"] + \
         ([f"• {short(r['subject'])}: {r['name']} (неделя {r['weeks'][1]})" for r in beh[:8]] or ["• ничего"]) + \
         ([f"• и ещё {len(beh) - 8} более старых"] if len(beh) > 8 else [])
    L += ["", f"ИЗУЧЕНО, НЕ ПОДТВЕРЖДЕНО ({len(s['to_confirm'])})"] + \
         ([f"• {short(r['subject'])}: {r['name']}" for r in s["to_confirm"][:8]] or ["• ничего"])
    L += ["", f"ПОДТВЕРЖДЕНО: {s['confirmed']} из {s['ended']} законченных тем"]
    cutoff = (datetime.now() - timedelta(hours=26)).isoformat(timespec="seconds")
    new = [x for x in load("sync_log.json", []) if x.get("date", "") >= cutoff]
    if new:
        L += ["", new_files_text(new, "НОВЫЕ ФАЙЛЫ за сутки")]
    st = load("sync_status.json", {})
    if st.get("signed_in") is False or st.get("state") == "login":
        L += ["", "⚠ IntoStudy просит войти заново: открой Study Desk и нажми Connect IntoStudy."]
    elif st.get("last_ok") and st["last_ok"] < (datetime.now() - timedelta(hours=30)).isoformat():
        L += ["", f"⚠ IntoStudy не проверялся с {st['last_ok'][:16].replace('T', ' ')}."]
    ass = [a for a in upcoming_assessments() if (date.fromisoformat(a["date"]) - tdy).days <= 21 and a["kind"] != "exam"]
    if ass:
        L += ["", "ОЦЕНИВАНИЯ СКОРО"] + [f"• {short(a['subject'])}: {a['name']} ("
                                         + (f"неделя с {a['date'][8:]}.{a['date'][5:7]}" if a["kind"] == "plan" else f"{a['date'][8:]}.{a['date'][5:7]}") + ")" for a in ass]
    exams = [e for e in compute_stats()["exams"] if e["days"] >= 0]
    if exams:
        L += ["", "ЭКЗАМЕНЫ"] + [f"• {e['name']}: через {e['days']} дн., подтверждено {e['ready']} из {e['in_scope']} тем"
                                 + (f", отстаёшь по {e['behind']}" if e.get("behind") else "") for e in exams]
    pasted = load("compass.json", {})
    missing = [x["name"] for x in prog.get("subjects", []) if x["name"] in pasted and x["source"] != "compass"]
    if prog.get("week") and tdy.isoweekday() <= 5 and missing and not compass_connected():
        L += ["", f"{', '.join(short(m) for m in missing)}: расписание этой недели из Compass ещё не добавлено. "
                  "Пришли сюда текст поста, я сохраню."]
    return "\n".join(L)


def api_brief(_):
    return {"text": brief_text()}



# ---------- review notes: one detailed sheet per topic, to recall everything at a glance ----------
REVIEWS = DATA / "review"
REVIEW_BUSY = set()
REVIEW_CHECK = {"type": "object", "properties": {"ok": {"type": "boolean"}, "problems": {"type": "array", "items": {"type": "string"}}},
                "required": ["ok", "problems"]}


def review_load(tid):
    if cloud.on():
        return cloud.get(f"review/{tid}.json", None) or {}
    try:
        return json.loads((REVIEWS / f"{tid}.json").read_text())
    except (OSError, ValueError):
        return {}


def review_save(tid, obj):
    if cloud.on():
        return cloud.put(f"review/{tid}.json", obj)
    REVIEWS.mkdir(exist_ok=True)
    write_atomic(REVIEWS / f"{tid}.json", json.dumps(obj, ensure_ascii=False, indent=1))


def review_sig(tid):
    return sorted(topic_files(tid, shared=False))


def make_review(tid):
    """Write the review sheet (Markdown for the app, plain text for Telegram), checked once against the files."""
    eap = kind_of(tid) in ("english", "essay")
    essay = kind_of(tid) == "essay"
    prompt = (
        "Write REVIEW NOTES for the WHOLE topic, so the student can recall everything in one look before a class test or "
        "the exam and not re-read their chat history. Use ONLY the course materials: their notation, formulas, methods "
        "and exam-question style. Markdown, exactly these sections:\n"
        "## In one minute\n3 to 5 bullets: what this topic is about and what you must be able to do.\n"
        "## Key ideas and definitions\nEach term in **bold** with its meaning in one line, in the wording an examiner accepts.\n"
        + ("## Theories, models and evidence\nEach theory or model with who it comes from, the idea in one or two lines, "
           "one example or case study, and one strength and one limitation.\n" if essay else
           "## Useful language and structures\nThe phrases, sentence frames and structures taught, with one example each.\n"
           if eap else
           "## Formulas\nA table: Formula | Symbols and units | When to use it. Every formula must be in the materials or "
           "the formula sheet; say which ones are given in the exam and which must be memorised if the materials say.\n")
        + "## How to do the typical exam questions\nFor each question type in the materials: the steps as a numbered "
        "list, then " + ("one short model paragraph that would score well" if essay else
                         "one short worked example with real numbers" + (" or a real sentence" if eap else " and units")) + ".\n"
        "## Mistakes that lose marks\nBullets, each with the fix.\n"
        "## Check yourself\n5 short questions covering the topic, then 'Answers:' with the 5 answers.\n"
        "Rules: complete but compact (700 to 1100 words), no introduction or closing words, short lines, inline maths "
        "in $...$ and display maths in $$...$$ alone on its own line. Work every example through and check its numbers.")
    sys_ = learn_system(tid)
    md = gemini([{"role": "user", "parts": [{"text": prompt}]}], sys_, temperature=0.3)
    try:    # one independent look for wrong formulas, numbers or missing parts; fix once if needed
        c = gemini([{"role": "user", "parts": [{"text":
            "Check these review notes against the course materials. List only real problems: a wrong or invented "
            "formula, a wrong number or unit in an example, a wrong definition, or a main part of the topic that is "
            "missing. ok = true if there are none.\n\nNOTES:\n" + md}]}], sys_, REVIEW_CHECK, 0.1)
        if not c.get("ok") and c.get("problems"):
            md = gemini([{"role": "user", "parts": [{"text": prompt + "\n\nA checker found these problems in your last "
                         "draft; fix them all and return the full corrected notes:\n- " + "\n- ".join(c["problems"][:12])
                         + "\n\nLAST DRAFT:\n" + md}]}], sys_, temperature=0.2)
    except GeminiError:
        pass
    md = tidy_math(md.strip())
    return {"md": md, "tg": make_tg(md), "made": today(), "files": review_sig(tid)}


def make_tg(md):
    """The same notes as one plain-text Telegram message (Telegram shows no LaTeX or tables)."""
    tg = gemini([{"role": "user", "parts": [{"text":
        "Rewrite these review notes as ONE compact Telegram message in plain text, HARD LIMIT 3000 characters. No "
        "Markdown, no tables, no LaTeX: write formulas with Unicode (v², ½mv², √, θ, ω, Δ, ×, ÷, →, ≈). Section titles "
        "in CAPITALS on their own line, items start with '• ', numbered steps as '1) '. Keep: every formula with a "
        "short key to its symbols (one line per formula, no 'when to use' column), the definitions shortened to a few "
        "words each, the steps for each question type with a one-line example, the mistakes (one short line each) and "
        "the 5 check questions with one-line answers. Cut wording, never cut a section.\n\nNOTES:\n" + md}]}],
        "You convert study notes to clean, compact plain text.", temperature=0.2, think=False).strip()
    if len(tg) > 3800:                                   # never cut in the middle of a line
        tg = tg[:3800].rsplit("\n", 1)[0] + "\n… (полная версия в Study Desk)"
    return tg



def build_review(tid):
    """Generate and store; one at a time per topic. Returns the stored review (old one if generation fails)."""
    if tid in REVIEW_BUSY:
        return review_load(tid)
    REVIEW_BUSY.add(tid)
    try:
        new = make_review(tid)
        with LOCK:
            r = review_load(tid)
            r.update(new)
            review_save(tid, r)
            log_event("review_made", tid)
        return r
    finally:
        REVIEW_BUSY.discard(tid)


def review_state(tid):
    r = review_load(tid)
    return {"md": r.get("md"), "tg": r.get("tg"), "made": r.get("made"), "mine": r.get("mine", ""),
            "stale": bool(r.get("md")) and not HOSTED and r.get("files") != review_sig(tid), "generating": tid in REVIEW_BUSY}


def api_review(b):
    """Review notes for a topic. Writes them the first time (about a minute); refresh=true rewrites."""
    tid = b["topic"]
    r = review_load(tid)
    if (not r.get("md") or b.get("refresh")) and tid not in REVIEW_BUSY:
        if b.get("wait", True):
            build_review(tid)
        else:
            threading.Thread(target=lambda: _quiet(build_review, tid), daemon=True).start()
            time.sleep(0.2)
    return review_state(tid)


def _quiet(fn, *a):
    try:
        fn(*a)
    except Exception as e:
        print("background:", type(e).__name__, e)


def api_review_mine(b):
    """Your own notes for a topic (for example the summary Gemini gives at the end of a session)."""
    tid = b["topic"]
    with LOCK:
        r = review_load(tid)
        r["mine"] = str(b.get("text", ""))[:20000]
        review_save(tid, r)
    return {"ok": True}


def review_warmup():
    """In the background, write review notes for the topics already taught (newest first), a few per run."""
    time.sleep(90)
    while True:
        try:
            todo = sorted((t for t in TOPICS.values() if t["start"] <= today()), key=lambda t: t["start"], reverse=True)
            for t in todo:
                r = review_load(t["id"])
                if api_key() and (not r.get("md") or r.get("files") != review_sig(t["id"])) and topic_files(t["id"], shared=False):
                    build_review(t["id"])
                    time.sleep(20)
        except GeminiError as e:            # over the free limit: try again later
            print("review warm-up paused:", e)
        except Exception as e:
            print("review warm-up:", type(e).__name__, e)
        time.sleep(3600)



# ---------- tasks: small things from teachers that must not be forgotten ----------
TASK_SCHEMA = {"type": "object", "properties": {"tasks": {"type": "array", "items": {"type": "object", "properties": {
    "text": {"type": "string"}, "subject": {"type": "string"}, "due": {"type": "string"}},
    "required": ["text", "subject", "due"]}}}, "required": ["tasks"]}


def next_class(subject, start=None):
    """(date, 'HH:MM') of the next class of a subject that has not begun yet, or None."""
    now = datetime.now()
    d = start or date.today()
    for _ in range(40):
        if not any(b["start"] <= d.isoformat() <= b["end"] for b in CONFIG.get("breaks", [])):
            cs = sorted(c["start"] for c in CONFIG.get("timetable", []) if c["day"] == d.isoweekday() and c["subject"] == subject)
            cs = [c for c in cs if d > now.date() or c > now.strftime("%H:%M")]
            if cs:
                return d, cs[0]
        d += timedelta(days=1)
    return None


def parse_tasks(text, subject=None):
    """Turn a free-text note ('по физике доделать Hwk10 к пятнице') into tasks with a subject and a date."""
    tdy = date.today()
    tt = "; ".join(f"{['', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][c['day']]} {c['start']} {c['subject']}"
                   for c in CONFIG.get("timetable", []))
    try:
        r = gemini([{"role": "user", "parts": [{"text":
            f"Today is {tdy.isoformat()} ({tdy.strftime('%A')}). A student wrote a note of things to do so as not to "
            f"forget (maybe in Russian). Split it into separate tasks. For each: text = the task itself, short and "
            f"clear, in the language he wrote it, keeping names like Hwk10 exactly; subject = one of "
            f"{list(CONFIG['subjects'])} if it is clear from the note, else \"\"; due = the date as YYYY-MM-DD only if "
            f"he gave a day or date ('к пятнице', 'до 14-го', 'завтра'), else \"\" (\"на следующий урок\" = \"\").\n"
            f"His weekly classes: {tt}.\n\nNOTE:\n{text[:2000]}"}]}], None, TASK_SCHEMA, 0.0, think=False)
        tasks = [t for t in r.get("tasks", []) if str(t.get("text", "")).strip()]
    except GeminiError:
        tasks = []
    if not tasks:
        tasks = [{"text": text.strip()[:300], "subject": "", "due": ""}]
    out = []
    for t in tasks[:10]:
        subj = t.get("subject") if t.get("subject") in CONFIG["subjects"] else (subject or "")
        due = t["due"] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(t.get("due", ""))) else ""
        kind = "date" if due else ""
        if not due and subj and next_class(subj):
            due, kind = next_class(subj)[0].isoformat(), "class"       # by the next class of that subject
        out.append({"text": str(t["text"]).strip()[:300], "subject": subj, "due": due or None, "due_kind": kind or None})
    return out


def add_tasks(text, source, subject=None):
    new = parse_tasks(text, subject)
    with LOCK:
        tasks = load("tasks.json", [])
        norm = lambda x: re.sub(r"\W+", " ", x.lower()).strip()
        have = {norm(t["text"]) for t in tasks if not t.get("done") or t["done"] >= (date.today() - timedelta(days=14)).isoformat()}
        added = []
        for t in new:
            if norm(t["text"]) in have:
                continue
            t.update(id=uuid.uuid4().hex[:8], created=today(), done=None, source=source)
            tasks.append(t)
            added.append(t)
            have.add(norm(t["text"]))
        save("tasks.json", tasks)
        if added:
            log_event("task_add", count=len(added), source=source)
    return added


def open_tasks():
    key = lambda t: (t.get("due") or "9999", t["created"])
    return sorted((t for t in load("tasks.json", []) if not t.get("done")), key=key)


def task_when(t, tdy=None):
    """Short Russian label: when the task is due."""
    tdy = tdy or date.today()
    if not t.get("due"):
        return "без срока"
    d = date.fromisoformat(t["due"])
    day = f"{['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'][d.weekday()]} {d.day}.{d.month:02d}"
    if d < tdy:
        return f"ПРОСРОЧЕНО, было {'к паре ' if t.get('due_kind') == 'class' else 'до '}{day}"
    return ("к паре " if t.get("due_kind") == "class" else "до ") + ("сегодня" if d == tdy else "завтра" if d == tdy + timedelta(days=1) else day)


def task_line(t):
    return f"{SHORT_SUBJECT.get(t['subject'], t['subject']) + ': ' if t.get('subject') else ''}{t['text']} ({task_when(t)})"


def api_tasks(b):
    """The app's task list: add (free text), done / undo, delete."""
    act = b.get("action", "list")
    if act == "add":
        if not str(b.get("text", "")).strip():
            raise ValueError("Write the task first.")
        add_tasks(str(b["text"]), "app")
    elif act in ("done", "undo", "delete"):
        with LOCK:
            tasks = load("tasks.json", [])
            for t in tasks:
                if t["id"] == b.get("id"):
                    t["done"] = today() if act == "done" else None
            if act == "delete":
                tasks = [t for t in tasks if t["id"] != b.get("id")]
            save("tasks.json", tasks)
    return {"tasks": tasks_for_app()}


def tasks_for_app():
    recent = (date.today() - timedelta(days=2)).isoformat()
    done = [t for t in load("tasks.json", []) if t.get("done") and t["done"] >= recent]
    return [{**t, "when": task_when(t), "overdue": bool(t.get("due")) and t["due"] < today() and not t.get("done")}
            for t in open_tasks() + done]


def match_tasks(q):
    """Open tasks a free-text phrase means ('сделал домашку по физике')."""
    tasks = open_tasks()
    if not tasks:
        return []
    low = str(q or "").lower()
    if re.search(r"\b(все|всё|all)\b", low):
        return tasks
    listing = "\n".join(f"{t['id']}: {task_line(t)}" for t in tasks)
    try:
        r = gemini([{"role": "user", "parts": [{"text":
            f"A student says which of his to-do tasks he has finished (maybe in Russian): \"{str(q)[:300]}\".\n"
            f"Return the ids of the tasks he means; none if it is unclear.\n\n{listing}"}]}], None, MATCH_SCHEMA, 0.0, think=False)
        ids = set(r.get("ids", []))
    except GeminiError:
        ids = {t["id"] for t in tasks if any(w in t["text"].lower() for w in re.findall(r"\w{4,}", low))}
    return [t for t in tasks if t["id"] in ids]


def remind_text():
    """Evening reminder: what must be ready for tomorrow's classes. Empty when there is nothing."""
    tmr = date.today() + timedelta(days=1)
    due = [t for t in open_tasks() if t.get("due") and t["due"] <= tmr.isoformat()]
    if not due:
        return ""
    classes = {c["subject"]: c for c in sorted(CONFIG.get("timetable", []), key=lambda c: c["start"], reverse=True)
               if c["day"] == tmr.isoweekday()}
    days = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    L = [f"Напоминание на завтра ({days[tmr.weekday()]} {tmr.day}.{tmr.month:02d})"]
    for subj in list(classes) + [""]:
        mine = [t for t in due if (t.get("subject") or "") == subj or (subj == "" and t.get("subject") not in classes)]
        if not mine:
            continue
        c = classes.get(subj)
        L.append("")
        L.append(f"{SHORT_SUBJECT.get(subj, subj)} в {c['start']}, {c['room']} — нужно было:" if c else "Ещё не сделано:")
        L += [f"• {t['text']}" + (f" ({task_when(t)})" if not c else "") for t in mine]
    L += ["", "Сделал — напиши «сделал …», я сниму."]
    return "\n".join(L)


# ---------- for the Telegram agent (Hermes): plain-text answers ----------
MATCH_SCHEMA = {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "string"}}}, "required": ["ids"]}


def find_topics(q):
    """Topic ids that a free-text request means ('статика', 'moments', 'trig', 'p-statics')."""
    q = str(q or "").strip()
    low = q.lower()
    if low in TOPICS:
        return [low]
    exact = [tid for tid, t in TOPICS.items() if low and (low == t["name"].lower() or low in t["name"].lower())]
    if len(exact) == 1:
        return exact
    listing = "\n".join(f"{tid}: {t['subject']}: {t['name']}" for tid, t in TOPICS.items())
    try:
        r = gemini([{"role": "user", "parts": [{"text":
            f"A student asks about a topic in their own words (maybe in Russian): \"{q[:300]}\".\nWhich of these course "
            f"topics do they mean? Return the id of the one best match; two or three ids only if it is truly unclear; "
            f"none if nothing fits.\n\n{listing}"}]}], None, MATCH_SCHEMA, 0.0, think=False)
        return [i for i in r.get("ids", []) if i in TOPICS][:3]
    except GeminiError:
        return exact[:3]


def topic_line(r):
    wk = f"wk {r['weeks'][0]}" + (f"-{r['weeks'][1]}" if r["weeks"][1] != r["weeks"][0] else "") if r.get("weeks") else "all term"
    st = ("подтверждена" if r["status"] == "confirmed" else "изучена, не подтверждена" if r["status"] == "studied"
          else "ОТСТАЁШЬ" if r["behind"] else "не изучена")
    return f"{r['name']} ({wk}): {st}"


def api_agent(b):
    """One entry point for the Telegram agent. Every answer is ready-to-send text."""
    act = b.get("action")
    if act == "status":
        return {"text": brief_text()}
    if act == "new":                                   # only what appeared since the last call (for alerts)
        with LOCK:
            st = load("agent_state.json", {})
            since = st.get("announced") or (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
            new = [x for x in load("sync_log.json", []) if x.get("date", "") > since]
            st["announced"] = max([x["date"] for x in new] + [since])
            save("agent_state.json", st)
        return {"text": new_files_text(new, "Новые файлы на IntoStudy") if new else ""}
    if act == "topics":
        rows = standing()["rows"]
        subj = str(b.get("subject") or "").lower()
        out = []
        for name in CONFIG["subjects"]:
            if subj and subj not in name.lower() and subj not in SHORT_SUBJECT.get(name, name).lower():
                continue
            out.append(f"{name.upper()}")
            out += [f"• {topic_line(r)}" for r in rows if r["subject"] == name]
            out.append("")
        return {"text": "\n".join(out).strip() or "Нет такого предмета. Предметы: " + ", ".join(CONFIG["subjects"])}
    if act in ("review", "mark"):
        ids = find_topics(b.get("topic"))
        if len(ids) != 1:
            names = [TOPICS[i]["name"] for i in ids] or [t["name"] for t in TOPICS.values()]
            return {"text": ("Уточни тему: " if ids else "Не понял, какая тема. Темы: ") + "; ".join(names)}
        tid = ids[0]
        label = f"{TOPICS[tid]['subject']}: {TOPICS[tid]['name']}"
        if act == "mark":
            st = api_topic_mark({"topic": tid, "studied": b.get("studied", True)})
            return {"text": f"{label}: " + ("отмечена как изученная. Подтверди её проверкой в Study Desk."
                                             if st["status"] != "none" else "отметка снята.")}
        r = api_review({"topic": tid})                 # the first time this writes the notes (about a minute)
        if not r["tg"]:
            return {"text": f"Конспект по теме «{label}» ещё пишется. Спроси ещё раз через минуту."}
        st = topic_standing(progress(), tid)
        mine = f"\n\nТВОИ ЗАМЕТКИ\n{r['mine'][:1200]}" if r["mine"].strip() else ""
        return {"text": f"REVIEW · {label}\nСтатус: {topic_line({**st, 'name': TOPICS[tid]['name']}).split(': ', 1)[1]}\n\n{r['tg']}{mine}"}
    if act == "compass":
        r = api_compass({"text": b.get("text", "")})
        days = "; ".join(f"{l['date'][5:]} {TOPICS[l['topic']]['name']}" for l in r.get("lessons", []))
        return {"text": f"Сохранил расписание из Compass: {r['subject']}, неделя {r['week']}. "
                        + (f"По дням: {days}. " if days else "") + (r.get("summary") or "")
                        + ("\nДобавил задачи:\n" + "\n".join(f"• {x}" for x in r["tasks_added"]) if r.get("tasks_added") else "")}
    if act == "task_add":
        added = add_tasks(str(b.get("text", "")), "telegram")
        if not added:
            return {"text": "Такая задача уже записана."}
        return {"text": "Записал:\n" + "\n".join(f"• {task_line(t)}" for t in added)
                        + "\nНапомню вечером накануне и в утренней сводке. Видно и в Study Desk (Today)."}
    if act == "task_list":
        tasks = open_tasks()
        return {"text": (f"ЗАДАЧИ ({len(tasks)})\n" + "\n".join(f"• {task_line(t)}" for t in tasks)) if tasks else "Открытых задач нет."}
    if act == "task_done":
        hit = match_tasks(b.get("text"))
        if not hit:
            tasks = open_tasks()
            return {"text": "Не понял, какую задачу снять. Открытые:\n" + "\n".join(f"• {task_line(t)}" for t in tasks)
                            if tasks else "Открытых задач нет."}
        with LOCK:
            tasks = load("tasks.json", [])
            for t in tasks:
                if t["id"] in {h["id"] for h in hit}:
                    t["done"] = today()
            save("tasks.json", tasks)
        return {"text": "Готово, снял:\n" + "\n".join(f"• {t['text']}" for t in hit)
                        + (f"\nОсталось задач: {len(open_tasks())}." if open_tasks() else "\nЗадач больше нет.")}
    if act == "remind":
        return {"text": remind_text()}
    if act == "ask":
        q = str(b.get("question", "")).strip()
        if not q:
            raise ValueError("empty question")
        ids = find_topics(q) if len(q) < 400 else []
        system = (STYLE + "\nYou answer the student's question in a Telegram chat. Plain text only: no Markdown tables, "
                  "no LaTeX (write formulas with Unicode: v², ½mv², √, θ). Be direct and complete but short (under 1800 "
                  "characters unless they ask for detail). Base facts about their progress, plan and files ONLY on the "
                  "data below; if it isn't there, say so.\n\n" + assistant_context(ids[0] if len(ids) == 1 else None))
        return {"text": gemini([{"role": "user", "parts": [{"text": q}]}], system, temperature=0.4).strip()}
    raise ValueError("unknown action")


def new_files_text(new, title):
    by = defaultdict(list)
    for x in new:
        tid = topic_for_path(x["path"])
        by[f"{SHORT_SUBJECT.get(x['course'], x['course'])}: {TOPICS[tid]['name'] if tid else 'без темы'}"].append(x["path"].rsplit("/", 1)[-1])
    return "\n".join([f"{title} ({len(new)})"] + [f"• {k}: {', '.join(v[:4])}{' …' if len(v) > 4 else ''}" for k, v in list(by.items())[:10]])


# ---------- file viewer: open course files inside Study Desk, fill in worksheets ----------
# Your filled-in copy of a Word file is saved as HTML in data/work/<hash>.json; the original is never changed.
def _work_name(rel):
    import hashlib
    return f"work/{hashlib.sha1(rel.encode()).hexdigest()[:16]}.json"


def _known(rel):
    if rel not in ALL_FILES:
        raise ValueError("unknown file")
    return rel


def api_file_view(b):
    rel = _known(b["path"])
    name = rel.removesuffix(".txt").rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    out = {"path": rel, "name": name, "ext": ext, "folder": rel.rsplit("/", 1)[0] if "/" in rel else "",
           "original": bool(original_for(rel)), "topic": topic_for_path(rel.removesuffix(".txt")),
           "work": load(_work_name(rel), None)}
    if ext not in ("pdf", "docx", "png", "jpg", "jpeg", "gif", "webp"):
        out["text"] = (TEXT / rel).read_text(errors="ignore")[:300_000]
    return out


def api_work_save(b):
    rel = _known(b["path"])
    html = b.get("html")
    (DATA / "work").mkdir(exist_ok=True)
    if not html:                                  # "Back to the original": forget your copy
        with LOCK:
            (DATA / _work_name(rel)).unlink(missing_ok=True)
        return {"ok": True, "work": None}
    if len(html) > 8_000_000:
        raise ValueError("too large")
    w = {"path": rel, "html": html, "updated": datetime.now().isoformat(timespec="seconds")}
    with LOCK:
        save(_work_name(rel), w)
    return {"ok": True, "work": {"updated": w["updated"]}}


def api_file_open(b):
    f = original_for(_known(b["path"]))
    if not f:
        raise ValueError("no original file")
    subprocess.run(["open", str(f)], timeout=20)      # Word / PowerPoint / Preview on this Mac
    return {"ok": True}


def work_docx(rel):
    """Your filled-in copy as a Word file (macOS textutil turns the HTML into .docx)."""
    w = load(_work_name(rel), None) if rel in ALL_FILES else None
    if not w:
        return None
    with tempfile.TemporaryDirectory() as d:
        src, dst = Path(d) / "in.html", Path(d) / "out.docx"
        src.write_text(f"<html><head><meta charset='utf-8'></head><body>{w['html']}</body></html>")
        subprocess.run(["textutil", "-convert", "docx", "-output", str(dst), str(src)], timeout=60, check=True)
        return dst.read_bytes()


# ---------- class recordings: record a lesson, Whisper (offline) writes the transcript, Gemini the notes ----------
# data/classes/<id>.json + the audio next to it. Notes are split by Learn subtopic and shown in its My notes tab.
CLASS_DIR = DATA / "classes"
CLASS_DIR.mkdir(exist_ok=True)
_VIBE = Path.home() / "Library/Application Support/github.com.thewh1teagle.vibe"   # the Vibe app already has the models
WHISPER = shutil.which("whisper-cli") or "/opt/homebrew/bin/whisper-cli"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
WHISPER_MODEL = next((Path(x) for x in (os.environ.get("WHISPER_MODEL") or read_env().get("WHISPER_MODEL"),
                                         DATA / "models/ggml-large-v3-turbo.bin", _VIBE / "ggml-large-v3-turbo.bin")
                      if x and Path(x).is_file()), None)
VAD_MODEL = next((x for x in (DATA / "models/ggml-silero-v6.2.0.bin", _VIBE / "ggml-silero-v6.2.0.bin") if x.is_file()), None)
AUDIO_EXT = {".webm", ".m4a", ".mp4", ".mp3", ".wav", ".aac", ".ogg", ".opus", ".flac", ".caf", ".aiff", ".aif", ".3gp", ".amr"}
CLASS_QUEUE = threading.Semaphore(1)          # one transcription at a time; Whisper already uses the whole GPU
CLASS_SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string"}, "summary": {"type": "string"},
    "sections": {"type": "array", "items": {"type": "object", "properties": {
        "sub": {"type": "string"}, "notes": {"type": "string"}}, "required": ["sub", "notes"]}},
    "announcements": {"type": "array", "items": {"type": "object", "properties": {
        "kind": {"type": "string", "enum": ["exam", "deadline", "homework", "other"]},
        "text": {"type": "string"}, "date": {"type": "string"}}, "required": ["kind", "text", "date"]}}},
    "required": ["title", "summary", "sections", "announcements"]}


def class_load(cid):
    if not re.fullmatch(r"[0-9a-f]{12}", str(cid)):
        raise ValueError("unknown recording")
    if HOSTED:
        c = cloud.get(f"classes/{cid}.json", None)
        if not c:
            raise ValueError("unknown recording")
        return c
    return json.loads((CLASS_DIR / f"{cid}.json").read_text())


def class_save(c):
    write_atomic(CLASS_DIR / f"{c['id']}.json", json.dumps(c, ensure_ascii=False, indent=1))
    if cloud.on() and c.get("status") not in ("recording", "uploading"):    # not on every audio piece
        try:
            cloud.put(f"classes/{c['id']}.json", c, force=True)
        except Exception as e:
            print(f"class {c['id']}: copy to Supabase failed ({e}); it goes up with the next change")


def class_set(cid, **kw):
    with LOCK:
        c = class_load(cid)
        c.update(kw)
        class_save(c)
        return c


def class_new(topic, ext, status, day=None, source="mic"):
    if topic not in TOPICS:
        raise ValueError("Unknown topic")
    c = {"id": uuid.uuid4().hex[:12], "topic": topic, "date": day if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day)) else today(),
         "created": datetime.now().isoformat(timespec="seconds"), "status": status, "step": "", "error": "",
         "ext": ext, "source": source, "chunks": 0, "minutes": None, "title": "", "summary": "",
         "sections": [], "announcements": [], "transcript": ""}
    with LOCK:
        class_save(c)
    return c


def class_transcribe(c):
    if not WHISPER_MODEL or not Path(WHISPER).exists() or not Path(FFMPEG).exists():
        raise RuntimeError("Speech recognition isn't set up: needs whisper-cli, ffmpeg (brew install whisper-cpp ffmpeg) "
                           "and ggml-large-v3-turbo.bin in data/models.")
    audio = CLASS_DIR / f"{c['id']}{c['ext']}"
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        r = subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-i", str(audio), "-ar", "16000", "-ac", "1",
                            "-c:a", "pcm_s16le", str(wav)], capture_output=True, text=True, timeout=1800)
        if r.returncode or not wav.exists():
            raise RuntimeError("Couldn't read the audio: " + (r.stderr.strip()[-200:] or "unknown format"))
        minutes = round(wav.stat().st_size / 32000 / 60, 1)
        t = TOPICS[c["topic"]]
        outline = learn_load(c["topic"]).get("outline") or []
        hint = f"{t['subject']} class on {t['name']}: " + ", ".join(x["title"] for x in outline)[:400] + "."
        cmd = [WHISPER, "-m", str(WHISPER_MODEL), "-f", str(wav), "-l", "en", "-oj", "-of", str(Path(tmp) / "out"),
               "-t", "8", "-pp", "--prompt", hint]
        if VAD_MODEL:
            cmd += ["--vad", "-vm", str(VAD_MODEL)]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace")
        killer = threading.Timer(600 + minutes * 120, proc.kill)    # normally ~10x faster than real time
        killer.start()
        try:
            for line in proc.stderr:
                m = re.search(r"progress =\s*(\d+)%", line)
                if m:
                    class_set(c["id"], step=f"Transcribing {m.group(1)}%")
            code = proc.wait()
        finally:
            killer.cancel()
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        if code != 0 or not (Path(tmp) / "out.json").exists():
            raise RuntimeError("Whisper couldn't transcribe this recording.")
        segs = json.loads((Path(tmp) / "out.json").read_text(errors="replace")).get("transcription", [])
    parts, last = [], -60_000
    for sg in segs:                                       # paragraphs with a [mm:ss] mark about once a minute
        text = sg.get("text", "").strip()
        if not text:
            continue
        off = sg["offsets"]["from"]
        if off - last >= 60_000:
            parts.append(f"\n\n[{off // 60000:02d}:{off // 1000 % 60:02d}] ")
            last = off
        parts.append(text + " ")
    transcript = "".join(parts).strip()
    if len(transcript) < 20:
        raise RuntimeError("No speech found in this recording.")
    return transcript, minutes


def class_notes(c):
    tid = c["topic"]
    t = TOPICS[tid]
    outline = api_learn_outline({"topic": tid})["outline"]          # builds the subtopic plan if it doesn't exist yet
    ids = {x["id"] for x in outline}
    day = date.fromisoformat(c["date"])
    prompt = (
        f"Below is an automatic transcript of a {t['subject']} class the student recorded on {c['date']} "
        f"({day.strftime('%A')}), topic: {t['name']}. Speech recognition gets technical words wrong: fix them using "
        "the course materials and common sense. Never add content the teacher didn't cover.\n"
        "1. 'sections': excellent notes of what was taught, as the best student in the room would write them: Markdown "
        "with ### headings, definitions, " + ("formulas in LaTeX with units, every worked example the teacher did step by step "
        "with their numbers" if kind_of(tid) in ("maths", "science") else "theories and who they come from, examples and "
        "case studies, arguments for and against, how to structure answers") + ", and the tips and warnings they gave "
        "(\"this always comes up\", typical mistakes). One entry "
        "per subtopic actually covered, in class order; 'sub' is that subtopic's id from this list, or 'general' if it "
        f"fits none: {json.dumps([{'id': x['id'], 'title': x['title']} for x in outline], ensure_ascii=False)}\n"
        "2. 'announcements': everything said beyond the lesson content that the student must not miss: test and exam "
        "dates, exam structure (sections, number and type of questions, duration, calculator, what's included), "
        "deadlines, homework, changes to classes. One clear sentence each. 'date' is YYYY-MM-DD when a date is said or "
        f"can be worked out from the class date, otherwise ''. Empty list if there are none.\n"
        "3. 'title': 3-6 words naming what the class covered. 'summary': 1-2 sentences.\n"
        "Skip the register, small talk, jokes and noise.\n\nTRANSCRIPT:\n" + c["transcript"][:200_000])
    system = (f"{STYLE}\nTopic: {topic_label(tid)}.\nThe student's course materials for this topic (use them to fix "
              f"misheard terms and follow their notation):\n{topic_material(tid, 150_000)}")
    out = gemini([{"role": "user", "parts": [{"text": prompt}]}], system, CLASS_SCHEMA, 0.3)
    sections, first = [], None
    for x in out.get("sections", []):
        sid = x.get("sub") if x.get("sub") in ids else None
        first = first or sid
        notes = tidy_math(str(x.get("notes", "")).strip())
        if notes:
            sections.append({"sub": sid, "notes": notes})
    fallback = first or outline[0]["id"]                          # 'general' parts go with the first subtopic taught
    merged = {}
    for x in sections:
        k = x["sub"] or fallback
        merged[k] = (merged[k] + "\n\n" + x["notes"]) if k in merged else x["notes"]
    news = [{"kind": a.get("kind") if a.get("kind") in ("exam", "deadline", "homework") else "other",
             "text": str(a.get("text", "")).strip(), "date": a["date"] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(a.get("date"))) else ""}
            for a in out.get("announcements", []) if str(a.get("text", "")).strip()]
    return {"title": str(out.get("title", "")).strip()[:80], "summary": str(out.get("summary", "")).strip(),
            "sections": [{"sub": k, "notes": v} for k, v in merged.items()], "announcements": news}


def class_process(cid):
    with CLASS_QUEUE:
        try:
            c = class_load(cid)
            if not c.get("transcript"):
                class_set(cid, status="transcribing", step="Preparing the audio", error="")
                transcript, minutes = class_transcribe(c)
                c = class_set(cid, transcript=transcript, minutes=minutes)
            class_set(cid, status="writing", step="Writing notes", error="")
            notes = class_notes(c)
            class_set(cid, status="done", step="", **notes)
            with LOCK:
                log_event("class_notes", c["topic"], minutes=c.get("minutes"))
        except Exception as e:  # shown on the recording; Try again restarts from what's done
            if not (CLASS_DIR / f"{cid}.json").exists():
                return                            # deleted while it was being processed
            class_set(cid, status="error", step="", error=str(e)[:300])


def class_queue(cid):
    threading.Thread(target=class_process, args=(cid,), daemon=True).start()


def class_public(c, full=False):
    return {k: v for k, v in c.items() if full or k != "transcript"}


def api_class_start(b):
    ext = b.get("ext") if b.get("ext") in AUDIO_EXT else ".webm"
    return {"id": class_new(b["topic"], ext, "recording")["id"]}


def api_class_stop(b):
    with LOCK:
        c = class_load(b["id"])
        go = c["status"] == "recording" and c["chunks"] > 0
        if go:
            c.update(status="queued", step="Waiting")
            class_save(c)
    if c["status"] == "recording":                # nothing was recorded
        return api_class_delete(b) | {"empty": True}
    if go:
        class_queue(c["id"])
    return {"ok": True}


def api_class_list(b):
    out = []
    if HOSTED:
        found = [cloud.get(k, None) for k in cloud.keys("classes/")]
    else:
        found = []
        for f in CLASS_DIR.glob("*.json"):
            try:
                found.append(json.loads(f.read_text()))
            except json.JSONDecodeError:
                continue
    for c in found:
        if c and (not b.get("topic") or c["topic"] == b["topic"]):
            out.append(class_public(c))
    out.sort(key=lambda c: (c["date"], c["created"]), reverse=True)
    return {"classes": out, "ready": bool(WHISPER_MODEL)}


def api_class_get(b):
    return class_public(class_load(b["id"]), full=True)


def api_class_redo(b):
    with LOCK:
        c = class_load(b["id"])
        if c["status"] in ("queued", "transcribing", "writing", "recording", "uploading"):
            return {"ok": True}
        c.update(status="queued", step="Waiting", error="")
        if b.get("topic") in TOPICS:
            c["topic"] = b["topic"]
        class_save(c)
    class_queue(c["id"])
    return {"ok": True}


def api_class_delete(b):
    c = class_load(b["id"])
    with LOCK:
        (CLASS_DIR / f"{c['id']}{c['ext']}").unlink(missing_ok=True)
        (CLASS_DIR / f"{c['id']}.json").unlink(missing_ok=True)
    if cloud.on():
        cloud.delete(f"classes/{c['id']}.json")
    return {"ok": True}


def class_chunk(q, rfile, n):
    """One piece of a recording in progress (sent every few seconds, in order)."""
    if not 0 < n <= 50_000_000:
        raise ValueError("bad piece size")
    data, seq = rfile.read(n), int(q["n"])
    if len(data) != n:
        raise ValueError("the piece was cut off")
    with LOCK:
        c = class_load(q["id"])
        if c["status"] != "recording":
            raise ValueError("this recording was already stopped")
        if seq < c["chunks"]:
            return {"ok": True, "chunks": c["chunks"]}             # a retry of a piece we already have
        if seq > c["chunks"]:                     # the browser gave up on a piece: keep going without it
            print(f"class {c['id']}: pieces {c['chunks']}-{seq - 1} missing")
            c["gaps"] = c.get("gaps", 0) + seq - c["chunks"]
        with open(CLASS_DIR / f"{c['id']}{c['ext']}", "ab") as f:
            f.write(data)
        c["chunks"] = seq + 1
        class_save(c)
    return {"ok": True, "chunks": c["chunks"]}


def class_upload(q, rfile, n):
    """A whole recording made elsewhere (phone voice memo etc.)."""
    ext = Path(q.get("name", "")).suffix.lower()
    if ext not in AUDIO_EXT:
        raise ValueError("That isn't an audio file Study Desk can read (m4a, mp3, wav, webm, ogg…)")
    if not 0 < n <= 2_000_000_000:
        raise ValueError("file too large")
    c = class_new(q.get("topic", ""), ext, "uploading", q.get("date"), "file")
    with open(CLASS_DIR / f"{c['id']}{ext}", "wb") as f:
        left = n
        while left:
            piece = rfile.read(min(left, 1 << 20))
            if not piece:
                break
            f.write(piece)
            left -= len(piece)
    if left:
        api_class_delete({"id": c["id"]})
        raise ValueError("the upload was cut off")
    class_set(c["id"], status="queued", step="Waiting")
    class_queue(c["id"])
    return {"id": c["id"]}


def class_resume():
    """After a restart, finish recordings that were waiting or being processed."""
    for f in CLASS_DIR.glob("*.json"):
        try:
            c = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        if c.get("status") in ("queued", "transcribing", "writing"):
            class_queue(c["id"])
        elif c.get("status") == "uploading":
            class_set(c["id"], status="error", step="", error="The upload was interrupted. Delete this and upload the file again.")


# =====================================================================
# FIRST-RUN SETUP GUIDE (/setup): name, AI engine, IntoStudy, Compass, programme
# =====================================================================
JOBS = {}            # background jobs the guide shows: {name: {"state": running|done|error, "step", "error"}}


def run_job(name, fn, *a):
    if JOBS.get(name, {}).get("state") == "running":
        return False
    JOBS[name] = {"state": "running", "step": "", "error": None, "started": datetime.now().isoformat(timespec="seconds")}

    def go():
        try:
            fn(*a)
            JOBS[name].update(state="done", step=JOBS[name].get("step") or "Done.")
        except Exception as e:
            JOBS[name].update(state="error", error=f"{e}"[:400])
            print(f"setup job {name}:", type(e).__name__, e, flush=True)
    threading.Thread(target=go, daemon=True).start()
    return True


def env_set(**kw):
    """Write settings into .env (keys are never sent back to the browser)."""
    with LOCK:
        env = read_env()
        env.update({k: str(v) for k, v in kw.items() if v is not None})
        ENV.parent.mkdir(parents=True, exist_ok=True)
        ENV.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
        os.chmod(ENV, 0o600)


def setup_state():
    return load("setup.json", {})


def needs_setup():
    return not setup_state().get("done") and not CONFIG["subjects"]


# ---------- AI engine: the Antigravity CLI (agy) ----------
AGY_MANIFEST = "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/darwin_{arch}.json"
AGY_SIGNIN = {"proc": None, "fd": None, "out": "", "url": None}


def agy_env():
    """AGY_HOME (testing only) gives agy its own sign-in and projects instead of ~/.gemini."""
    env = dict(os.environ)
    if os.environ.get("AGY_HOME"):
        env["HOME"] = os.environ["AGY_HOME"]
    return env


def mac_arch():
    """arm64 on Apple silicon (also when this Python runs under Rosetta), amd64 on Intel."""
    try:
        arm = subprocess.run(["sysctl", "-n", "hw.optional.arm64"], capture_output=True, text=True).stdout.strip() == "1"
    except OSError:
        arm = False
    return "arm64" if arm else "amd64"


def agy_install():
    """Download Google's own build of agy for this Mac (Intel or Apple silicon) into STUDY_HOME/bin."""
    import hashlib
    import tarfile
    job = JOBS["agy_install"]
    job["step"] = "Finding the right version for this Mac…"
    with urllib.request.urlopen(AGY_MANIFEST.format(arch=mac_arch()), timeout=30) as r:
        man = json.load(r)
    url = man["url"]
    if not url.startswith("https://storage.googleapis.com/antigravity-public/"):
        raise RuntimeError("unexpected download address in Google's manifest")
    dest = paths.HOME / "bin" / "agy"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tgz = Path(tmp) / "agy.tar.gz"
        curl_download(url, tgz, job, f"Downloading Antigravity {man.get('version', '')}")
        h = hashlib.sha512()
        with open(tgz, "rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
        if man.get("sha512") and h.hexdigest() != man["sha512"]:
            raise RuntimeError("the download was damaged (checksum mismatch); try again")
        job["step"] = "Unpacking…"
        with tarfile.open(tgz) as t:
            member = next((m for m in t.getmembers() if m.isfile() and m.name.rsplit("/", 1)[-1] in ("antigravity", "agy")), None)
            if not member:
                raise RuntimeError("no agy program inside the download")
            src = t.extractfile(member)
            tmpbin = dest.with_suffix(".new")
            with open(tmpbin, "wb") as f:
                shutil.copyfileobj(src, f)
        os.chmod(tmpbin, 0o755)
        os.replace(tmpbin, dest)
    global AGY
    AGY = find_agy()
    job["step"] = f"Installed Antigravity {man.get('version', '')}."


def curl_download(url, dest, job, label):
    """Big downloads go through macOS's curl (retries, follows redirects); progress shown in the job."""
    size = 0
    try:
        head = subprocess.run(["curl", "-sIL", url], capture_output=True, text=True, timeout=30).stdout
        size = int((re.findall(r"(?im)^content-length:\s*(\d+)", head) or [0])[-1])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    proc = subprocess.Popen(["curl", "-fsSL", "--retry", "3", "-o", str(dest), url], stderr=subprocess.PIPE, text=True)
    while proc.poll() is None:
        got = dest.stat().st_size if dest.exists() else 0
        job["step"] = f"{label}: {got >> 20}" + (f" of {size >> 20} MB" if size else " MB")
        time.sleep(1)
    if proc.returncode:
        raise RuntimeError(f"download failed ({(proc.stderr.read() or '').strip()[:200] or proc.returncode})")


def agy_models():
    """(signed_in, [model ids]) from `agy models`."""
    if not os.path.exists(AGY):
        return False, []
    try:
        r = subprocess.run([AGY, "models"], capture_output=True, text=True, timeout=90, env=agy_env(), stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, []
    out = r.stdout + r.stderr
    models = [l.split("\t", 1)[0].strip() for l in out.splitlines() if "\t" in l and not l.startswith(" ")]
    return ("sign in" not in out.lower() and bool(models)), models


def pick_agy_models(models):
    gem = [m for m in models if m.startswith("gemini")] or models

    def first(pats):
        return next((m for p in pats for m in gem if re.search(p, m)), None)
    smart = first([r"flash.*high", r"flash", r"high$", r"."])        # Flash: good and with a generous free quota
    fast = first([r"flash.*medium", r"flash.*low", r"flash", r"."]) or smart
    return smart, fast


def agy_project():
    """Make sure the Antigravity project "Study Desk" exists, so these chats stay apart from the student's own."""
    home = Path(agy_env().get("HOME") or Path.home())
    d = home / ".gemini" / "config" / "projects"
    for f in d.glob("*.json") if d.exists() else []:
        try:
            if json.loads(f.read_text()).get("name") == AGY_PROJECT:
                return f.stem
        except (OSError, ValueError):
            continue
    AGY_WORK.mkdir(parents=True, exist_ok=True)
    d.mkdir(parents=True, exist_ok=True)
    pid = str(uuid.uuid4())
    (d / f"{pid}.json").write_text(json.dumps({"id": pid, "name": AGY_PROJECT, "projectResources": {
        "resources": [{"folderUri": AGY_WORK.resolve().as_uri()}]}}, indent=2))
    return pid


def agy_signin_start():
    """Run agy in a pseudo-terminal: it asks for a login method (we pick Google) and opens the browser."""
    import pty
    agy_signin_stop()
    master, slave = pty.openpty()
    proc = subprocess.Popen([AGY], stdin=slave, stdout=slave, stderr=slave, env={**agy_env(), "TERM": "xterm-256color"},
                            cwd=str(DATA), start_new_session=True, close_fds=True)
    os.close(slave)
    AGY_SIGNIN.update(proc=proc, fd=master, out="", url=None)

    def read():
        picked, raw = False, b""
        while proc.poll() is None:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            raw = (raw + chunk)[-200_000:]
            if not picked and b"Google OAuth" in raw:
                time.sleep(0.4)
                os.write(master, b"\r")          # 1. Google OAuth
                picked = True
            m = re.search(rb"\x1b\]8;[^;]*;(https://accounts\.google\.com/[^\x07\x1b]+)", raw)
            if m:
                AGY_SIGNIN["url"] = m.group(1).decode()
            text = re.sub(rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", b"", raw)
            text = re.sub(rb"\x1b\[[0-9;?>=]*[ -/]*[@-~]", b"", text).decode(errors="replace")
            AGY_SIGNIN["out"] = "\n".join(l.rstrip() for l in text.splitlines() if l.strip())[-1500:]
    threading.Thread(target=read, daemon=True).start()
    threading.Timer(900, agy_signin_stop).start()    # give up after 15 minutes


def agy_signin_stop():
    p, fd = AGY_SIGNIN.get("proc"), AGY_SIGNIN.get("fd")
    if p and p.poll() is None:
        p.kill()
    if fd:
        try:
            os.close(fd)
        except OSError:
            pass
    AGY_SIGNIN.update(proc=None, fd=None)


def agy_check():
    """Everything the guide needs about agy; sets up the project and models once signed in."""
    signed, models = agy_models()
    out = {"installed": os.path.exists(AGY), "path": AGY, "signed_in": signed, "models": models}
    if signed:
        agy_signin_stop()
        smart, fast = pick_agy_models(models)
        env = read_env()
        if not env.get("AGY_MODEL") or env["AGY_MODEL"] not in models or re.search(r"argon|pro-|hydrogen", env["AGY_MODEL"]):
            env_set(AGY_MODEL=smart)
        if not env.get("AGY_FAST_MODEL") or env["AGY_FAST_MODEL"] not in models:
            env_set(AGY_FAST_MODEL=fast)
        out.update(project=agy_project(), model=agy_model(True), fast_model=agy_model(False))
    return out


def api_setup_agy(b):
    act = b.get("action", "check")
    if act == "install":
        run_job("agy_install", agy_install)
        return {"started": True}
    if act == "signin":
        if not os.path.exists(AGY):
            raise ValueError("Install Antigravity first.")
        agy_signin_start()
        return {"started": True}
    if act == "code":                     # the code Google shows after signing in, if agy asks for it
        code = str(b.get("code", "")).strip()
        if not AGY_SIGNIN.get("fd") or not re.fullmatch(r"[\w\-./~]{4,400}", code):
            raise ValueError("Start the sign-in again, then paste the code.")
        os.write(AGY_SIGNIN["fd"], code.encode() + b"\r")
        return {"ok": True}
    if act == "terminal":                 # fallback: the ordinary sign-in in a Terminal window
        script = f'tell application "Terminal" to do script "{AGY}"\ntell application "Terminal" to activate'
        subprocess.run(["osascript", "-e", script], timeout=20)
        return {"ok": True}
    if act == "test":
        return {"answer": agy_call([{"role": "user", "parts": [{"text": "Reply with exactly: Study Desk is ready."}]}],
                                   think=False)}
    return agy_check()


# ---------- the setup status and steps ----------
def api_setup(_):
    st, sync = setup_state(), load("sync_status.json", {})
    found = load("courses_found.json", {})
    chosen = load("courses.json", {}) or CONFIG.get("intostudy_courses", {})
    signin = AGY_SIGNIN.get("proc")
    return {
        "done": bool(st.get("done")), "name": progress()["settings"].get("name", ""), "app": bool(os.environ.get("STUDY_HOME")),
        "version": app_version(), "port": PORT, "home": str(paths.HOME),
        "engine": {"installed": os.path.exists(AGY), "has_key": bool(api_key()),
                   "install": JOBS.get("agy_install"), "signing_in": bool(signin and signin.poll() is None),
                   "signin_url": AGY_SIGNIN.get("url"), "signin_out": AGY_SIGNIN.get("out", "")[-600:]},
        "intostudy": {**{k: sync.get(k) for k in ("state", "step", "signed_in", "last_ok", "error", "finished")},
                      "running": sync_running(), "found": found, "chosen": chosen, "files": len(ALL_FILES),
                      "known": len(load("sync_manifest.json", {}))},
        "compass": compass_public(), "timetable": len(CONFIG.get("timetable", [])),
        "onedrive": onedrive_public(),
        "programme": {"job": JOBS.get("programme"), "subjects": {n: len(s["topics"]) for n, s in CONFIG["subjects"].items()},
                      "log": tail(DATA / "setup_topics.log", 1500)},
    }


def tail(p, n):
    try:
        return p.read_text(errors="replace")[-n:]
    except OSError:
        return ""


def app_version():
    try:
        return (APP / "VERSION").read_text().strip()
    except OSError:
        return "dev"


def api_setup_name(b):
    name = str(b.get("name", "")).strip()[:40]
    if not name:
        raise ValueError("Write your name first.")
    return api_settings({"name": name})


def api_setup_courses(b):
    found = load("courses_found.json", {})
    ids = [str(i) for i in b.get("ids", []) if str(i) in found]
    if not ids:
        raise ValueError("Tick at least one course.")
    with LOCK:
        save("courses.json", {i: found[i]["name"] for i in ids})
    start_sync()
    return {"ok": True, "chosen": len(ids)}


def build_programme():
    log = DATA / "setup_topics.log"
    JOBS["programme"]["step"] = "Reading your course files and writing your programme (a few minutes)…"
    with open(log, "w") as f:
        r = subprocess.run([sys.executable, str(APP / "setup_topics.py"), "--force"], cwd=str(APP), stdout=f,
                           stderr=subprocess.STDOUT, timeout=3600, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    if r.returncode:
        raise RuntimeError(tail(log, 400).strip().splitlines()[-1] if tail(log, 400).strip() else f"exit {r.returncode}")
    load_config()
    reload_files()
    with LOCK:                                   # exam dates from the files, unless you already set your own
        p = progress()
        if not p["exams"]:
            p["exams"] = CONFIG.get("exams", [])
            save("progress.json", p)
    JOBS["programme"]["step"] = "Programme ready. Sorting the remaining files into topics…"
    threading.Thread(target=lambda: (_quiet(auto_sort), _quiet(sort_links)), daemon=True).start()
    if compass_connected():                      # now the timetable can be matched to the new subjects
        threading.Thread(target=lambda: _quiet(compass_apply), daemon=True).start()


def api_setup_programme(b):
    if not ALL_FILES:
        raise ValueError("No course files yet: connect IntoStudy and let the first sync finish.")
    if not (agy_on() or api_key()):
        raise ValueError("Connect the AI engine first (step 2).")
    return {"started": run_job("programme", build_programme)}


def api_setup_done(b):
    with LOCK:
        st = setup_state()
        st["done"] = today()
        save("setup.json", st)
    return {"ok": True}


# ---------- updates (Mac app only): replace the code, never the data ----------
UPDATE_REPO = os.environ.get("STUDY_REPO", "akylbek10/study-desk-share")


def _vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "0"))


def api_update(b):
    if not os.environ.get("STUDY_HOME"):
        raise ValueError("Updates are for the Study Desk app. This copy runs from a folder: use git pull.")
    # github.com/…/releases/latest redirects to …/releases/tag/vX.Y.Z (no API rate limit on shared Wi-Fi)
    try:
        req = urllib.request.Request(f"https://github.com/{UPDATE_REPO}/releases/latest", method="HEAD",
                                     headers={"User-Agent": "StudyDesk"})
        with urllib.request.urlopen(req, timeout=30) as r:
            final = r.geturl()
    except urllib.error.HTTPError as e:
        raise ValueError(f"Couldn't check for updates (GitHub said {e.code}).")
    except urllib.error.URLError as e:
        raise ValueError(f"Couldn't reach GitHub to check for updates: {e.reason}")
    latest = (re.search(r"/releases/tag/v?([\d.]+)$", final) or [None, ""])[1]
    asset = {"browser_download_url": f"https://github.com/{UPDATE_REPO}/releases/download/v{latest}/Study-Desk-{latest}.zip"} \
        if latest else None
    newer = bool(asset) and _vtuple(latest) > _vtuple(app_version())
    if b.get("check") or not newer:
        return {"current": app_version(), "latest": latest or None, "newer": newer}
    run_job("update", install_update, asset["browser_download_url"], latest)
    return {"current": app_version(), "latest": latest, "newer": True, "started": True}


def update_check_loop():
    """Once a day: if a newer Study Desk is out, say so in the bell (the student decides when to install)."""
    time.sleep(120)
    while os.environ.get("STUDY_HOME"):
        try:
            r = api_update({"check": True})
            if r.get("newer"):
                notify([{"id": f"update:{r['latest']}", "kind": "update", "link": "#/settings",
                         "title": f"Study Desk {r['latest']} is available", "text": "Settings → Check for updates. Your data stays as it is."}])
        except Exception as e:
            print("update check:", e)
        time.sleep(86400)


def install_update(url, latest):
    job = JOBS["update"]
    if not url.startswith(f"https://github.com/{UPDATE_REPO}/releases/download/"):
        raise RuntimeError("unexpected download address")
    with tempfile.TemporaryDirectory(dir=paths.HOME) as tmp:
        z = Path(tmp) / "update.zip"
        job["step"] = f"Downloading Study Desk {latest}…"
        curl_download(url, z, job, f"Downloading Study Desk {latest}")
        job["step"] = "Unpacking…"
        subprocess.run(["ditto", "-x", "-k", str(z), tmp], check=True, timeout=300)
        bundle = next(Path(tmp).glob("*.app"), None)
        code = bundle / "Contents" / "Resources" / "study-app" if bundle else None
        if not code or not (code / "server.py").exists():
            raise RuntimeError("the download doesn't contain Study Desk")
        old = APP.with_name(APP.name + ".old")
        shutil.rmtree(old, ignore_errors=True)
        os.replace(APP, old)                  # running code stays loaded in memory until the restart
        shutil.copytree(code, APP)
        target = Path(os.environ.get("STUDY_APP_BUNDLE", ""))   # also swap the app in Applications, when we can
        if target.name.endswith(".app") and "AppTranslocation" not in str(target) and os.access(target.parent, os.W_OK):
            spare = target.with_name(target.name + ".old")
            shutil.rmtree(spare, ignore_errors=True)
            try:
                os.replace(target, spare)
                shutil.copytree(bundle, target, symlinks=True)
                shutil.rmtree(spare, ignore_errors=True)
            except OSError as e:
                print("update: couldn't replace the app bundle:", e)
    job["step"] = f"Updated to {latest}. Restarting…"
    label = os.environ.get("STUDY_LABEL")
    if label:
        threading.Timer(1.5, lambda: subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"])).start()


ROUTES = {
    ("GET", "/api/setup"): api_setup,
    ("POST", "/api/setup/name"): api_setup_name,
    ("POST", "/api/setup/agy"): api_setup_agy,
    ("POST", "/api/setup/courses"): api_setup_courses,
    ("POST", "/api/setup/programme"): api_setup_programme,
    ("POST", "/api/setup/done"): api_setup_done,
    ("POST", "/api/compass/connect"): api_compass_connect,
    ("POST", "/api/onedrive"): api_onedrive,
    ("GET", "/api/notify"): api_notify,
    ("POST", "/api/notify/seen"): api_notify_seen,
    ("GET", "/api/compass/want"): api_compass_want,
    ("POST", "/api/compass/push"): api_compass_push,
    ("POST", "/api/compass/helper"): api_compass_helper,
    ("POST", "/api/timetable/image"): api_timetable_image,
    ("POST", "/api/update"): api_update,
    ("POST", "/api/file/view"): api_file_view,
    ("POST", "/api/work/save"): api_work_save,
    ("POST", "/api/file/open"): api_file_open,
    ("POST", "/api/tasks"): api_tasks,
    ("POST", "/api/topic/search"): api_topic_search,
    ("POST", "/api/topic/links"): api_topic_links,
    ("POST", "/api/week"): api_week,
    ("POST", "/api/review"): api_review,
    ("POST", "/api/review/mine"): api_review_mine,
    ("POST", "/api/agent"): api_agent,
    ("GET", "/api/brief"): api_brief,
    ("POST", "/api/topic/mark"): api_topic_mark,
    ("POST", "/api/check/test"): api_check_test,
    ("POST", "/api/check/submit"): api_check_submit,
    ("GET", "/api/program"): api_program,
    ("POST", "/api/compass"): api_compass,
    ("POST", "/api/handoff"): api_handoff,
    ("POST", "/api/handoff/result"): api_handoff_result,
    ("POST", "/api/handoff/open"): api_handoff_open,
    ("POST", "/api/files/sort"): api_files_sort,
    ("POST", "/api/files/assign"): api_files_assign,
    ("GET", "/api/files/check"): api_files_check,
    ("GET", "/api/sync"): api_sync_status,
    ("POST", "/api/sync"): api_sync_start,
    ("POST", "/api/learn/outline"): api_learn_outline,
    ("POST", "/api/learn/lesson"): api_learn_lesson,
    ("POST", "/api/learn/notes"): api_learn_notes,
    ("POST", "/api/learn/slides"): api_learn_slides,
    ("POST", "/api/learn/ask"): api_learn_ask,
    ("POST", "/api/learn/test"): api_learn_test,
    ("POST", "/api/learn/submit"): api_learn_submit,
    ("POST", "/api/learn/save"): api_learn_save,
    ("POST", "/api/learn/mistakes"): api_learn_mistakes,
    ("GET", "/api/state"): api_state,
    ("GET", "/api/stats"): api_stats,
    ("POST", "/api/chat"): api_chat,
    ("POST", "/api/assistant"): api_assistant,
    ("POST", "/api/insights"): api_insights,
    ("POST", "/api/practice/generate"): api_practice_generate,
    ("POST", "/api/practice/mark"): api_practice_mark,
    ("POST", "/api/cards/generate"): api_cards_generate,
    ("POST", "/api/cards/due"): api_cards_due,
    ("POST", "/api/cards/all"): api_cards_all,
    ("POST", "/api/cards/review"): api_cards_review,
    ("POST", "/api/cards/delete"): api_cards_delete,
    ("POST", "/api/confidence"): api_confidence,
    ("POST", "/api/ping"): api_ping,
    ("POST", "/api/topic/files"): api_topic_files,
    ("POST", "/api/settings"): api_settings,
    ("POST", "/api/class/start"): api_class_start,
    ("POST", "/api/class/stop"): api_class_stop,
    ("POST", "/api/class/list"): api_class_list,
    ("POST", "/api/class/get"): api_class_get,
    ("POST", "/api/class/redo"): api_class_redo,
    ("POST", "/api/class/delete"): api_class_delete,
}
TYPES = {".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml"}


# ---------- the phone version: one password, a signed cookie ----------
# Things that need the Mac itself (its files, agy/whisper, the Compass browser, settings in .env).
MAC_ROUTES = {api_file_view, api_work_save, api_file_open, api_agent, api_compass, api_handoff, api_handoff_result,
              api_handoff_open, api_files_sort, api_files_assign, api_files_check, api_sync_start, api_settings,
              api_class_start, api_class_stop, api_class_redo, api_class_delete, api_setup_name, api_setup_agy,
              api_setup_courses, api_setup_programme, api_setup_done, api_compass_connect, api_update, api_timetable_image,
              api_compass_push, api_compass_helper, api_onedrive}
PASSWORD = os.environ.get("STUDY_PASSWORD", "")
SESSION_DAYS = 90
LOGIN_PAGE = b"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><title>Study Desk</title>
<meta name="theme-color" content="#111214"><style>
:root{color-scheme:dark}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#111214;color:#ecedf0;
font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:16px;box-sizing:border-box}
form{width:min(360px,100%);display:grid;gap:14px}h1{font-size:26px;margin:0}p{margin:0;color:#a3a6ad;font-size:14.5px}
input{font:inherit;padding:13px 14px;border-radius:12px;border:1px solid #33353b;background:#1b1c20;color:inherit}
input:focus{outline:2px solid #6d8cff;outline-offset:1px}
button{font:inherit;font-weight:600;padding:13px;border-radius:12px;border:0;background:#ecedf0;color:#111214;min-height:48px}
#err{color:#ff8a80;min-height:1.5em}</style></head><body>
<form id="f"><h1>Study Desk</h1><p>Sign in to see your cards, lessons and notes.</p>
<input type="text" name="username" value="study-desk" autocomplete="username" hidden>
<input id="pw" type="password" name="password" autocomplete="current-password" placeholder="Password" aria-label="Password" required autofocus>
<button>Sign in</button><p id="err" role="alert"></p></form>
<script>document.getElementById("f").onsubmit=async e=>{e.preventDefault();const b=e.submitter;b.disabled=true;
const r=await fetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},
body:JSON.stringify({password:document.getElementById("pw").value})}).catch(()=>null);
if(r&&r.ok){location.replace("/");return}b.disabled=false;
document.getElementById("err").textContent=r&&r.status===401?"Wrong password.":"Couldn't sign in. Try again.";}</script>
</body></html>"""


def _session_key():
    import hashlib
    return hashlib.sha256(f"studydesk|{PASSWORD}|{cloud.KEY}".encode()).digest()   # new password = everyone signed out


def make_session():
    import hmac
    exp = str(int(time.time()) + SESSION_DAYS * 86400)
    return f"{exp}.{hmac.new(_session_key(), exp.encode(), 'sha256').hexdigest()}"


def session_ok(cookie_header):
    import hmac
    from http.cookies import SimpleCookie
    try:
        tok = SimpleCookie(cookie_header or "")["sd"].value
        exp, sig = tok.split(".", 1)
        good = hmac.new(_session_key(), exp.encode(), "sha256").hexdigest()
        return hmac.compare_digest(sig, good) and int(exp) > time.time()
    except (KeyError, ValueError):
        return False


class Handler(BaseHTTPRequestHandler):
    timeout = 120                                 # a stalled upload can't hold a thread forever
    OK_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

    def foreign(self):
        origin = self.headers.get("Origin")
        if HOSTED:                                # Vercel only routes its own domains here; refuse other sites' pages
            return origin is not None and origin != f"https://{self.headers.get('Host', '')}"
        if origin and origin.startswith("chrome-extension://") and self.path.split("?")[0] in ("/api/compass/want", "/api/compass/push"):
            return self.headers.get("Host", "") not in self.OK_HOSTS
        return (self.headers.get("Host", "") not in self.OK_HOSTS
                or (origin is not None and origin.removeprefix("http://") not in self.OK_HOSTS))

    def gate(self, method):
        """Phone version only: True if this request has been answered (sign-in page, refusal)."""
        if not HOSTED:
            return False
        path = self.path.split("?")[0]
        if not cloud.on() or len(PASSWORD) < 12:
            self.send(503, b"Study Desk isn't set up here yet: it needs SUPABASE_URL, SUPABASE_SERVICE_KEY "
                           b"and STUDY_PASSWORD (12+ characters) in the Vercel project settings.", "text/plain")
            return True
        if path == "/login" and method == "GET":
            self.send(200, LOGIN_PAGE, "text/html")
            return True
        if path == "/api/login" and method == "POST":
            import hmac
            try:
                pw = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 10_000)) or b"{}").get("password", "")
            except ValueError:
                pw = ""
            if not hmac.compare_digest(str(pw).encode(), PASSWORD.encode()):
                time.sleep(1.5)                   # slows down guessing
                self.send(401, {"error": "Wrong password."})
                return True
            self.send(200, {"ok": True}, extra={"Set-Cookie": f"sd={make_session()}; Path=/; Max-Age={SESSION_DAYS * 86400}; "
                                                              "HttpOnly; Secure; SameSite=Lax"})
            return True
        if not session_ok(self.headers.get("Cookie")):
            if path.startswith("/api/"):
                self.send(401, {"error": "Sign in again."})
            else:
                self.send(302, b"", "text/plain", {"Location": "/login"})
            return True
        if path in ("/api/class/chunk", "/api/class/upload") or ROUTES.get((method, path)) in MAC_ROUTES:
            self.send(503, {"error": MAC_ONLY.replace(" The phone version doesn't make anything new with AI.", "")})
            return True
        return False

    def log_message(self, fmt, *args):  # never prints request bodies
        print(time.strftime("%H:%M:%S"), self.command, self.path.split("?")[0])

    def send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") or ctype.endswith("json") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def handle_api(self, method):
        fn = ROUTES.get((method, self.path.split("?")[0]))
        if not fn:
            return self.send(404, {"error": "not found"})
        try:
            body = {}
            if method == "POST":
                n = int(self.headers.get("Content-Length", 0))
                if n > 25_000_000:
                    return self.send(413, {"error": "Too large (max ~18 MB image)."})
                body = json.loads(self.rfile.read(n) or b"{}")
                if body.get("topic") and body["topic"] not in TOPICS and fn not in (api_assistant, api_files_assign, api_agent):
                    return self.send(400, {"error": "Unknown topic"})
            self.send(200, fn(body))
        except GeminiError as e:
            self.send(502, {"error": str(e)})
        except cloud.Conflict as e:
            self.send(409, {"error": str(e)})
        except (KeyError, ValueError, TypeError) as e:
            self.send(400, {"error": f"Bad request: {e}"})
        except Exception as e:  # keep the server alive
            self.send(500, {"error": f"{type(e).__name__}: {e}"})

    def serve_original(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        rel = (q.get("p") or [""])[0]
        f = original_for(rel) if rel in ALL_FILES else None
        if not f:
            return self.send(404, b"File not found", "text/plain")
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        disp = "inline" if ctype in ("application/pdf",) or ctype.startswith("image/") else "attachment"
        self.send(200, f.read_bytes(), ctype,
                  {"Content-Disposition": f"{disp}; filename*=UTF-8''{urllib.parse.quote(f.name)}"})

    def do_GET(self):
        cloud.begin()
        if self.foreign():
            return self.send(403, b"Forbidden", "text/plain")
        if self.gate("GET"):
            return
        if self.path.startswith("/api/"):
            return self.handle_api("GET")
        if self.path.startswith("/file?"):
            return self.serve_original()
        if self.path.startswith("/work.docx?"):
            rel = (urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("p") or [""])[0]
            data = work_docx(rel)
            if not data:
                return self.send(404, b"No filled-in copy yet", "text/plain")
            name = rel.removesuffix(".txt").rsplit("/", 1)[-1].rsplit(".", 1)[0] + " (my answers).docx"
            return self.send(200, data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                             {"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}"})
        rel = self.path.split("?")[0].lstrip("/") or "index.html"
        if rel == "setup":
            rel = "setup.html"
        elif rel == "index.html" and not HOSTED and needs_setup():
            return self.send(302, b"", "text/plain", {"Location": "/setup"})
        f = (STATIC / rel).resolve()
        if not f.is_relative_to(STATIC.resolve()) or not f.is_file():
            f = STATIC / "index.html"
        self.send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))

    def do_POST(self):
        cloud.begin()
        if self.foreign():
            return self.send(403, {"error": "forbidden"})
        if self.gate("POST"):
            return
        path = self.path.split("?")[0]
        if path in ("/api/class/chunk", "/api/class/upload"):      # raw audio, not JSON
            q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()}
            n = int(self.headers.get("Content-Length", 0))
            try:
                return self.send(200, (class_chunk if path.endswith("chunk") else class_upload)(q, self.rfile, n))
            except (KeyError, ValueError, FileNotFoundError) as e:
                return self.send(400, {"error": str(e)})
            except Exception as e:
                return self.send(500, {"error": f"{type(e).__name__}: {e}"})
        self.handle_api("POST")


if __name__ == "__main__":
    print(f"Study Desk running at http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
    print(f"Course text: {TEXT} ({len(ALL_FILES)} files)")
    threading.Thread(target=auto_sync_loop, daemon=True).start()
    threading.Thread(target=lambda: (time.sleep(5), auto_sort()), daemon=True).start()
    threading.Thread(target=review_warmup, daemon=True).start()
    threading.Thread(target=compass_loop, daemon=True).start()
    threading.Thread(target=update_check_loop, daemon=True).start()
    threading.Thread(target=onedrive_soon, daemon=True).start()
    _quiet(ensure_helper)                         # an update brings a new helper: put it where Chrome loads it from
    class_resume()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
