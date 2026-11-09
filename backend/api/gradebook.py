"""
Gradebook tools.

1. add_grades_to_classlist()
   Moodle quiz results (CSV / XLSX / ODS, keyed by e-mail) → a column of the
   university class list workbook (CO_GRADES.xlsx: one sheet per section,
   keyed by student ID), with optional rescaling and rounding.

2. announce_grades()
   Class list columns → the Moodle gradebook export (.ods/.csv/.xlsx, keyed by
   e-mail), overriding existing columns or adding new ones, plus an optional
   total. The result is written as CSV, ready for Moodle's grade import.

In both, a student who is in the destination file but not in the source gets
0 in the written column(s); students in the source that are not in the
destination are listed as unmatched. Every run also produces a task report
workbook (see task_report.py).

Students are matched by the numeric part of the ID and the e-mail:
   ID 2021/11262  →  "21" + "11262"  →  2111262  ←  fathy2111262@miuegypt.edu.eg
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

import openpyxl
from copy import copy

from openpyxl.utils import get_column_letter

from .ods import OdsSheet
from .task_report import Sheet, TaskReport


class GradebookError(ValueError):
    pass


# ─── Student keys ─────────────────────────────────────────────────────────────

_ID_RE = re.compile(r"^\s*(\d{4})\s*/\s*(\d+)\s*$")
_EMAIL_DIGITS_RE = re.compile(r"(\d+)@")


def key_from_id(value) -> str | None:
    """'2021/11262' → '2111262' (drop the century of the year, drop the slash)."""
    if value is None:
        return None
    m = _ID_RE.match(str(value))
    if not m:
        return None
    return m.group(1)[2:] + m.group(2)


def key_from_email(value) -> str | None:
    """'fathy2111262@miuegypt.edu.eg' → '2111262'."""
    if not value:
        return None
    m = _EMAIL_DIGITS_RE.search(str(value))
    return m.group(1) if m else None


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _to_number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value or "").strip().replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def round_to_step(value: float, step: float) -> float:
    """Round half-up to a multiple of `step` (0.01, 0.25, 0.5, 1 …)."""
    if step <= 0:
        return value
    d = Decimal(str(value)) / Decimal(str(step))
    return float(d.quantize(Decimal(1), rounding=ROUND_HALF_UP) * Decimal(str(step)))


def _display(v: float) -> str:
    return f"{v:.2f}"


# ─── Generic table reading (results file) ─────────────────────────────────────

def read_table(data: bytes, filename: str) -> list[list[str]]:
    """First sheet of a .csv / .xlsx / .ods file as a matrix of strings."""
    name = filename.lower()
    if name.endswith(".csv"):
        for enc in ("utf-8-sig", "cp1256", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), dialect)]
    if name.endswith(".xlsx"):
        wb = _load_xlsx(data, read_only=True)
        ws = wb.worksheets[0]
        return [["" if v is None else str(v).strip() for v in row]
                for row in ws.iter_rows(values_only=True)]
    if name.endswith(".ods"):
        return OdsSheet(data).text_matrix()
    raise GradebookError("Unsupported file type. Use .csv, .xlsx or .ods.")


def _load_xlsx(data: bytes, read_only=False):
    try:
        return openpyxl.load_workbook(io.BytesIO(data), read_only=read_only)
    except Exception as exc:
        raise GradebookError("The file is not a valid .xlsx workbook.") from exc


# ─── Quiz results ─────────────────────────────────────────────────────────────

_MAX_RE = re.compile(r"/\s*(\d+(?:[.,]\d+)?)\s*$")
_DATE_FORMATS = ("%d %B %Y %I:%M %p", "%d %B %Y %H:%M", "%A, %d %B %Y, %I:%M %p")


def _parse_date(s: str):
    s = re.sub(r"\s+", " ", s or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


@dataclass
class Results:
    headers: list[str]
    email_col: int | None
    id_col: int | None
    started_col: int | None
    grade_col: int | None
    out_of: float | None
    # student key → list of (order, started, grade or None)
    attempts: dict[str, list[tuple]] = field(default_factory=dict)
    unkeyed: list[str] = field(default_factory=list)
    emails: dict[str, str] = field(default_factory=dict)  # key → e-mail / id
    names: dict[str, str] = field(default_factory=dict)   # key → student name


def parse_results(data: bytes, filename: str, grade_col: int | None = None) -> Results:
    matrix = [r for r in read_table(data, filename) if any(c for c in r)]
    if not matrix:
        raise GradebookError("The results file is empty.")
    headers = matrix[0]
    low = [_norm(h) for h in headers]

    email_col = next((i for i, h in enumerate(low) if "email" in h or "e-mail" in h), None)
    id_col = next((i for i, h in enumerate(low) if h in ("id number", "idnumber", "id", "student id")), None)
    started_col = next((i for i, h in enumerate(low) if h.startswith("started")), None)
    first_col = next((i for i, h in enumerate(low) if h == "first name"), None)
    last_col = next((i for i, h in enumerate(low) if h in ("last name", "surname")), None)
    if email_col is None and id_col is None:
        raise GradebookError("The results file has no 'Email address' (or 'ID number') column.")

    if grade_col is None:
        grade_col = next((i for i, h in enumerate(low) if h.startswith("grade")), None)
    if grade_col is not None and not (0 <= grade_col < len(headers)):
        raise GradebookError("The selected grade column does not exist in the results file.")

    out_of = None
    if grade_col is not None:
        m = _MAX_RE.search(headers[grade_col])
        if m:
            out_of = float(m.group(1).replace(",", "."))

    res = Results(headers, email_col, id_col, started_col, grade_col, out_of)
    if grade_col is None:
        return res

    for order, row in enumerate(matrix[1:]):
        row = row + [""] * (len(headers) - len(row))
        email = row[email_col] if email_col is not None else ""
        key = key_from_email(email) or (key_from_id(row[id_col]) if id_col is not None else None)
        if key is None:
            label = email or " ".join(c for c in row[:2] if c)
            if label and not label.lower().startswith("overall average"):
                res.unkeyed.append(label)
            continue
        started = _parse_date(row[started_col]) if started_col is not None else None
        res.attempts.setdefault(key, []).append((order, started, _to_number(row[grade_col])))
        res.emails.setdefault(key, email or row[id_col])
        first = row[first_col] if first_col is not None else ""
        last = row[last_col] if last_col is not None else ""
        res.names.setdefault(key, last if first and last.startswith(first) else f"{first} {last}".strip())
    return res


def pick_attempt(attempts: list[tuple], policy: str) -> float | None:
    graded = [a for a in attempts if a[2] is not None]
    if not graded:
        return None
    if policy == "highest":
        return max(a[2] for a in graded)
    by_time = sorted(graded, key=lambda a: (a[1] is None, a[1] or datetime.min, a[0]))
    if policy == "first":
        return by_time[0][2]
    if policy == "latest":
        return by_time[-1][2]
    raise GradebookError(f"Unknown attempts policy '{policy}'.")


# ─── Class list workbook ──────────────────────────────────────────────────────

@dataclass
class Section:
    ws: object
    label: str
    header_row: int
    columns: dict[str, int]          # header text → column index (1-based)
    last_col: int
    id_col: int
    name_col: int | None
    students: list[tuple[int, str]]  # (row, key)

    def student_id(self, row):
        return str(self.ws.cell(row, self.id_col).value or "").strip()

    def student_name(self, row):
        if self.name_col is None:
            return ""
        return str(self.ws.cell(row, self.name_col).value or "").strip()


def _section_label(ws, header_row: int) -> str:
    for row in ws.iter_rows(min_row=1, max_row=header_row - 1):
        for c in row:
            if isinstance(c.value, str) and _norm(c.value).startswith("section"):
                return c.value.split(":", 1)[-1].strip() or ws.title
    return ws.title


def _find_sections(wb) -> list[Section]:
    sections = []
    for ws in wb.worksheets:
        header_row = id_col = None
        for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 40)):
            for c in row:
                if _norm(c.value) in ("id", "student id", "id number"):
                    header_row, id_col = c.row, c.column
                    break
            if header_row:
                break
        if not header_row:
            continue
        columns, last_col, name_col = {}, id_col, None
        for c in ws[header_row]:
            if c.value is not None and str(c.value).strip():
                title = str(c.value).strip()
                columns.setdefault(title, c.column)
                last_col = max(last_col, c.column)
                if name_col is None and "name" in _norm(title):
                    name_col = c.column
        students = []
        for r in range(header_row + 1, ws.max_row + 1):
            key = key_from_id(ws.cell(r, id_col).value)
            if key:
                students.append((r, key))
        sections.append(Section(ws, _section_label(ws, header_row), header_row, columns,
                                last_col, id_col, name_col, students))
    if not sections:
        raise GradebookError(
            "No class list found: the workbook needs a header row with an 'Id' column "
            "and student IDs like 2021/11262 below it."
        )
    return sections


def _gradable_columns(sections: list[Section]) -> list[dict]:
    """Columns that can hold grades: not '#', not the Id column, and every
    filled student cell is numeric."""
    order: list[str] = []
    numeric: dict[str, bool] = {}
    for s in sections:
        for name, col in s.columns.items():
            if col == s.id_col or name.strip() == "#":
                continue
            if name not in numeric:
                order.append(name)
                numeric[name] = True
            if not numeric[name]:
                continue
            for r, _ in s.students:
                v = s.ws.cell(r, col).value
                if v not in (None, "") and _to_number(v) is None:
                    numeric[name] = False
                    break
    return [{"name": n, "gradable": numeric[n]} for n in order]


def _classlist_keys(sections) -> set[str]:
    return {k for s in sections for _, k in s.students}


def inspect_classlist(data: bytes) -> dict:
    sections = _find_sections(_load_xlsx(data))
    return {
        "sections": len(sections),
        "students": len(_classlist_keys(sections)),
        "columns": _gradable_columns(sections),
    }


# ─── Feature 1: quiz results → class list ────────────────────────────────────

def inspect_results(data: bytes, filename: str, classlist: bytes | None = None) -> dict:
    res = parse_results(data, filename)
    info = {
        "columns": res.headers,
        "grade_column": res.grade_col,
        "out_of": res.out_of,
        "students": len(res.attempts),
        "multi_attempt_students": sum(1 for a in res.attempts.values() if len(a) > 1),
    }
    if classlist is not None:
        keys = _classlist_keys(_find_sections(_load_xlsx(classlist)))
        info["matched"] = len(keys & set(res.attempts))
    return info


def _append_column(s: Section, title: str) -> int:
    """Add a column after the last header column of a section sheet, copying
    the formatting of the previous column and widening edge-aligned merges
    (title, instructor and 'End Of Section' rows)."""
    ws, old, new = s.ws, s.last_col, s.last_col + 1
    for r in range(1, ws.max_row + 1):
        src = ws.cell(r, old)
        if src.has_style:
            ws.cell(r, new)._style = copy(src._style)
    width = ws.column_dimensions[get_column_letter(old)].width
    ws.column_dimensions[get_column_letter(new)].width = max(min(width or 0, 14), 10, len(title) + 2)

    for rng in list(ws.merged_cells.ranges):
        if rng.max_col == old and rng.min_col < old:
            bounds = dict(start_row=rng.min_row, start_column=rng.min_col,
                          end_row=rng.max_row, end_column=new)
            ws.unmerge_cells(str(rng))
            ws.merge_cells(**bounds)

    ws.cell(s.header_row, new).value = title
    s.columns[title] = new
    s.last_col = new
    return new


ATTEMPT_LABELS = {"highest": "Highest grade", "latest": "Latest attempt", "first": "First attempt"}


def add_grades_to_classlist(
    classlist: bytes,
    classlist_name: str,
    results: bytes,
    results_name: str,
    *,
    target_column: str,
    new_column: bool,
    grade_col: int | None = None,
    out_of: float | None = None,
    scale_to: float | None = None,
    round_step: float = 0.01,
    attempts: str = "highest",
) -> tuple[bytes, TaskReport]:
    target_column = (target_column or "").strip()
    if not target_column:
        raise GradebookError("Choose the class list column to put the grades in.")
    if attempts not in ATTEMPT_LABELS:
        raise GradebookError(f"Unknown attempts option '{attempts}'.")

    report = TaskReport("Add grades to class list")
    report.inputs = [("Class list", classlist_name), ("Quiz results", results_name)]

    res = parse_results(results, results_name, grade_col)
    if res.grade_col is None:
        raise GradebookError("Choose which column of the results file holds the grade.")
    out_of = out_of or res.out_of
    if scale_to is not None:
        if not out_of or out_of <= 0:
            raise GradebookError("To rescale, enter what the grade is out of (e.g. 10).")
        if scale_to <= 0:
            raise GradebookError("The new maximum must be greater than 0.")
    report.step("Read quiz results",
                f"{len(res.attempts)} students, "
                f"{sum(len(a) for a in res.attempts.values())} attempt rows; grade column "
                f"'{res.headers[res.grade_col]}'"
                + (f" (out of {out_of:g})" if out_of else ""))

    wb = _load_xlsx(classlist)
    sections = _find_sections(wb)
    report.step("Read class list",
                f"{len(_classlist_keys(sections))} students in {len(sections)} section sheet(s)")

    exists = [s for s in sections if target_column in s.columns]
    if new_column:
        if exists:
            raise GradebookError(f"The class list already has a '{target_column}' column; "
                                 f"pick it from the list instead of adding it.")
    elif not exists:
        raise GradebookError(f"The class list has no '{target_column}' column.")

    report.settings = [
        ("Grade column", res.headers[res.grade_col]),
        ("Out of", f"{out_of:g}" if out_of else "—"),
        ("Rescaled to", f"{scale_to:g}" if scale_to is not None else "No rescaling"),
        ("Rounded to", f"{round_step:g}"),
        ("Several attempts", ATTEMPT_LABELS[attempts]),
        ("Class list column", target_column + (" (new column)" if new_column else "")),
        ("Not in results file", "Set to 0"),
    ]

    final = {k: pick_attempt(a, attempts) for k, a in res.attempts.items()}

    def convert(g: float) -> float:
        if scale_to is not None:
            g = g / out_of * scale_to
        return round_to_step(g, round_step)

    added_to = 0
    log: list[list] = []
    not_in_source: list[list] = []
    updated = zero_missing = zero_no_attempt = over_max = 0
    for s in sections:
        col = s.columns.get(target_column)
        if col is None:
            col = _append_column(s, target_column)
            added_to += 1
        for row, key in s.students:
            cell = s.ws.cell(row, col)
            old = cell.value
            sid, name = s.student_id(row), s.student_name(row)
            if key in final and final[key] is not None:
                raw = final[key]
                if out_of and raw > out_of + 1e-9:
                    over_max += 1
                cell.value = convert(raw)
                status = "Updated"
                updated += 1
            elif key in final:
                raw = None
                cell.value = 0.0
                status = "Set to 0 — no submitted attempt"
                zero_no_attempt += 1
            else:
                raw = None
                cell.value = 0.0
                status = "Set to 0 — not in results file"
                zero_missing += 1
                not_in_source.append([s.label, sid, name])
            log.append([s.label, sid, name, old, raw, cell.value, status])

    if added_to:
        report.step("Add column", f"'{target_column}' added to {added_to} section sheet(s)")

    class_keys = _classlist_keys(sections)
    not_in_dest = [[res.names.get(k, ""), res.emails.get(k, ""), final.get(k)]
                   for k in res.attempts if k not in class_keys]
    not_in_dest.sort(key=lambda r: r[0])

    report.step("Match students",
                f"{updated + zero_no_attempt} of {len(class_keys)} class-list students found in "
                f"the results file", "ok" if not not_in_dest and not zero_missing else "warn")
    report.step("Write grades", f"{updated} grades written to '{target_column}'"
                + (f", rescaled from {out_of:g} to {scale_to:g}" if scale_to is not None else ""))
    if zero_missing:
        report.step("Students not in results file",
                    f"{zero_missing} set to 0 (see sheet 'Not in results file')", "warn")
    if zero_no_attempt:
        report.step("Students with no submitted attempt", f"{zero_no_attempt} set to 0", "warn")
    if not_in_dest:
        report.step("Results not placed",
                    f"{len(not_in_dest)} student(s) in the results file are not in the class "
                    f"list (see sheet 'Not in class list')", "warn")
    if res.unkeyed:
        report.step("Unreadable result rows",
                    f"{len(res.unkeyed)} row(s) without a student e-mail were ignored: "
                    + ", ".join(res.unkeyed[:20]), "warn")
    if over_max:
        report.step("Check grades", f"{over_max} grade(s) are above the maximum {out_of:g}", "warn")

    report.totals = [
        ("Class-list students", len(class_keys)),
        ("Grades written", updated),
        ("Set to 0 (not in results file)", zero_missing),
        ("Set to 0 (no submitted attempt)", zero_no_attempt),
        ("In results but not in class list", len(not_in_dest)),
    ]
    report.sheets = [
        Sheet("Students", ["Section", "ID", "Name", "Old value", "Quiz grade", "New value", "Status"], log),
        Sheet("Not in results file", ["Section", "ID", "Name"], not_in_source,
              note="In the class list but not in the quiz results — given 0."),
        Sheet("Not in class list", ["Name", "Email", "Grade"], not_in_dest,
              note="In the quiz results but not in the class list — not written anywhere."),
    ]

    out = io.BytesIO()
    wb.save(out)
    report.step("Save", "Class list saved")
    return out.getvalue(), report


# ─── Feature 2: class list → grade report (CSV) ──────────────────────────────

def _read_template(data: bytes, filename: str) -> list[list[str]]:
    matrix = read_table(data, filename)
    while matrix and not any(c.strip() for c in matrix[-1]):
        matrix.pop()
    if len(matrix) < 2:
        raise GradebookError("The grade report has no student rows.")
    width = len(matrix[0])
    while width and not matrix[0][width - 1].strip():
        width -= 1
    return [(r + [""] * width)[:width] for r in matrix]


def _template_key_col(matrix: list[list[str]]) -> tuple[int, str]:
    headers = [_norm(h) for h in matrix[0]]
    body = matrix[1:]
    for i, h in enumerate(headers):
        if ("email" in h or "e-mail" in h) and any(key_from_email(r[i]) for r in body):
            return i, "email"
    for i, _ in enumerate(headers):
        if any(key_from_id(r[i]) for r in body):
            return i, "id"
    raise GradebookError("The grade report has no 'Email address' column with student "
                         "e-mails (or an ID column with IDs like 2021/11262).")


def _row_key(value: str, kind: str) -> str | None:
    return key_from_email(value) if kind == "email" else key_from_id(value)


def _report_name(row: list[str], headers: list[str]) -> str:
    low = [_norm(h) for h in headers]
    first = row[low.index("first name")] if "first name" in low else ""
    last = row[low.index("last name")] if "last name" in low else ""
    if first and last.startswith(first):
        return last
    return f"{first} {last}".strip()


def inspect_template(data: bytes, filename: str, classlist: bytes | None = None) -> dict:
    matrix = _read_template(data, filename)
    key_col, kind = _template_key_col(matrix)
    columns = []
    for i, h in enumerate(matrix[0]):
        values = [r[i].strip() for r in matrix[1:] if r[i].strip()]
        numeric = (bool(values) and all(_to_number(v) is not None for v in values)) \
            or _norm(h).endswith("(real)")
        if _norm(h).startswith("last downloaded"):
            numeric = False
        columns.append({"name": h, "numeric": numeric and i != key_col, "key": i == key_col})
    keys = [_row_key(r[key_col], kind) for r in matrix[1:]]
    info = {
        "columns": columns,
        "key_column": matrix[0][key_col],
        "students": sum(1 for k in keys if k),
    }
    if classlist is not None:
        cl = _classlist_keys(_find_sections(_load_xlsx(classlist)))
        info["matched"] = sum(1 for k in keys if k in cl)
    return info


def _to_csv(matrix: list[list[str]]) -> bytes:
    out = io.StringIO()
    csv.writer(out, lineterminator="\r\n").writerows(matrix)
    return out.getvalue().encode("utf-8")


def announce_grades(classlist: bytes, classlist_name: str, template: bytes, template_name: str,
                    config: dict) -> tuple[bytes, TaskReport]:
    """config = {
         "mappings": [{"source": "Quiz", "target": "Quiz: lab … (Real)" | None}],
         "total": {"enabled": bool, "name": "Total Course Work",
                   "columns": [{"kind": "template", "name": ...} |
                               {"kind": "new", "source": ...}]}
       }
    target None → add the class list column as a new report column.
    Returns the updated grade report as CSV bytes."""
    mappings = config.get("mappings") or []
    total = config.get("total") or {}
    if not mappings and not total.get("enabled"):
        raise GradebookError("Select at least one class list column, or the total column.")

    report = TaskReport("Announce grades")
    report.inputs = [("Class list", classlist_name), ("Grade report template", template_name)]

    wb = _load_xlsx(classlist)
    sections = _find_sections(wb)
    cl_columns = {c["name"] for c in _gradable_columns(sections)}

    # key → (section, id, name, {column → value}); first sheet wins on duplicates
    students: dict[str, tuple] = {}
    dup_keys = set()
    for s in sections:
        for row, key in s.students:
            if key in students:
                dup_keys.add(key)
                continue
            vals = {name: s.ws.cell(row, col).value for name, col in s.columns.items()}
            students[key] = (s.label, s.student_id(row), s.student_name(row), vals)
    report.step("Read class list", f"{len(students)} students in {len(sections)} section sheet(s)")

    matrix = _read_template(template, template_name)
    key_col, kind = _template_key_col(matrix)
    orig_headers = list(matrix[0])
    headers = matrix[0]
    rows = matrix[1:]
    key_header = orig_headers[key_col]
    report.step("Read grade report", f"{len(rows)} rows, students identified by '{key_header}'")

    # New columns go before Moodle's trailing "Last downloaded from this course".
    insert_at = len(headers)
    for i, h in enumerate(headers):
        if _norm(h).startswith("last downloaded"):
            insert_at = i
            break

    def add_column(title: str) -> str:
        nonlocal insert_at
        base, n = title, 2
        while title in headers:
            title = f"{base} ({n})"
            n += 1
        headers.insert(insert_at, title)
        for r in rows:
            r.insert(insert_at, "")
        insert_at += 1
        return title

    plan: list[tuple[str, str, bool]] = []   # (source, report column, is_new)
    new_titles: dict[str, str] = {}
    for m in mappings:
        src = str(m.get("source") or "").strip()
        if src not in cl_columns:
            raise GradebookError(f"The class list has no column '{src}'.")
        tgt = m.get("target")
        if tgt:
            if tgt not in headers:
                raise GradebookError(f"The grade report has no column '{tgt}'.")
            if tgt == key_header:
                raise GradebookError(f"'{tgt}' identifies the students and cannot be overwritten.")
            plan.append((src, tgt, False))
        else:
            title = add_column(src)
            new_titles[src] = title
            plan.append((src, title, True))
    if len({t for _, t, _ in plan}) != len(plan):
        raise GradebookError("Two class list columns are mapped to the same report column.")
    for src, tgt, is_new in plan:
        report.step("Map column", f"'{src}' → " + (f"new column '{tgt}'" if is_new
                                                   else f"overrides '{tgt}'"))

    total_title = None
    total_sources: list[str] = []
    if total.get("enabled"):
        for c in total.get("columns") or []:
            if c.get("kind") == "new":
                t = new_titles.get(c.get("source"))
            else:
                t = c.get("name") if c.get("name") in headers else None
            if not t:
                raise GradebookError(f"Total: unknown column {c.get('name') or c.get('source')!r}.")
            total_sources.append(t)
        if not total_sources:
            raise GradebookError("Choose at least one column to add up in the total.")
        name = (total.get("name") or "Total Course Work").strip()
        if name in headers and name not in total_sources and name != key_header:
            total_title = name           # an existing total column is overwritten
        else:
            total_title = add_column(name)
        report.step("Total column", f"'{total_title}' = " + " + ".join(total_sources))

    report.settings = [
        ("Columns", "; ".join(f"{s} → {t}" + (" (new)" if n else "") for s, t, n in plan) or "—"),
        ("Total column", f"{total_title} = " + " + ".join(total_sources) if total_title else "No"),
        ("Not in class list", "Set to 0"),
        ("Output", "CSV (UTF-8, comma separated)"),
    ]

    col_of = {h: i for i, h in enumerate(headers)}
    key_i = col_of[key_header]
    log_headers = ["Name", key_header, "Section", "ID", "Status"]
    for _, t, is_new in plan:
        if not is_new:
            log_headers.append(f"{t} (old)")
        log_headers.append(t)
    if total_title:
        log_headers.append(total_title)

    log, not_in_class, matched_keys = [], [], set()
    written = zeroed_rows = blank_cells = 0
    for r in rows:
        raw_key = r[key_i]
        key = _row_key(raw_key, kind)
        if key is None:
            continue
        name = _report_name(r, headers)
        student = students.get(key)
        if student:
            matched_keys.add(key)
            section, sid, _, vals = student
            status = "Updated"
        else:
            section = sid = ""
            vals = {}
            status = "Set to 0 — not in class list"
            zeroed_rows += 1
            not_in_class.append([name, raw_key])

        entry = [name or (student[2] if student else ""), raw_key, section, sid, status]
        for src, tgt, is_new in plan:
            i = col_of[tgt]
            if not is_new:
                entry.append(r[i])
            v = vals.get(src)
            num = _to_number(v)
            if num is not None:
                r[i] = _display(num)
            elif student and v not in (None, ""):
                r[i] = str(v).strip()
            else:
                if student:
                    blank_cells += 1
                r[i] = _display(0.0)
            written += 1
            entry.append(r[i])
        if total_title:
            s = round_to_step(sum(_to_number(r[col_of[t]]) or 0.0 for t in total_sources), 0.01)
            r[col_of[total_title]] = _display(s)
            entry.append(s)
        log.append(entry)

    not_in_report = sorted(([sec, sid, nm] for k, (sec, sid, nm, _) in students.items()
                            if k not in matched_keys), key=lambda x: (x[0], x[2]))

    report.step("Match students",
                f"{len(matched_keys)} of {len(log)} report students found in the class list",
                "ok" if not not_in_class and not not_in_report else "warn")
    report.step("Write grades", f"{written} cells written")
    if not_in_class:
        report.step("Students not in class list",
                    f"{len(not_in_class)} set to 0 (see sheet 'Not in class list')", "warn")
    if not_in_report:
        report.step("Class-list students not in report",
                    f"{len(not_in_report)} could not be written (see sheet 'Not in grade report')",
                    "warn")
    if blank_cells:
        report.step("Empty class list cells", f"{blank_cells} empty cell(s) written as 0", "warn")
    if dup_keys:
        report.step("Duplicate students",
                    f"{len(dup_keys)} student(s) appear in more than one section sheet; "
                    f"the first sheet's values were used", "warn")

    report.totals = [
        ("Report students", len(log)),
        ("Updated from class list", len(matched_keys)),
        ("Set to 0 (not in class list)", zeroed_rows),
        ("Class-list students not in report", len(not_in_report)),
    ]
    report.sheets = [
        Sheet("Students", log_headers, log),
        Sheet("Not in class list", ["Name", key_header], not_in_class,
              note="In the grade report but not in the class list — given 0."),
        Sheet("Not in grade report", ["Section", "ID", "Name"], not_in_report,
              note="In the class list but not in the grade report — not written anywhere."),
    ]
    data = _to_csv([headers] + rows)
    report.step("Save", "Grade report saved as CSV")
    return data, report
