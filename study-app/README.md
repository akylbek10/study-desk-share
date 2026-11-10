# Study Desk

A personal study app that runs on this Mac and uses Gemini with your own course files (`../text`).

## Start it
Double-click **Start Study Desk.command**. The first time, macOS may ask you to confirm: right-click → Open.
It opens http://127.0.0.1:8890 in your browser. To stop it, close the Terminal window that opened.

From a terminal instead:
```
cd study-app && python3 server.py
```

## First time
Get a free Gemini API key at https://aistudio.google.com/apikey and paste it in **Settings**.
The key is saved in `study-app/.env`, readable only by your Mac user. Don't share that file.

## How it is meant to be used
You learn in the Gemini app (your own 3.1 Pro); Study Desk keeps score and tells you where you stand.
1. Open a topic and press **Study this topic in Gemini** (it copies the prompt and opens your files).
2. When you have studied it, **tick it** (Progress page, the topic page, or the Gemini window).
3. **Confirm** it: an 8-question check over the whole topic, written from your files and solved twice; 80% confirms. A score of 80%+ from Gemini's own final test on the whole topic also confirms it.

Statuses: *Not studied* → *Studied* (your tick) → *Confirmed* (proved). *Behind* = the topic's class weeks are over and it isn't ticked. *Studied, not confirmed* = over, ticked, not proved.

## Telegram (Hermes, topic University)
Study Desk runs all the time as a login service (`~/Library/LaunchAgents/com.akylbek.studydesk.plist`, log in `~/Library/Logs/studydesk.log`), so Hermes can always reach it.
- **The agent.** In the University topic you just write ("что завтра?", "ревью по статике", "отметь momentum пройденной", "где я отстаю по химии?", or paste the Compass schedule). Hermes's model picks one of six Study Desk tools: `status`, `topics`, `review`, `mark`, `compass`, `ask`. The tools are `hermes/studydesk_mcp.py`; the copy Hermes runs is `~/.hermes/studydesk/studydesk_mcp.py` (copy the file there after editing it, then `hermes gateway restart`).
- **Reviews and long answers are posted by the tool itself, unchanged** (`hermes send`), because a small model shortens and translates them. The model then only says "Отправил выше."
- **Morning brief** at 07:30 (Hermes cron `study-brief`, script `~/.hermes/scripts/study_brief.py`, no AI): today's and the next classes with topics, what you are behind on, what is not confirmed, new files, assessments, exam readiness.
- **New files:** every 30 minutes (`study-new-files`), a message only when teachers uploaded something. Study Desk checks IntoStudy every 2 hours.
- **Tasks.** Write the agent what you must not forget ("запиши: по физике доделать Hwk10 к следующей паре, по химии прочитать главу к пятнице"). It splits the note into tasks, works out the subject and the date (no date + a subject = by that subject's next class) and saves them. "какие у меня задачи?" lists them, "сделал Hwk10" ticks one off. Things a teacher asks for in a pasted Compass post become tasks too.
- **Evening reminder** at 18:00 (`study-remind`, no AI): only when something is due for tomorrow's classes, e.g. "Physics в 09:00, BWH206 — нужно было: Hwk10". Open tasks are also in the morning brief.
- Everything goes through `POST /api/agent` (`action`: status, new, topics, review, mark, compass, ask, task_add, task_list, task_done, remind).

## What's inside
- **Review notes** (a tab on every topic): one detailed sheet to recall the whole topic: in one minute, key ideas, formulas, how to do each exam question type with a worked example, mistakes, five check questions. Written from your course files and checked once against them; written in the background for every topic already taught, and rewritten when new files arrive. Below it, *Your own notes*: paste the "Notes for Study Desk" block that Gemini writes at the end of a session.
- **Tasks (Today):** the same task list as in Telegram: add in free text, tick off, delete. Stored in `data/tasks.json`.
- **Course files search:** the Course files tab of every topic has a search box: it finds files by name and by words inside them, and shows the place in the text.
- **Progress:** every topic in class order with its status, a tick box, a Check button and a Gemini button. The first time, tick what you already know from class: until you do, every finished topic counts as behind.
- **Today, top:** *Where you stand* (confirmed / to confirm / behind / next assessment), *Do next* (the next class's topic if you haven't touched it, the oldest unconfirmed topic, the latest topic you are behind on, due flashcards) and *Assessments and exams* (dates from the course plans, Compass and Settings; for each exam how many of its topics are confirmed, studied and behind).
- `GET /api/brief` returns the same picture as plain text (for a Telegram message).
- **Today:**
  - *Semester ruler:* every topic on the week it starts, plus today, the holidays and your exams.
  - *Plan for today:* built from your due flashcards, weakest topics and the next class.
  - *Classes today* (or the next day with classes): time, room, teacher and the topic being taught. Each class opens that topic.
  - *Coming up in class* and *Needs attention* lists.
- **Week N of Semester 1 (Today) and Programme:** where each subject is now and what comes next.
  - The plan comes from your teachers' files on IntoStudy: the Physics Student Planner, the EAP Semester 1 overview, and the week folders in Maths and Chemistry. Later Maths and Chemistry weeks with no folder yet are marked *estimate*.
  - *New on IntoStudy* shows topics teachers uploaded or opened this week. The *On plan / ahead / behind* tag compares teachers' recent edits with the plan.
  - *Add from Compass:* paste the weekly programme your teacher posts in Compass (text or screenshot). Gemini reads which topics it covers and which day teaches what, and that week follows Compass instead of the plan: the class rows on Today and the Timetable show each day's topic. The physics schedule is the weekly "Class news: FSPH1102" post from your physics teacher on the Compass home page. The assistant and the "Study in Gemini" prompt know this week's topics too.
  - Compass can't be read automatically: it blocks automated browsers, so you copy the text yourself.
- **Timetable:** your weekly classes as a Monday–Friday grid, with a line for the current time. On a phone it shows one day under another. Holidays show no classes. The assistant knows the timetable too.
- **Subjects / subject pages:** a table of every topic with class date, confidence, practice marks, cards and when you last studied it.
- **Learn mode** (the first tab on every topic):
  - Gemini splits the topic into lesson-sized **subtopics** from your files. Every topic and subtopic is open on any day.
  - Each subtopic opens a learning canvas with:
    - *Guided lesson:* step by step, with a check question after each step and feedback on wrong answers.
    - *Notes:* revision notes you can save as a `.md` file.
    - *Slides:* a deck with speaker notes, arrow-key navigation and full screen.
    - *Figures:* maths and science questions come with a diagram (triangles, angles, forces, vectors, cliffs and projectiles, circuits) or a graph, like a printed exam paper. Gemini describes the figure (points, sides, angles, arrows, or the function/data); the app draws it, so labels sit on the right parts and graphs are plotted exactly. Lessons, mini tests and practice questions all have them. Content generated before this change has no figures until you regenerate it.
    - *Mini test:* you choose 5–20 questions and Easy, Medium, Hard or Exam style. **80% or more passes the subtopic.** After the test you get a review of every question, can ask about any mistake, and can turn mistakes into flashcards.
    - *My notes:* your own notes, saved automatically.
  - **Ask anytime:** a chat panel beside the lesson that sees the current step, slide or question. During a test it gives hints only.
  - Lessons, notes and slides are generated once and then open instantly. You can regenerate any of them.
  - Progress shows on the topic page, in the subject table (Learned column), in Today's plan (Continue) and in Profile (Learning path).
- **Study in Gemini** (button on every topic and subtopic): for a session in the Gemini app with your own model (e.g. 3.1 Pro). Study Desk copies a prompt and opens a Finder folder (`Study packs/…`) with the text of all your notes and exam questions for that topic, the formula sheet and the main PDFs. The prompt tells Gemini what to teach (objectives), your progress and weak flashcards, your exam date and style, and to teach step by step with check questions, an exam question marked like an examiner and a final 5-question test. Open Gemini, drag the files in, paste, send. Afterwards type Gemini's score into the same window: it counts toward your progress (80%+ passes the subtopic) and study time.
- **Topic pages:**
  - *Tutor:* answers from your course files. You can attach a photo of your working.
  - *Practice questions:* written in past-paper style and marked against a mark scheme.
  - *Flashcards:* made from your notes.
  - *Course files:* opens the original PDFs and slides.
- **IntoStudy files:** Study Desk keeps your course files up to date by itself. It checks man.intostudy.com when it starts and every 6 hours while it's open, downloads new and changed files, turns them into text and adds them to your topics. Press **Update files** (top right) to check now. Today shows *New from your teachers*, grouped by topic. The first time, press **Connect IntoStudy** and sign in with Microsoft in the window that opens (tick "Stay signed in"); after that it signs in on its own. It uses its own Chrome profile in `data/browser-profile`; Study Desk never stores your password. If teachers start a new week or unit, add its folder to a topic's `sources` in `topics.json` (new files that match no topic show as "Not linked to a topic yet").
- **Files check** (left menu): is everything from IntoStudy downloaded, readable and in a topic? Files that no folder rule covers are sorted by Gemini automatically after every update (a file that covers several topics goes into all of them, e.g. unit notes or mixed exam questions; course-wide files like syllabus and past papers go to "Whole subject"). Anything the sorter wasn't sure about is listed under *For you to check*: pick the right topic and it's saved (`data/file_topics.json`; your choice always wins). It also lists files that failed to download, taught topics with no files, and scans the AI can't read.
- **Flashcards:** spaced-repetition review. Space flips a card; keys 1–4 grade it.
- **Profile:** streak, study time, activity heatmap, exam readiness, per-subject table, marks over time, marks by topic, flashcard maturity and retention, a 14-day review forecast, and an AI analysis of your progress.
- **Assistant** (button at the top right, or press `/`): a Gemini chat that knows your programme, exam dates and progress, from any page.
- **Settings:** API key, model, your name, exam dates, light/dark theme.

Every statistic is computed from your saved data only (`data/progress.json`, `data/cards.json`, `data/activity.json`). Study time is counted per active minute while the app is open.
Design notes are in `DESIGN.md`.

## The tutor
- Every tutor answer (topic *Tutor* tab and *Ask anytime* in lessons) is thought through first (Gemini "thinking" on high) and laid out as: the idea, the formula, a worked example, the common mistake, a problem for you.
- Answers with calculations, and problems the tutor sets, get a second independent pass that re-solves them; a wrong answer is replaced before you see it (marked *Double-checked*). If Gemini is over its limit, the first answer is shown unchecked.
- The tutor draws a diagram or graph under its answer when a picture helps.
- Buttons under the last answer: *Give me a hint / Show the next step / Show the full solution* while you solve a problem, otherwise *Explain it more simply / Show another example / Give me a problem to try / Make it harder*.
- *Check my solution (photo):* send a photo of your working. The tutor finds the first wrong line and explains it without giving the answer; the mistake is saved as a flashcard.
- Mini tests are solved a second time without the answer key; questions whose key doesn't match are dropped.
- These checks use 2 to 3 Gemini requests per answer, so the free daily limit is reached sooner.

## Gemini model
The app uses `gemini-3.5-flash` first. On 8 Nov 2026 it answered in about 2 seconds, while the newest Flash took 50–75 seconds under load. If a model is busy, over quota or slow, the app switches to the next one automatically. To force a model, set it in Settings.

## Where things are saved
- `data/progress.json`: confidence ratings, marked answers, settings
- `data/cards.json`: flashcards and their review schedule
- `data/learn/<topic>.json`: subtopic plan, generated lessons, notes and slides, test results, passed subtopics, your notes
- Chat history and unanswered practice questions stay in the browser.

## Changing topics
Edit `topics.json`. The weekly classes are in `timetable` (day 1 = Monday). Each topic lists `sources`: path prefixes inside `../text`; a source with `*` is a pattern (EAP week folders get renumbered, so they use `*Semester 1 Week 4/`). Each subject's `plan` lists topics by teaching week; `term.week1` is the Monday of week 1, and a topic in the plan starts in its first planned week. Add new topics there as the semester goes on.
New course files arrive automatically (see *IntoStudy files*). The sync needs `study-app/.venv`: `python3 -m venv .venv && .venv/bin/pip install playwright pypdf python-pptx python-docx`.
