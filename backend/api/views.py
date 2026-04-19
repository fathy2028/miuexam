"""
API views for the docx → Moodle-XML converter.

POST /api/convert/   multipart form, field "file" — .docx upload
                     response: the Moodle XML as an attachment download
GET  /api/health/    sanity check
"""
import io
import os

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .converter import convert_stream


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

    data = upload.read()

    try:
        xml_text = convert_stream(io.BytesIO(data))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=422)
    except Exception as exc:
        return JsonResponse(
            {"error": f"Conversion failed: {exc.__class__.__name__}: {exc}"},
            status=500,
        )

    out_name = os.path.splitext(os.path.basename(name))[0] + "_moodle.xml"
    response = HttpResponse(xml_text, content_type="application/xml; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{out_name}"'
    response["Access-Control-Expose-Headers"] = "Content-Disposition"
    return response
