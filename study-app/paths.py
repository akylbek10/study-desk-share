"""Where Study Desk keeps its data.

STUDY_HOME set (the Mac app sets it to ~/Library/Application Support/Study Desk): everything that is yours lives
there (data/, topics.json, .env, course files), and the code can be replaced by an update without touching it.
Not set (running from the repo): the old layout, data next to the code and course files one folder up.
"""
import os
from pathlib import Path

APP = Path(__file__).resolve().parent
_home = os.environ.get("STUDY_HOME")
if _home:
    HOME = Path(_home).expanduser()
    FILES = HOME                   # courses/ and text/
else:
    HOME = APP
    FILES = APP.parent
DATA = HOME / "data"
TOPICS = HOME / "topics.json"
ENV = HOME / ".env"
TEXT = FILES / "text"
COURSES = FILES / "courses"
PACKS = FILES / "Study packs"
OVERVIEW = FILES / "PROGRAM_OVERVIEW.md"


def redact(text):
    """Error text with sign-in secrets taken out: Playwright errors list the request's headers, cookies included."""
    import re
    text = re.sub(r"(?im)^(\s*-?\s*(?:cookie|set-cookie|authorization|x-[\w-]*token)\s*:).*$", r"\1 [hidden]", str(text))
    return re.sub(r"(?i)\b(FedAuth|rtFa|SPA_RT|ESTSAUTH\w*|SignInStateCookie|MSISAuth\w*|\w*session\w*|\w*token\w*)=[^;\s\"']+",
                  r"\1=[hidden]", text)


def crash(status, **kw):
    """The end of a background script that failed: status and log say why, without any secret."""
    import sys
    import traceback
    e = sys.exc_info()[1]
    status(error=redact(f"{type(e).__name__}: {e}")[:500], **kw)
    sys.stderr.write(redact(traceback.format_exc()))
    sys.exit(1)
