"""
API views for the docx → Moodle-XML converter.

POST /api/convert/            multipart form, field "file" — .docx upload
                              response: the Moodle XML as an attachment download
                              (warnings are written as XML comments at the top,
                              counts in X-Question-Count / X-Warning-Count)
POST /api/convert/?report=1   same, but responds with JSON:
                              {filename, xml, question_count, mcq_count,
                               essay_count, warnings: [..]}
GET  /api/health/             sanity check

Errors are JSON: {error, warnings?} with status 400 / 422 / 500.
"""
import io
import logging
import os
import zipfile

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .converter import convert

log = logging.getLogger(__name__)


@require_GET
def health(_request):
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
def convert_docx(request):
    upload = request.FILES.get("file")
    if upload is None:
        return JsonResponse(
            {"error": "No file uploaded. Send a multipart POST with field 'file'."},
            status=400,
        )

    name = upload.name or "upload.docx"
    if not name.lower().endswith(".docx"):
        return JsonResponse({"error": "File must be a .docx document."}, status=400)

    data = upload.read()

    try:
        result = convert(io.BytesIO(data))
    except zipfile.BadZipFile:
        return JsonResponse(
            {"error": "The file is not a valid .docx document (it may be an old .doc "
                      "renamed to .docx, or corrupted)."},
            status=422,
        )
    except ValueError as exc:
        return JsonResponse(
            {"error": str(exc), "warnings": getattr(exc, "warnings", [])},
            status=422,
        )
    except Exception:
        log.exception("Conversion failed for %r", name)
        return JsonResponse(
            {"error": "Conversion failed due to an unexpected error in the document."},
            status=500,
        )

    out_name = os.path.splitext(os.path.basename(name))[0] + "_moodle.xml"
    mcq = sum(1 for q in result.questions if q["type"] == "multichoice")
    essay = len(result.questions) - mcq

    if request.GET.get("report") == "1":
        return JsonResponse({
            "filename": out_name,
            "xml": result.xml,
            "question_count": len(result.questions),
            "mcq_count": mcq,
            "essay_count": essay,
            "warnings": result.warnings,
        })

    response = HttpResponse(result.xml, content_type="application/xml; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{_header_safe(out_name)}"'
    response["X-Question-Count"] = str(len(result.questions))
    response["X-Warning-Count"] = str(len(result.warnings))
    response["Access-Control-Expose-Headers"] = (
        "Content-Disposition, X-Question-Count, X-Warning-Count"
    )
    return response


def _header_safe(filename: str) -> str:
    """ASCII-only filename for the Content-Disposition header."""
    safe = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in filename)
    return safe or "converted_moodle.xml"
