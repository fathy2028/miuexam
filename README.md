# miuexam — MIU Exam Tools

A small web app with three tools:

1. **DOCX → Moodle XML.** Turn exam questions written in Word into a Moodle
   question-bank import file.
2. **Add grade to class list.** Put the grades from a Moodle quiz export into
   a column of the university class list (`CO_GRADES.xlsx`).
3. **Announce grades.** Copy class-list columns into the Moodle gradebook
   export (`… Grades.ods`), optionally with a total column, and get the result
   as a `.csv` ready for Moodle's grade import.

Every gradebook run downloads the updated file **and a task report**
(`Task Report - … .xlsx`): settings, step-by-step progress, every student's
old → new value, and the students that could not be matched.

## How students are matched

The class list identifies students by ID, Moodle by e-mail. The numeric parts
match once the century and the slash are removed from the ID:

```
2021/11262  →  21 + 11262  →  2111262  ←  fathy2111262@miuegypt.edu.eg
```

**Missing students get 0.** A student who is in the file being written to
but not in the source file gets 0 in the written column(s). A student in the
source but not in the destination can't be written anywhere and is listed in
the task report.

## Add grade to class list

Inputs: the class list `.xlsx` (one sheet per section; a header row with
`Id`, then one student per row) and the quiz results exported from Moodle
(`.csv`, `.xlsx` or `.ods` with an `Email address` and a `Grade/10.00` column).

- **Grade column / out of:** detected from the `Grade/10.00` header and can be changed.
- **Rescale to:** e.g. out of 10 → out of 7 (`9.5 → 6.65`), then **round to**
  0.01 / 0.1 / 0.25 / 0.5 / 1.
- **Several attempts:** highest grade (default), latest or first attempt.
  "Never submitted" attempts are ignored; a student with only those gets 0.
- **Column:** overwrite an existing column (e.g. `Quiz`) or add a new one.
  A new column is added to every section sheet with the same formatting, and
  the title and "End Of Section" bars are widened to cover it.

After a run, the page keeps working from the updated class list, so you can
add the next quiz straight away.

## Announce grades

Inputs: the class list `.xlsx` and the Moodle gradebook export
(*Grades → Export*, as `.ods`, `.csv` or `.xlsx`).

Output: the grade report as **CSV** (UTF-8, comma separated, same columns as
the export plus the new ones), named after the template, e.g.
`CSC220 - 26F Grades.csv`. Import it with *Grades → Import → CSV file*.

- Tick the class-list columns to send. For each one, choose an existing report
  column to **override**, or leave it as a **new column**. New columns are
  inserted before Moodle's "Last downloaded from this course" column.
- **Add total column:** choose which columns to add up per student (default
  name "Total Course Work").

## Writing the Word file

```
Q1  What is the capital of France?          ← question label + text
A) Berlin                                   ← options A) B) C) … (A. and (A) also work)
B) Madrid
C) Paris
D) Rome
Answer: C                                   ← correct answer
Explanation: Paris has been the capital…    ← optional, shown as feedback
```

**Question labels:** `Q1`, `Q1.`, `Question 1`, `1.`, `1)`. Labels must count up
(1, 2, 3 …; a new section may restart at 1), so numbered lines inside a question
or explanation don't split it. If the file has no labels at all, each `A)`
starts a new question.

**Options:** 2 to 8 options (`A`–`H`), starting at A, in order.

**Marking the correct answer** (first one that applies wins):

1. An `Answer: X` line (`Ans: X`, `Correct: X`, `Correct answer: X` also work)
2. Highlighting / shading exactly one option
3. Making exactly one option bold

If the signals disagree (e.g. the answer line says C but B is highlighted), the
answer line is used and a warning is shown. A question with no marked answer,
several highlighted options, missing/out-of-order options, etc. is skipped and
listed in the warnings.

**Essay questions:** a labelled question with text but no options.

**Also supported:** questions or options inside tables, Shift+Enter line breaks,
hyperlinks, equations (converted to plain text), and images — images in the
question go into the question text, an image right after an option goes into
that option, and images after the options go into the feedback.

## Running

### Docker (production)

```bash
DJANGO_SECRET_KEY=$(openssl rand -hex 32) docker compose up --build -d
# → http://localhost:8080   (change with HOST_PORT=…)
```

nginx serves the React app and proxies `/api/` to the Django backend.

### Local development

```bash
# backend (http://localhost:8000)
cd backend
pip install -r requirements.txt
DJANGO_DEBUG=1 python manage.py runserver   # DJANGO_TIME_ZONE defaults to Africa/Cairo

# frontend (http://localhost:5173, proxies /api to :8000)
cd frontend
npm install
npm run dev
```

### Demo files

```bash
pip install python-docx pillow
python make_test_docs.py   # writes Demo_MCQ_with_images.docx / Demo_MCQ_no_images.docx
```

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/convert/` | Multipart field `file` (.docx). Returns the XML as a download. Headers `X-Question-Count` and `X-Warning-Count`; warnings are XML comments at the top of the file. |
| `POST` | `/api/convert/?report=1` | Same input; returns JSON `{filename, xml, question_count, mcq_count, essay_count, warnings}`. Used by the web UI. |
| `GET` | `/api/health/` | `{"ok": true}` |
| `POST` | `/api/grades/inspect/` | Any of `classlist` (.xlsx), `results` (.csv/.xlsx/.ods), `template` (.ods). Returns column lists and match counts. |
| `POST` | `/api/grades/add-to-classlist/` | `classlist`, `results` + fields `target_column`, `new_column`, `grade_column`, `out_of`, `scale_to`, `round_step`, `attempts`. |
| `POST` | `/api/grades/announce/` | `classlist`, `template` (.ods/.csv/.xlsx) + field `config` (JSON: `mappings`, `total`). The returned file is CSV. |

The gradebook endpoints return JSON `{file, report, totals, steps}`, where
`file` and `report` are `{name, type, data}` with base64 data.

Errors are JSON `{"error": "...", "warnings": [...]}` with status 400 (bad
upload), 422 (not a valid .docx / no convertible questions) or 500.

```bash
curl -F file=@exam.docx -OJ http://localhost:8080/api/convert/
```

## Project layout

```
backend/              Django + gunicorn, no database
  api/converter.py    DOCX parsing + Moodle XML generation
  api/views.py        converter endpoints
  api/gradebook.py    class list / quiz results / grade report logic
  api/ods.py          minimal .ods reader
  api/task_report.py  task report workbook
  api/grade_views.py  gradebook endpoints
frontend/             React + Vite single page, served by nginx
  src/tabs/           one component per tool
  nginx.conf        static files + /api/ reverse proxy
docker-compose.yml
make_test_docs.py   generates demo .docx files
```
