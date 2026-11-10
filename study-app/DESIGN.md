# Study Desk: design notes

**Subject:** the personal exam desk of an IFY student (Maths, Physics, Chemistry, EAP) at INTO Manchester.
**Primary job:** answer "what should I study now?", then make the studying itself fast.

## Direction: "exam desk"
The material world of the subject: fountain-pen ink, an examiner's red pen, graph paper, timetables.
- **Ink blue** `#2445C8` is the action colour.
- **Examiner red** `#C62F2F` is used only for "today" and lost marks.
- **Graph paper** appears in one place only: the semester ruler on the Today page. That ruler is the memorable element. Everything else stays quiet.

## Tokens
| Role | Light | Dark |
|---|---|---|
| Paper (page) | #F4F6F9 | #0D1220 |
| Surface | #FFFFFF | #151B2B |
| Ink (text) | #14213D | #E7EBF3 |
| Ink 2 (secondary) | #4A5568 | #A9B2C3 |
| Rule | #DCE2EB | #283149 |
| Pen (primary) | #2445C8 | #8EA2FF |
| Red pen | #C62F2F | #FF7B7B |

**Subject colours** (validated with the dataviz `validate_palette.js`; always shown with the subject's name, never colour alone):
| Subject | Light | Dark |
|---|---|---|
| Maths | #3B5BDB | #6A82EC |
| Physics | #00909E | #10A0AC |
| Chemistry | #A23BC2 | #B858D6 |
| EAP | #D9640B | #CF6E1E |

## Colour schemes (Settings → Colour scheme)
Six palettes, each with a light and a dark version. They live in `static/palettes.js`.
| Scheme | Light primary | Mood | Reference |
|---|---|---|---|
| Ink | #2445C8 | exam paper, fountain pen | the exam desk itself |
| Sage | #1D7350 | calmest, library green | EdTech guidance: calm greens and blues for focus; Khan Academy-style greens |
| Twilight | #5B3FD0 | gentle violet, deep indigo at night | the purple/indigo family common in flashcard and notes tools |
| Sunrise | #C2410C | warm and energetic | Amie's "warm minimalism"; Duolingo's energy, without its green |
| Graphite | #26262B | almost colourless | Notion / Linear minimalism: only subject colours carry hue |
| Ocean | #0B6E80 | fresh petrol teal | ui-ux-pro-max's teal suggestion |

**Default: Graphite, dark ("black")**, chosen by the owner on 8 Nov 2026. It's set in `index.html` (before first paint) and in `applyTheme()` in `app.js`.

Rules applied:
- Every text pair passes 4.5:1 in light and dark: ink on paper, ink-2 and ink-3 on surface, the primary on surface and on its own soft tint, and button text on the primary. Checked with a script; the first Sunrise draft failed at 4.47, so its tint was lightened.
- Bright colour goes only on actions, the active state and the heatmap, never behind reading text (EdTech guidance).
- Subject colours are fixed across all schemes, so a colour always means the same subject.
- Following Khan Academy's approach, the action colour is kept separate from success/warning/error colours.

## Type
- **Bricolage Grotesque** for headings: characterful, with optical sizes.
- **Onest** for UI and body text: clean, with Cyrillic support.
- Tabular figures for every number.
- Sentence case everywhere. No all-caps eyebrows, no middle-dot meta strings, no emoji icons. Icons are inline SVG.

## Layout
- **Desktop:** left rail, content up to 1120px wide, and an assistant drawer on the right that is reachable from every page.
- **Phone:** a bottom tab bar with 5 tabs.
- Lists are ruled rows (like exam answer lines) rather than a grid of identical cards.
- Numbered markers are used only for the day plan, which really is a sequence.

## Data honesty
Every number on the Profile page is computed in `server.py → compute_stats()` from saved files only (`data/progress.json`, `cards.json`, `activity.json`). If a stat has no data yet, the page shows an empty state saying how to get it, never a placeholder number.
