"""
"Task report" workbook produced alongside every gradebook operation:

  Summary              task, date, input files, settings, step-by-step progress
  Students             one row per destination student: old → new value, status
  <unmatched sheets>   students that could not be matched, per direction
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from django.utils import timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

_HEAD_FILL = PatternFill("solid", fgColor="D9E2F3")
_TITLE_FONT = Font(bold=True, size=14)
_BOLD = Font(bold=True)
_STATUS_FILLS = {
    "ok": PatternFill("solid", fgColor="E2F0D9"),
    "warn": PatternFill("solid", fgColor="FFF2CC"),
    "error": PatternFill("solid", fgColor="F8CBAD"),
}


@dataclass
class Sheet:
    title: str
    headers: list[str]
    rows: list[list] = field(default_factory=list)
    note: str = ""


@dataclass
class TaskReport:
    task: str
    inputs: list[tuple[str, str]] = field(default_factory=list)     # (label, file name)
    settings: list[tuple[str, str]] = field(default_factory=list)   # (label, value)
    steps: list[tuple[str, str, str]] = field(default_factory=list) # (step, result, ok|warn|error)
    totals: list[tuple[str, object]] = field(default_factory=list)  # (label, number)
    sheets: list[Sheet] = field(default_factory=list)

    def step(self, name: str, result: str, status: str = "ok"):
        self.steps.append((name, result, status))

    def to_xlsx(self) -> bytes:
        wb = Workbook()
        ws = wb.active
        ws.title = "Summary"
        ws["A1"] = self.task
        ws["A1"].font = _TITLE_FONT
        ws["A2"] = "Generated " + timezone.localtime().strftime("%Y-%m-%d %H:%M")
        r = 4

        def section(title, rows, headers=None):
            nonlocal r
            ws.cell(r, 1, title).font = _BOLD
            r += 1
            if headers:
                for i, h in enumerate(headers, 1):
                    c = ws.cell(r, i, h)
                    c.font = _BOLD
                    c.fill = _HEAD_FILL
                r += 1
            for row in rows:
                for i, v in enumerate(row, 1):
                    ws.cell(r, i, v)
                r += 1
            r += 1

        section("Input files", self.inputs)
        section("Settings", self.settings)
        section("Results", self.totals)

        ws.cell(r, 1, "Progress").font = _BOLD
        r += 1
        for i, h in enumerate(("#", "Step", "Result", "Status"), 1):
            c = ws.cell(r, i, h)
            c.font = _BOLD
            c.fill = _HEAD_FILL
        r += 1
        for n, (name, result, status) in enumerate(self.steps, 1):
            ws.cell(r, 1, n)
            ws.cell(r, 2, name)
            ws.cell(r, 3, result).alignment = Alignment(wrap_text=True, vertical="top")
            c = ws.cell(r, 4, {"ok": "Done", "warn": "Check", "error": "Failed"}[status])
            c.fill = _STATUS_FILLS[status]
            r += 1

        ws.column_dimensions["A"].width = 26
        ws.column_dimensions["B"].width = 34
        ws.column_dimensions["C"].width = 80
        ws.column_dimensions["D"].width = 10

        for sheet in self.sheets:
            self._add_table(wb, sheet)

        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()

    @staticmethod
    def _add_table(wb, sheet: Sheet):
        ws = wb.create_sheet(sheet.title[:31])
        start = 1
        if sheet.note:
            ws["A1"] = sheet.note
            ws["A1"].font = Font(italic=True)
            start = 3
        for i, h in enumerate(sheet.headers, 1):
            c = ws.cell(start, i, h)
            c.font = _BOLD
            c.fill = _HEAD_FILL
        for j, row in enumerate(sheet.rows, start + 1):
            for i, v in enumerate(row, 1):
                ws.cell(j, i, v)
        if not sheet.rows:
            ws.cell(start + 1, 1, "(none)").font = Font(italic=True)
        for i, h in enumerate(sheet.headers, 1):
            longest = max([len(str(h))] + [len(str(r[i - 1])) for r in sheet.rows[:500]
                                           if i - 1 < len(r) and r[i - 1] is not None])
            ws.column_dimensions[get_column_letter(i)].width = min(max(10, longest + 2), 60)
        ws.freeze_panes = ws.cell(start + 1, 1)
        ws.auto_filter.ref = f"A{start}:{get_column_letter(len(sheet.headers))}{start + max(1, len(sheet.rows))}"
