"""
API views for the docx → Moodle-XML converter.

POST /api/convert/   multipart form, field "file" — .docx upload
                     response: the Moodle XML as an attachment download
GET  /api/health/    sanity check
"""
import io
import os
import sys
from pathlib import Path

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

# Import the existing converter from the project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import docx_to_moodle_xml as converter  # noqa: E402


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
        return JsonResponse(
            {"error": "File must be a .docx document."},
            status=400,
        )

    # Read fully into memory — docx files are small
    data = upload.read()

    try:
        xml_text = converter.convert_stream(io.BytesIO(data))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=422)
    except Exception as exc:  # malformed docx, zipfile error, etc.
        return JsonResponse(
            {"error": f"Conversion failed: {exc.__class__.__name__}: {exc}"},
            status=500,
        )

    out_name = os.path.splitext(os.path.basename(name))[0] + "_moodle.xml"
    response = HttpResponse(xml_text, content_type="application/xml; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{out_name}"'
    response["Access-Control-Expose-Headers"] = "Content-Disposition"
    return response
