"""
Gradebook API.

POST /api/grades/inspect/
    multipart, any of: classlist (.xlsx), results (.csv/.xlsx/.ods),
    template (.ods/.csv/.xlsx — the Moodle gradebook export)
    → {classlist?: {...}, results?: {...}, template?: {...}}  (column lists, counts,
      match counts when the class list is sent too)

POST /api/grades/add-to-classlist/
    classlist, results files + fields:
      target_column, new_column ("1"/"0"), grade_column (index), out_of,
      scale_to (empty = no rescale), round_step, attempts (highest|latest|first)
    → {file: {name, data}, report: {name, data}, totals, steps}

POST /api/grades/announce/
    classlist, template files + field config (JSON, see gradebook.announce_grades)
    → same shape as above; the file is the updated grade report as CSV

Files are returned base64-encoded so the UI can show the summary and offer
both downloads (the edited file and the task report).
"""
import base64
import json
import logging
import os
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import gradebook
from .gradebook import GradebookError

log = logging.getLogger(__name__)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV = "text/csv"
TEMPLATE_EXTS = (".ods", ".csv", ".xlsx")


def _file(request, field, exts, required=True):
    f = request.FILES.get(field)
    if f is None:
        if required:
            raise GradebookError(f"Missing file '{field}'.")
        return None, None
    name = f.name or field
    if not name.lower().endswith(exts):
        raise GradebookError(f"'{name}' must be one of: {', '.join(exts)}.")
    return f.read(), name


def _number(request, field, default=None):
    raw = (request.POST.get(field) or "").strip().replace(",", ".")
    if raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise GradebookError(f"'{raw}' is not a valid number for {field.replace('_', ' ')}.")


def _guarded(fn):
    def wrapper(request):
        try:
            return fn(request)
        except GradebookError as exc:
            return JsonResponse({"error": str(exc)}, status=422)
        except Exception:
            log.exception("Gradebook request failed")
            return JsonResponse({"error": "Unexpected error while processing the files."},
                                status=500)
    wrapper.__name__ = fn.__name__
    return csrf_exempt(require_POST(wrapper))


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _response(out_bytes, out_name, out_type, report):
    stamp = timezone.localtime().strftime("%Y-%m-%d %H-%M")
    report_bytes = report.to_xlsx()
    return JsonResponse({
        "file": {"name": out_name, "type": out_type, "data": _b64(out_bytes)},
        "report": {"name": f"Task Report - {report.task} - {stamp}.xlsx", "type": XLSX,
                   "data": _b64(report_bytes)},
        "totals": [[k, v] for k, v in report.totals],
        "steps": [list(s) for s in report.steps],
    })


@_guarded
def inspect(request):
    classlist, _ = _file(request, "classlist", (".xlsx",), required=False)
    results, results_name = _file(request, "results", (".csv", ".xlsx", ".ods"), required=False)
    template, template_name = _file(request, "template", TEMPLATE_EXTS, required=False)
    out = {}
    if classlist is not None:
        out["classlist"] = gradebook.inspect_classlist(classlist)
    if results is not None:
        out["results"] = gradebook.inspect_results(results, results_name, classlist)
    if template is not None:
        out["template"] = gradebook.inspect_template(template, template_name, classlist)
    return JsonResponse(out)


@_guarded
def add_to_classlist(request):
    classlist, classlist_name = _file(request, "classlist", (".xlsx",))
    results, results_name = _file(request, "results", (".csv", ".xlsx", ".ods"))
    grade_col = request.POST.get("grade_column", "").strip()
    attempts = request.POST.get("attempts", "highest")
    data, report = gradebook.add_grades_to_classlist(
        classlist, classlist_name, results, results_name,
        target_column=request.POST.get("target_column", ""),
        new_column=request.POST.get("new_column") == "1",
        grade_col=int(grade_col) if grade_col.isdigit() else None,
        out_of=_number(request, "out_of"),
        scale_to=_number(request, "scale_to"),
        round_step=_number(request, "round_step", 0.01),
        attempts=attempts,
    )
    return _response(data, os.path.basename(classlist_name), XLSX, report)


@_guarded
def announce(request):
    classlist, classlist_name = _file(request, "classlist", (".xlsx",))
    template, template_name = _file(request, "template", TEMPLATE_EXTS)
    try:
        config = json.loads(request.POST.get("config") or "{}")
    except json.JSONDecodeError:
        raise GradebookError("Invalid configuration.")
    if not isinstance(config, dict):
        raise GradebookError("Invalid configuration.")
    data, report = gradebook.announce_grades(classlist, classlist_name,
                                             template, template_name, config)
    out_name = os.path.splitext(os.path.basename(template_name))[0] + ".csv"
    return _response(data, out_name, CSV, report)
