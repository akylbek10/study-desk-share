#!/usr/bin/env python3
"""Study Desk tools for the Hermes agent (MCP over stdio, Python standard library only).

Hermes starts this file; it only talks to the Study Desk server on this Mac (http://127.0.0.1:8890/api/agent).
Every tool returns text that is ready to send to Telegram.
Installed copy: ~/.hermes/studydesk/studydesk_mcp.py (copy this file there after editing it).
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

URL = "http://127.0.0.1:8890/api/agent"
SERVER_INFO = {"name": "studydesk", "version": "1.0.0"}
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = (
    "Study Desk is the owner's study tracker for his university course (NCUK foundation year: Maths, Physics, "
    "Chemistry, EAP). It knows the timetable, the weekly programme, every course file, which topics he has studied "
    "and confirmed, where he is behind, assessments and exam readiness. For ANY question about his studies call one "
    "of these tools first and answer from what it returns; never answer from memory. Send review notes unchanged.")

S = lambda desc: {"type": "string", "description": desc}
TOOLS = [
    ("status", "Where he stands right now: today's and the next day's classes with their topics, topics he is behind "
               "on, topics studied but not confirmed, new files from teachers, coming assessments, exam readiness. "
               "Use for: 'что сегодня', 'что завтра', 'где я отстаю', 'что нового', 'когда тест', 'готов ли я к экзамену'.",
     {}, []),
    ("topics", "All topics of the course with their class week and status (not studied / studied / confirmed / "
               "behind). Optional subject filter. Use for: 'какие темы по физике', 'что я не подтвердил', 'мой прогресс по химии'.",
     {"subject": S("Optional: Maths, Physics, Chemistry or EAP")}, []),
    ("review", "Review notes for ONE topic so he can quickly recall it: key ideas, formulas, how to solve the exam "
               "questions, common mistakes, check questions, plus his own notes. Use for: 'ревью по статике', "
               "'напомни моменты', 'review trigonometry', 'повторить кинематику'. Send the returned text as it is.",
     {"topic": S("The topic in the user's own words, any language (e.g. 'статика', 'momentum')")}, ["topic"]),
    ("mark", "Tick a topic as studied (or untick it). Use for: 'отметь статику пройденной', 'я изучил momentum', 'сними отметку с ...'.",
     {"topic": S("The topic in the user's own words"),
      "studied": {"type": "boolean", "description": "true = studied (default), false = untick"}}, ["topic"]),
    ("compass", "Save this week's class schedule that he copied from Compass (his school portal). Use when he pastes "
                "or forwards a teacher's weekly schedule post (days, topics, homework).",
     {"text": S("The full pasted text of the schedule post")}, ["text"]),
    ("task_add", "Write down something he must do so he doesn't forget: homework and small tasks from teachers, "
                 "things to bring or prepare. He gets a reminder the evening before that subject's next class and in "
                 "the morning brief, and sees it in Study Desk. Use for: 'запиши', 'не забыть', 'надо сделать', "
                 "'задали', 'к пятнице нужно'. Pass his words unchanged (several tasks in one message are fine).",
     {"text": S("What he wrote, unchanged")}, ["text"]),
    ("tasks", "List his open tasks with their deadlines. Use for: 'какие у меня задачи', 'что надо сделать', 'что задали'.",
     {}, []),
    ("task_done", "Tick tasks off as finished. Use for: 'сделал Hwk10', 'домашку по физике сдал', 'всё сделал'.",
     {"text": S("What he says he finished, unchanged")}, ["text"]),
    ("ask", "Any other study question: explain something from the course, what is in his files, what a homework or "
            "assessment needs, how to plan revision. Answered by Study Desk's assistant, which reads his course files "
            "and progress. Pass his question unchanged.",
     {"question": S("His question, unchanged, in his language")}, ["question"]),
]


def call(action, args, timeout):
    body = json.dumps({"action": action, **args}).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r).get("text") or "Нет данных.", False
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error", "")
        except Exception:
            msg = ""
        return f"Study Desk ответил ошибкой: {msg or e.code}", True
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        return ("Study Desk сейчас не отвечает (сервер на Mac не запущен или занят). "
                f"Попробуй через минуту. ({getattr(e, 'reason', e)})"), True


def send_direct(text):
    """Post text to the study topic exactly as it is (no model in between). Target comes from STUDY_TG_TARGET,
    e.g. telegram:<chat id>:<topic id>, set in Hermes's config for this server. Returns True when delivered."""
    target = os.environ.get("STUDY_TG_TARGET")
    hermes = shutil.which("hermes") or os.path.expanduser("~/.local/bin/hermes")
    if not target or not os.path.exists(hermes):
        print(f"studydesk: no direct send (target set: {bool(target)}, hermes found: {os.path.exists(hermes)})", file=sys.stderr)
        return False
    try:
        r = subprocess.run([hermes, "send", "-t", target, "-q"], input=text, text=True, capture_output=True, timeout=60)
        if r.returncode:
            print(f"studydesk: hermes send failed ({r.returncode}): {(r.stderr or r.stdout)[-300:]}", file=sys.stderr)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError) as e:
        print(f"studydesk: hermes send error: {e}", file=sys.stderr)
        return False


def dispatch(msg):
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mid is None:                      # a notification: nothing to answer
        return None
    ok = lambda result: {"jsonrpc": "2.0", "id": mid, "result": result}
    if method == "initialize":
        asked = params.get("protocolVersion")
        return ok({"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0], "capabilities": {"tools": {}},
                   "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": [{"name": n, "description": d,
                              "inputSchema": {"type": "object", "properties": props, "required": req},
                              "annotations": {"readOnlyHint": n in ("status", "topics", "review", "ask", "tasks"), "destructiveHint": False}}
                             for n, d, props, req in TOOLS]})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in {t[0] for t in TOOLS}:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"No tool {name}"}}
        action = {"tasks": "task_list"}.get(name, name)
        text, is_err = call(action, args, 240 if name in ("ask", "review", "compass") else 90)
        # Long answers go to the chat untouched: a small model would shorten or translate them on the way.
        if not is_err and (name == "ask" or (name == "review" and text.startswith("REVIEW"))) and send_direct(text):
            text = ("DONE. The full answer has ALREADY been posted to the chat as a separate message, unchanged. "
                    "Do not repeat, summarise or translate it. Reply with ONE short line in Russian, for example: "
                    "«Отправил выше.»")
        return ok({"content": [{"type": "text", "text": text}], "isError": is_err})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not supported: {method}"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            batch = msg if isinstance(msg, list) else [msg]
            resps = [r for r in (dispatch(m) for m in batch if isinstance(m, dict)) if r]
            resp = resps if isinstance(msg, list) else (resps[0] if resps else None)
        if resp:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
