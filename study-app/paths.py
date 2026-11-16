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
