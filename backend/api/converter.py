"""
docx → Moodle XML converter (API module).

This is the conversion logic, packaged as part of the `api` Django app so the
backend image is fully self-contained — no files outside the backend/ folder
are needed to build or run the service.

Public entry point:
  convert_stream(file_like)  →  str   Moodle XML

Detection priority for the correct answer:
  1. HIGHLIGHT   any background colour on a run or paragraph
  2. BOLD OPTION one option fully bold while others are not
  3. ANSWER TAG  a line like  "Answer: B" / "Ans: C" / "Correct: A"

Supported question-number formats: Q1, 1., 1), Question 1
Supported option formats: A)  A.  (A)  (case-insensitive)
Multiple options per paragraph (Shift-Enter separated) are handled.
OMML (Word equation-editor math) is decoded to plain text.
Embedded images in the question body are base64-embedded into the XML.
"""
from __future__ import annotations

import base64
import os
import re

from docx import Document


# ─── Namespace constants ──────────────────────────────────────────────────────

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
V = "{urn:schemas-microsoft-com:vml}"


# ─── Image discovery ──────────────────────────────────────────────────────────

def _find_image_rids(element):
    rids = []
    for blip in element.iter(f"{A}blip"):
        rid = blip.get(f"{R}embed")
        if rid:
            rids.append(rid)
    for imgd in element.iter(f"{V}imagedata"):
        rid = imgd.get(f"{R}id")
        if rid:
            rids.append(rid)
    return rids


# ─── OMML → plain text ───────────────────────────────────────────────────────

def _omml_to_text(elem):
    tag = elem.tag

    if tag == f"{M}r":
        return "".join(c.text or "" for c in elem if c.tag == f"{M}t")

    if tag == f"{M}sSup":
        base = exp = ""
        for c in elem:
            if c.tag == f"{M}e":   base = _omml_to_text(c)
            elif c.tag == f"{M}sup": exp = _omml_to_text(c)
        return base + exp

    if tag == f"{M}sSub":
        base = sub = ""
        for c in elem:
            if c.tag == f"{M}e":   base = _omml_to_text(c)
            elif c.tag == f"{M}sub": sub = "_" + _omml_to_text(c)
        return base + sub

    if tag == f"{M}d":
        beg, sep, end = "(", "", ")"
        exprs = []
        for c in elem:
            if c.tag == f"{M}dPr":
                for p in c:
                    v = p.get(f"{M}val")
                    if p.tag == f"{M}sepChr": sep = v if v is not None else ""
                    elif p.tag == f"{M}begChr": beg = v if v is not None else ""
                    elif p.tag == f"{M}endChr": end = v if v is not None else ")"
            elif c.tag == f"{M}e":
                exprs.append(_omml_to_text(c))
        return beg + sep.join(exprs) + end

    _SKIP = {
        f"{M}rPr", f"{M}sSupPr", f"{M}sSubPr", f"{M}dPr",
        f"{M}ctrlPr", f"{M}fPr", f"{M}naryPr", f"{M}sty", f"{M}oMathParaPr",
    }
    if tag in _SKIP:
        return ""

    return "".join(_omml_to_text(c) for c in elem)


# ─── Run / paragraph formatting helpers ──────────────────────────────────────

_PLAIN_FILLS = {"auto", "ffffff", "000000", ""}


def _run_is_highlighted(r_elem):
    for child in r_elem:
        if child.tag != f"{W}rPr":
            continue
        for prop in child:
            if prop.tag == f"{W}highlight":
                val = prop.get(f"{W}val", "")
                if val and val.lower() != "none":
                    return True
            if prop.tag == f"{W}shd":
                fill = prop.get(f"{W}fill", "auto")
                if fill.lower() not in _PLAIN_FILLS:
                    return True
    return False


def _run_is_bold(r_elem):
    for child in r_elem:
        if child.tag == f"{W}rPr":
            for prop in child:
                if prop.tag == f"{W}b":
                    return True
    return False


def _para_is_shaded(para):
    for child in para._element:
        if child.tag == f"{W}pPr":
            for prop in child:
                if prop.tag == f"{W}shd":
                    fill = prop.get(f"{W}fill", "auto")
                    if fill.lower() not in _PLAIN_FILLS:
                        return True
    return False


# ─── Segment extraction ───────────────────────────────────────────────────────

def _extract_segments(para):
    base_hl = _para_is_shaded(para)

    segments: list[dict] = []
    parts: list[str] = []
    hl_flag: bool = base_hl
    bold_chars: int = 0
    total_chars: int = 0

    def _flush():
        nonlocal parts, hl_flag, bold_chars, total_chars
        text = re.sub(r"\s+", " ", "".join(parts)).strip()
        if text:
            segments.append({
                "text": text,
                "highlighted": hl_flag,
                "bold_chars": bold_chars,
                "total_chars": total_chars,
                "is_image": False,
                "image_rid": None,
            })
        parts = []
        hl_flag = base_hl
        bold_chars = 0
        total_chars = 0

    for child in para._element:
        tag = child.tag

        if tag == f"{W}r":
            run_hl = _run_is_highlighted(child) or base_hl
            run_bold = _run_is_bold(child)
            run_buf: list[str] = []

            for sub in child:
                if sub.tag == f"{W}t":
                    run_buf.append(sub.text or "")
                elif sub.tag == f"{W}br":
                    pre = "".join(run_buf)
                    if pre:
                        parts.append(pre)
                        n = len(pre.replace(" ", ""))
                        total_chars += n
                        if run_hl:
                            hl_flag = True
                        if run_bold:
                            bold_chars += n
                    run_buf = []
                    _flush()

            rest = "".join(run_buf)
            if rest:
                parts.append(rest)
                n = len(rest.replace(" ", ""))
                total_chars += n
                if run_hl:
                    hl_flag = True
                if run_bold:
                    bold_chars += n

            for rid in _find_image_rids(child):
                _flush()
                segments.append({
                    "text": "",
                    "highlighted": False,
                    "bold_chars": 0,
                    "total_chars": 0,
                    "is_image": True,
                    "image_rid": rid,
                })

        elif tag == f"{M}oMathPara":
            for om in child:
                if om.tag == f"{M}oMath":
                    parts.append(_omml_to_text(om))

        elif tag == f"{M}oMath":
            parts.append(_omml_to_text(child))

    _flush()
    return segments


# ─── Segment classification ───────────────────────────────────────────────────

_OPT_RE = re.compile(r"^\s*(?:\(([A-Da-d])\)|([A-Da-d])\s*[.)]\s*)")

_Q_PATTERNS = [
    re.compile(r"^(Q\d+)\s*$", re.IGNORECASE),
    re.compile(r"^(Q\d+)\s+", re.IGNORECASE),
    re.compile(r"^(\d+)\.\s+"),
    re.compile(r"^(\d+)\)\s+"),
    re.compile(r"^(Question\s+\d+)\b", re.IGNORECASE),
]

_ANS_RE = re.compile(
    r"^\s*(?:Answer|Ans|Correct(?:\s+answer)?)\s*[:\-]?\s*([A-Da-d])\b",
    re.IGNORECASE,
)


def _classify(seg: dict) -> dict:
    if seg.get("is_image"):
        seg["opt_letter"] = None
        seg["is_q_start"] = False
        seg["q_label"] = ""
        seg["q_tail"] = ""
        seg["answer_letter"] = None
        return seg

    text = seg["text"]

    m = _OPT_RE.match(text)
    seg["opt_letter"] = (m.group(1) or m.group(2)).upper() if m else None

    seg["is_q_start"] = False
    seg["q_label"] = ""
    seg["q_tail"] = ""
    for pat in _Q_PATTERNS:
        qm = pat.match(text)
        if qm:
            seg["is_q_start"] = True
            seg["q_label"] = qm.group(1).strip()
            seg["q_tail"] = text[qm.end():].strip()
            break

    am = _ANS_RE.match(text)
    seg["answer_letter"] = am.group(1).upper() if am else None

    return seg


# ─── Block grouping ───────────────────────────────────────────────────────────

def _group_blocks(segs: list[dict]) -> list[list[dict]]:
    if any(s["is_q_start"] for s in segs):
        blocks: list[list[dict]] = []
        cur: list[dict] = []
        for seg in segs:
            if seg["is_q_start"]:
                if cur:
                    blocks.append(cur)
                cur = [seg]
            else:
                cur.append(seg)
        if cur:
            blocks.append(cur)
        return blocks

    blocks = []
    body_buf: list[dict] = []
    cur: list[dict] = []
    last_opt: str | None = None

    for seg in segs:
        opt = seg["opt_letter"]
        if opt == "A" and last_opt not in ("B", "C", "D"):
            if cur:
                blocks.append(cur)
            cur = list(body_buf) + [seg]
            body_buf = []
        elif opt in ("B", "C", "D"):
            cur.append(seg)
        else:
            if cur:
                cur.append(seg)
            else:
                body_buf.append(seg)
        if opt:
            last_opt = opt

    if cur:
        blocks.append(cur)
    return blocks


# ─── Correct-answer detection ─────────────────────────────────────────────────

def _find_correct(options: dict, answer_tag: str | None) -> str | None:
    letters = sorted(options.keys())

    hl = [l for l in letters if options[l]["highlighted"]]
    if len(hl) == 1:
        return hl[0]
    if len(hl) > 1:
        return hl[0]

    def _is_bold_dom(opt):
        tc = opt["total_chars"]
        return tc > 0 and opt["bold_chars"] / tc >= 0.6

    bold = [l for l in letters if _is_bold_dom(options[l])]
    non_bold = [l for l in letters if not _is_bold_dom(options[l])]
    if len(bold) == 1 and len(non_bold) == len(letters) - 1:
        return bold[0]

    if answer_tag and answer_tag in options:
        return answer_tag

    return None


# ─── Block → question dict ────────────────────────────────────────────────────

_OPT_STRIP = re.compile(r"^\s*(?:\([A-Da-d]\)|[A-Da-d]\s*[.)]\s*)", re.IGNORECASE)


def _parse_block(block: list[dict], index: int) -> dict | None:
    body_parts: list[str] = []
    options: dict[str, dict] = {}
    answer_tag: str | None = None
    expl_parts: list[str] = []
    images: list[str] = []
    past_opts: bool = False

    for seg in block:
        if seg.get("is_image"):
            if not past_opts:
                images.append(seg["image_rid"])
            continue

        text = seg["text"]

        if seg["is_q_start"]:
            if seg["q_tail"]:
                body_parts.append(seg["q_tail"])
            continue

        if seg["answer_letter"]:
            answer_tag = seg["answer_letter"]
            past_opts = True
            continue

        if seg["opt_letter"]:
            letter = seg["opt_letter"]
            opt_text = _OPT_STRIP.sub("", text).strip()
            if letter not in options:
                options[letter] = {
                    "text": opt_text,
                    "highlighted": seg["highlighted"],
                    "bold_chars": seg["bold_chars"],
                    "total_chars": seg["total_chars"],
                }
            past_opts = True
            continue

        if text:
            if past_opts:
                expl_parts.append(text)
            else:
                body_parts.append(text)

    first = block[0]
    has_q_start = first["is_q_start"]
    label = first["q_label"] if has_q_start else f"Q{index + 1}"
    n = re.search(r"\d+", label)
    q_num = int(n.group()) if n else index + 1
    if not re.match(r"^Q", label, re.IGNORECASE):
        label = f"Q{q_num}"
    else:
        label = label.upper()

    body_text = re.sub(r"\s+", " ", " ".join(body_parts)).strip()

    # Essay question: has a question label but no MCQ options
    if not {"A", "B", "C", "D"}.issubset(options.keys()):
        if not has_q_start or not body_text:
            return None
        return {
            "type": "essay",
            "num": q_num,
            "label": label,
            "text": body_text,
            "images": images,
        }

    correct = _find_correct(options, answer_tag)
    if correct is None:
        return None

    expl = re.sub(r"\s+", " ", " ".join(expl_parts)).strip()
    expl = re.sub(r"^(?:Explanation:?|Because:?)\s*", "", expl, flags=re.IGNORECASE).strip()

    return {
        "type": "multichoice",
        "num": q_num,
        "label": label,
        "text": body_text,
        "options": [options[l]["text"] for l in ("A", "B", "C", "D")],
        "correct_idx": ord(correct) - ord("A"),
        "explanation": expl,
        "images": images,
    }


# ─── Top-level parser ─────────────────────────────────────────────────────────

def parse_questions(doc) -> list[dict]:
    raw_segs: list[dict] = []
    for para in doc.paragraphs:
        for seg in _extract_segments(para):
            raw_segs.append(_classify(seg))

    blocks = _group_blocks(raw_segs)

    questions: list[dict] = []
    for i, block in enumerate(blocks):
        q = _parse_block(block, i)
        if q:
            questions.append(q)
    return questions


# ─── Moodle XML generator ─────────────────────────────────────────────────────

def _xml_esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


def _question_name(q: dict) -> str:
    # Just the short label — Moodle renders <name> as the page heading,
    # including the question body here would duplicate the text.
    return q["label"]


def _resolve_image(doc, rid):
    try:
        part = doc.part.related_parts[rid]
    except KeyError:
        return None
    if not hasattr(part, "blob"):
        return None
    name = os.path.basename(part.partname)
    data = base64.b64encode(part.blob).decode("ascii")
    return name, data


def _build_image_payload(q: dict, doc) -> tuple[str, list[str]]:
    img_html_parts: list[str] = []
    file_lines: list[str] = []
    seen: set = set()
    used_names: set = set()

    for rid in q.get("images", []):
        if rid in seen or doc is None:
            continue
        seen.add(rid)
        resolved = _resolve_image(doc, rid)
        if resolved is None:
            continue
        name, data = resolved

        unique = name
        i = 1
        while unique in used_names:
            stem, ext = os.path.splitext(name)
            unique = f"{stem}_{i}{ext}"
            i += 1
        used_names.add(unique)

        img_html_parts.append(
            f'<p><img src="@@PLUGINFILE@@/{_xml_esc(unique)}" alt=""/></p>'
        )
        file_lines.append(
            f'      <file name="{_xml_esc(unique)}" path="/" encoding="base64">{data}</file>'
        )

    return "".join(img_html_parts), file_lines


def generate_xml(questions: list[dict], doc=None) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<quiz>",
        "",
    ]

    for q in questions:
        img_html, file_lines = _build_image_payload(q, doc)
        q_type = q.get("type", "multichoice")

        q_body = (
            f"        <p>{q['text']}</p>"
            + (f"\n        {img_html}" if img_html else "")
        )

        lines += [
            f"  <!-- {q['label']} -->",
            f'  <question type="{q_type}">',
            "    <name>",
            f"      <text>{_xml_esc(_question_name(q))}</text>",
            "    </name>",
            '    <questiontext format="html">',
            "      <text><![CDATA[",
            q_body,
            "      ]]></text>",
            *file_lines,
            "    </questiontext>",
            "    <defaultgrade>1</defaultgrade>",
        ]

        if q_type == "essay":
            lines += [
                "    <responseformat>editor</responseformat>",
                "    <responserequired>0</responserequired>",
                "    <responsefieldlines>10</responsefieldlines>",
                "    <attachments>0</attachments>",
                "    <attachmentsrequired>0</attachmentsrequired>",
                '    <graderinfo format="html"><text></text></graderinfo>',
            ]
        else:
            fb_correct = f"Correct! {q['explanation']}" if q["explanation"] else "Correct!"
            lines += [
                "    <shuffleanswers>0</shuffleanswers>",
                "    <single>1</single>",
                "    <answernumbering>ABCD</answernumbering>",
                "",
            ]
            for i, opt in enumerate(q["options"]):
                fraction = 100 if i == q["correct_idx"] else 0
                fb = _xml_esc(fb_correct) if fraction == 100 else ""
                lines += [
                    f'    <answer fraction="{fraction}">',
                    f"      <text>{_xml_esc(opt)}</text>",
                    f"      <feedback><text>{fb}</text></feedback>",
                    "    </answer>",
                ]

        lines += ["  </question>", ""]

    lines.append("</quiz>")
    return "\n".join(lines)


# ─── Public entry point ──────────────────────────────────────────────────────

def convert_stream(docx_fileobj) -> str:
    """Convert a .docx (path or file-like) to Moodle XML string.
    Raises ValueError if the file contains no parseable MCQ questions."""
    doc = Document(docx_fileobj)
    questions = parse_questions(doc)
    if not questions:
        raise ValueError(
            "No questions parsed. For MCQ questions make sure the file has options "
            "A) B) C) D) and a highlighted / bold / 'Answer: X' marker. "
            "For essay questions make sure each question starts with a label (Q1, 1., etc.) "
            "and contains body text but no A/B/C/D options."
        )
    return generate_xml(questions, doc)
