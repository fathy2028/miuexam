"""
docx → Moodle XML converter (API module).

This is the conversion logic, packaged as part of the `api` Django app so the
backend image is fully self-contained — no files outside the backend/ folder
are needed to build or run the service.

Public entry points:
  convert(file_like)         →  ConversionResult(xml, questions, warnings)
  convert_stream(file_like)  →  str   Moodle XML (warnings discarded)

Correct-answer detection (first signal that applies wins):
  1. ANSWER TAG  a line like  "Answer: B" / "Ans: C" / "Correct: A"
  2. HIGHLIGHT   exactly one option highlighted / shaded
  3. BOLD        exactly one option mostly bold while the others are not
Conflicting or ambiguous signals are reported as warnings.

Supported question-number formats: Q1, Question 1, 1., 1)
Supported option formats: A)  A.  (A)  — options A–H, must start at A and run
in order. Multiple options per paragraph (Shift-Enter separated) are handled.
Paragraphs inside tables, hyperlinks, content controls and tracked insertions
are read in document order.
OMML (Word equation-editor math) is decoded to plain text.
Embedded images are base64-embedded into the XML: images in the question body
go to the question text, images right after an option go to that option, and
images after the options go to the correct-answer feedback.

Anything that cannot be converted is skipped *and reported* in
ConversionResult.warnings — nothing is dropped silently.
"""
from __future__ import annotations

import base64
import html
import os
import re
from dataclasses import dataclass, field

from docx import Document


# ─── Namespace constants ──────────────────────────────────────────────────────

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
V = "{urn:schemas-microsoft-com:vml}"

OPTION_LETTERS = "ABCDEFGH"


@dataclass
class ConversionResult:
    xml: str
    questions: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ─── Document traversal ──────────────────────────────────────────────────────

def _iter_paragraphs(container):
    """Yield every <w:p> under a body/cell in document order, descending into
    tables (incl. nested) and block-level content controls."""
    for child in container:
        tag = child.tag
        if tag == f"{W}p":
            yield child
        elif tag == f"{W}tbl":
            for tr in child.iter(f"{W}tr"):
                # iter() also reaches rows of nested tables; those are
                # visited when recursing into their cell instead.
                if _nearest_table(tr) is not child:
                    continue
                for tc in tr.iter(f"{W}tc"):
                    if _nearest_row(tc) is tr:
                        yield from _iter_paragraphs(tc)
        elif tag in (f"{W}sdt", f"{W}customXml"):
            content = child.find(f"{W}sdtContent")
            yield from _iter_paragraphs(content if content is not None else child)


def _nearest_ancestor(elem, tag):
    node = elem.getparent()
    while node is not None and node.tag != tag:
        node = node.getparent()
    return node


def _nearest_table(elem):
    return _nearest_ancestor(elem, f"{W}tbl")


def _nearest_row(elem):
    return _nearest_ancestor(elem, f"{W}tr")


# Inline wrappers whose child runs are part of the visible text.
_INLINE_CONTAINERS = {
    f"{W}hyperlink", f"{W}ins", f"{W}smartTag", f"{W}fldSimple",
    f"{W}customXml", f"{W}sdt", f"{W}sdtContent", f"{W}moveTo",
}


def _iter_inline(p_elem):
    """Yield runs and math objects of a paragraph in order, flattening
    hyperlinks, tracked insertions, content controls, etc."""
    for child in p_elem:
        tag = child.tag
        if tag in (f"{W}r", f"{M}oMath", f"{M}oMathPara"):
            yield child
        elif tag in _INLINE_CONTAINERS:
            yield from _iter_inline(child)


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
_OFF_VALUES = {"0", "false", "off", "none"}


def _toggle_on(prop) -> bool:
    """OOXML toggle properties (<w:b/>, <w:b w:val="0"/> …): absent val = on."""
    val = prop.get(f"{W}val")
    return val is None or val.lower() not in _OFF_VALUES


def _run_is_highlighted(r_elem):
    rpr = r_elem.find(f"{W}rPr")
    if rpr is None:
        return False
    for prop in rpr:
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
    rpr = r_elem.find(f"{W}rPr")
    if rpr is None:
        return False
    b = rpr.find(f"{W}b")
    return b is not None and _toggle_on(b)


def _para_is_shaded(p_elem):
    ppr = p_elem.find(f"{W}pPr")
    if ppr is None:
        return False
    shd = ppr.find(f"{W}shd")
    if shd is None:
        return False
    return shd.get(f"{W}fill", "auto").lower() not in _PLAIN_FILLS


# ─── Segment extraction ───────────────────────────────────────────────────────

def _extract_segments(p_elem):
    base_hl = _para_is_shaded(p_elem)

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

    def _take(text, run_hl, run_bold):
        nonlocal hl_flag, bold_chars, total_chars
        if not text:
            return
        parts.append(text)
        n = len(re.sub(r"\s", "", text))
        total_chars += n
        if run_hl and n:
            hl_flag = True
        if run_bold:
            bold_chars += n

    for child in _iter_inline(p_elem):
        tag = child.tag

        if tag == f"{W}r":
            run_hl = _run_is_highlighted(child) or base_hl
            run_bold = _run_is_bold(child)
            run_buf: list[str] = []

            for sub in child:
                if sub.tag == f"{W}t":
                    run_buf.append(sub.text or "")
                elif sub.tag == f"{W}tab":
                    run_buf.append(" ")
                elif sub.tag in (f"{W}br", f"{W}cr"):
                    _take("".join(run_buf), run_hl, run_bold)
                    run_buf = []
                    _flush()

            _take("".join(run_buf), run_hl, run_bold)

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

# "A) x", "A ) x", "A)x", "(A) x", "A. x" — but not "e.g." / "a.k.a."
_OPT_RE = re.compile(
    r"^\s*(?:\(\s*([A-H])\s*\)|([A-H])\s?\)|([A-H])\s?\.(?=\s|$))\s*",
    re.IGNORECASE,
)

# (pattern, style) — "named" labels (Q1, Question 1) vs bare "numeric" ones.
_Q_PATTERNS = [
    (re.compile(r"^(Q\s?(\d+))\s*[.):\-]?(?:\s+|$)", re.IGNORECASE), "named"),
    (re.compile(r"^(Question\s+(\d+))\s*[.):\-]?(?:\s+|$)", re.IGNORECASE), "named"),
    (re.compile(r"^((\d+))[.)]\s+"), "numeric"),
    (re.compile(r"^((\d+))[.)]\s*$"), "numeric"),
]

_ANS_RE = re.compile(
    r"^\s*(?:Answer|Ans|Correct(?:\s+answer)?)\s*[:\-]?\s*\(?\s*([A-H])\s*\)?(?![A-Za-z])",
    re.IGNORECASE,
)

_EXPL_RE = re.compile(
    r"^\s*(?:Explanation|Because|Reason|Rationale|Feedback)\b\s*[:\-]?\s*",
    re.IGNORECASE,
)


def _classify(seg: dict) -> dict:
    seg.update(opt_letter=None, opt_text="", is_q_start=False, q_label="",
               q_num=None, q_style=None, q_tail="", answer_letter=None,
               is_expl=False)
    if seg["is_image"]:
        return seg

    text = seg["text"]

    m = _OPT_RE.match(text)
    if m:
        seg["opt_letter"] = (m.group(1) or m.group(2) or m.group(3)).upper()
        seg["opt_text"] = text[m.end():].strip()

    for pat, style in _Q_PATTERNS:
        qm = pat.match(text)
        if qm:
            seg["is_q_start"] = True
            seg["q_label"] = qm.group(1).strip()
            seg["q_num"] = int(qm.group(2))
            seg["q_style"] = style
            seg["q_tail"] = text[qm.end():].strip()
            break

    am = _ANS_RE.match(text)
    if am:
        seg["answer_letter"] = am.group(1).upper()
        # An answer line is never an option or a question label.
        seg["opt_letter"] = None
        seg["is_q_start"] = False

    seg["is_expl"] = bool(_EXPL_RE.match(text))
    return seg


# ─── Block grouping ───────────────────────────────────────────────────────────

def _confirm_labels(segs: list[dict]) -> None:
    """Decide which label-looking lines really start a new question.

    * If the document uses named labels (Q1 / Question 1), bare "1." lines are
      ordinary text (e.g. numbered points inside an explanation).
    * A label only starts a question when its number continues the sequence
      (n == previous + 1). Numbering may restart at 1 once the current question
      already has options or an answer line (a new section).
    Rejected labels are demoted to plain text.
    """
    labelled = [s for s in segs if s["is_q_start"]]
    if not labelled:
        return
    style = "named" if any(s["q_style"] == "named" for s in labelled) else "numeric"

    last_num: int | None = None
    block_has_opts = False
    for seg in segs:
        if seg["is_q_start"]:
            n = seg["q_num"]
            ok = seg["q_style"] == style and (
                last_num is None
                or n == last_num + 1
                or (n == 1 and block_has_opts)
            )
            if ok:
                last_num = n
                block_has_opts = False
                continue
            seg["is_q_start"] = False
        if seg["opt_letter"] or seg["answer_letter"]:
            block_has_opts = True


def _group_blocks(segs: list[dict]) -> list[list[dict]]:
    _confirm_labels(segs)

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

    # No question labels: a question starts at each "A)" option. Text after a
    # question's options is held in `pending` until the next "A)": the part up
    # to the last Answer/Explanation line belongs to the previous question,
    # the rest is the body of the next one.
    blocks = []
    cur: list[dict] = []
    pending: list[dict] = []
    in_options = False

    def _split_pending():
        cut = 0
        for i, s in enumerate(pending):
            if s["answer_letter"] or s["is_expl"]:
                cut = i + 1
        return pending[:cut], pending[cut:]

    for seg in segs:
        opt = seg["opt_letter"]
        if opt == "A":
            tail, body = _split_pending()
            if cur:
                cur.extend(tail)
                blocks.append(cur)
            elif tail:
                body = tail + body
            cur = body + [seg]
            pending = []
            in_options = True
        elif opt and in_options and all(s["is_image"] for s in pending):
            # next option: images held since the previous option belong to it
            cur.extend(pending)
            pending = []
            cur.append(seg)
        else:
            if not seg["is_image"]:
                in_options = False
            pending.append(seg)

    if cur:
        cur.extend(pending)
        blocks.append(cur)
    return blocks


# ─── Correct-answer detection ─────────────────────────────────────────────────

def _find_correct(options: dict, answer_tag: str | None, label: str,
                  warnings: list[str]) -> str | None:
    letters = list(options)

    hl = [l for l in letters if options[l]["highlighted"]]
    if len(hl) == len(letters):
        hl = []  # whole block highlighted → carries no information

    def _is_bold_dom(opt):
        tc = opt["total_chars"]
        return tc > 0 and opt["bold_chars"] / tc >= 0.6

    bold = [l for l in letters if _is_bold_dom(options[l])]
    bold_pick = bold[0] if len(bold) == 1 else None

    if answer_tag:
        if answer_tag not in options:
            warnings.append(
                f"{label}: 'Answer: {answer_tag}' refers to an option that does not "
                f"exist (options are {letters[0]}–{letters[-1]}); skipped."
            )
            return None
        if len(hl) == 1 and hl[0] != answer_tag:
            warnings.append(
                f"{label}: answer line says {answer_tag} but option {hl[0]} is "
                f"highlighted; used {answer_tag}."
            )
        return answer_tag

    if len(hl) == 1:
        if bold_pick and bold_pick != hl[0]:
            warnings.append(
                f"{label}: option {hl[0]} is highlighted but option {bold_pick} is "
                f"bold; used the highlighted option {hl[0]}."
            )
        return hl[0]

    if len(hl) > 1:
        warnings.append(
            f"{label}: several options are highlighted ({', '.join(hl)}) and there is "
            f"no 'Answer: X' line; skipped. Highlight only the correct option."
        )
        return None

    if bold_pick:
        return bold_pick

    if len(bold) > 1:
        warnings.append(
            f"{label}: several options are bold ({', '.join(bold)}) and there is no "
            f"highlight or 'Answer: X' line; skipped."
        )
    else:
        warnings.append(
            f"{label}: no correct answer marked (highlight, bold or 'Answer: X'); skipped."
        )
    return None


# ─── Block → question dict ────────────────────────────────────────────────────

def _parse_block(block: list[dict], index: int, warnings: list[str]) -> dict | None:
    body_parts: list[str] = []
    options: dict[str, dict] = {}
    answer_tag: str | None = None
    expl_parts: list[str] = []
    images: list[str] = []
    feedback_images: list[str] = []
    problems: list[str] = []
    zone = "body"           # body → options → after
    last_letter: str | None = None

    for seg in block:
        if seg["is_image"]:
            if zone == "body":
                images.append(seg["image_rid"])
            elif zone == "options":
                options[last_letter]["images"].append(seg["image_rid"])
            else:
                feedback_images.append(seg["image_rid"])
            continue

        text = seg["text"]

        if seg["is_q_start"]:
            if seg["q_tail"]:
                body_parts.append(seg["q_tail"])
            continue

        if seg["answer_letter"]:
            answer_tag = seg["answer_letter"]
            if options:
                zone = "after"
            continue

        letter = seg["opt_letter"]
        if letter and zone != "after":
            expected = OPTION_LETTERS[len(options)] if len(options) < len(OPTION_LETTERS) else None
            if letter == expected:
                options[letter] = {
                    "text": seg["opt_text"],
                    "highlighted": seg["highlighted"],
                    "bold_chars": seg["bold_chars"],
                    "total_chars": seg["total_chars"],
                    "images": [],
                }
                zone = "options"
                last_letter = letter
                continue
            if zone == "options":
                problems.append(
                    f"option {letter} appears out of order after option {last_letter}"
                )
                continue
            # zone == "body" and letter != "A": ordinary text such as "C. elegans …"

        if zone == "options":
            zone = "after"
        if zone == "after":
            expl_parts.append(text)
        else:
            body_parts.append(text)

    first = block[0]
    has_q_start = first["is_q_start"]
    q_num = first["q_num"] if has_q_start else index + 1
    label = f"Q{q_num}"

    body_paras = [re.sub(r"\s+", " ", p).strip() for p in body_parts if p.strip()]

    if problems:
        warnings.append(f"{label}: {'; '.join(problems)}; skipped.")
        return None

    if not options:
        if not has_q_start:
            if answer_tag:
                warnings.append(
                    f"Text before the first question has an answer line "
                    f"('Answer: {answer_tag}') but no options were recognised; ignored."
                )
            return None  # preamble / title text
        if answer_tag:
            warnings.append(
                f"{label}: has 'Answer: {answer_tag}' but no options were recognised "
                f"(use A) B) C) …); skipped."
            )
            return None
        if not body_paras and not images:
            warnings.append(f"{label}: question label with no text; skipped.")
            return None
        return {
            "type": "essay",
            "num": q_num,
            "label": label,
            "paragraphs": body_paras,
            "images": images,
        }

    if len(options) < 2:
        warnings.append(f"{label}: only option A was found; skipped.")
        return None

    empty = [l for l, o in options.items() if not o["text"] and not o["images"]]
    if empty:
        warnings.append(f"{label}: option {', '.join(empty)} has no text; skipped.")
        return None

    if not body_paras and not images:
        warnings.append(f"{label}: the question text is empty (options only).")

    correct = _find_correct(options, answer_tag, label, warnings)
    if correct is None:
        return None

    expl = re.sub(r"\s+", " ", " ".join(expl_parts)).strip()
    expl = _EXPL_RE.sub("", expl).strip()

    letters = list(options)
    return {
        "type": "multichoice",
        "num": q_num,
        "label": label,
        "paragraphs": body_paras,
        "options": [options[l]["text"] for l in letters],
        "option_images": [options[l]["images"] for l in letters],
        "correct_idx": letters.index(correct),
        "explanation": expl,
        "images": images,
        "feedback_images": feedback_images,
    }


# ─── Top-level parser ─────────────────────────────────────────────────────────

def parse_questions(doc, warnings: list[str] | None = None) -> list[dict]:
    if warnings is None:
        warnings = []

    raw_segs: list[dict] = []
    for p_elem in _iter_paragraphs(doc.element.body):
        for seg in _extract_segments(p_elem):
            raw_segs.append(_classify(seg))

    blocks = _group_blocks(raw_segs)

    questions: list[dict] = []
    for i, block in enumerate(blocks):
        q = _parse_block(block, i, warnings)
        if q:
            questions.append(q)

    seen: dict[str, int] = {}
    for q in questions:
        seen[q["label"]] = seen.get(q["label"], 0) + 1
    dups = sorted((l for l, n in seen.items() if n > 1), key=lambda l: int(l[1:]))
    if dups:
        warnings.append(
            f"Duplicate question numbers: {', '.join(dups)} (they are imported as "
            f"separate questions with the same name)."
        )
    return questions


# ─── Moodle XML generator ─────────────────────────────────────────────────────

def _xml_esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


def _html(text: str) -> str:
    """Plain document text → HTML-safe text (Moodle renders these as HTML)."""
    return html.escape(text, quote=False)


def _cdata(content: str) -> str:
    # "]]>" cannot occur inside a CDATA section — split it across two.
    return "<![CDATA[" + content.replace("]]>", "]]]]><![CDATA[>") + "]]>"


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


def _image_payload(rids: list[str], doc, indent: str) -> tuple[str, list[str]]:
    """Return (img HTML, <file> lines) for the given image relationship ids."""
    img_html_parts: list[str] = []
    file_lines: list[str] = []
    seen: set = set()
    used_names: set = set()

    for rid in rids:
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

        img_html_parts.append(f'<img src="@@PLUGINFILE@@/{_xml_esc(unique)}" alt=""/>')
        file_lines.append(
            f'{indent}<file name="{_xml_esc(unique)}" path="/" encoding="base64">{data}</file>'
        )

    return "".join(img_html_parts), file_lines


def _xml_comment(text: str) -> str:
    # "--" is not allowed inside XML comments.
    return "<!-- " + text.replace("--", "- -") + " -->"


def generate_xml(questions: list[dict], doc=None, warnings: list[str] | None = None) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    if warnings:
        lines.append(_xml_comment(f"Conversion warnings ({len(warnings)}):"))
        lines += [_xml_comment(w) for w in warnings]
    lines += ["<quiz>", ""]

    for q in questions:
        q_type = q["type"]
        img_html, file_lines = _image_payload(q.get("images", []), doc, "      ")

        q_body = "".join(f"<p>{_html(p)}</p>" for p in q["paragraphs"])
        if img_html:
            q_body += f"<p>{img_html}</p>"

        lines += [
            f"  {_xml_comment(q['label'])}",
            f'  <question type="{q_type}">',
            "    <name>",
            f"      <text>{_xml_esc(q['label'])}</text>",
            "    </name>",
            '    <questiontext format="html">',
            f"      <text>{_cdata(q_body)}</text>",
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
            fb_text = "Correct!" + (f" {q['explanation']}" if q["explanation"] else "")
            fb_img_html, fb_files = _image_payload(q["feedback_images"], doc, "        ")
            fb_html = _html(fb_text) + (f"<br/>{fb_img_html}" if fb_img_html else "")

            lines += [
                "    <shuffleanswers>0</shuffleanswers>",
                "    <single>1</single>",
                "    <answernumbering>ABCD</answernumbering>",
                "",
            ]
            for i, opt in enumerate(q["options"]):
                correct = i == q["correct_idx"]
                opt_img_html, opt_files = _image_payload(q["option_images"][i], doc, "      ")
                opt_html = _html(opt) + (f" {opt_img_html}" if opt_img_html else "")
                lines += [
                    f'    <answer fraction="{100 if correct else 0}" format="html">',
                    f"      <text>{_cdata(opt_html)}</text>",
                    *opt_files,
                    '      <feedback format="html">',
                    f"        <text>{_cdata(fb_html) if correct else ''}</text>",
                    *(fb_files if correct else []),
                    "      </feedback>",
                    "    </answer>",
                ]

        lines += ["  </question>", ""]

    lines.append("</quiz>")
    return "\n".join(lines)


# ─── Public entry points ─────────────────────────────────────────────────────

def convert(docx_fileobj) -> ConversionResult:
    """Convert a .docx (path or file-like) to Moodle XML.

    Raises ValueError if no question could be converted; the message lists the
    reasons each candidate question was skipped."""
    doc = Document(docx_fileobj)
    warnings: list[str] = []
    questions = parse_questions(doc, warnings)
    if not questions:
        msg = (
            "No questions could be converted. For MCQ questions make sure the file "
            "has options A) B) C) … and a highlighted / bold / 'Answer: X' marker. "
            "For essay questions make sure each question starts with a label "
            "(Q1, 1., etc.) and contains body text but no A/B/C options."
        )
        err = ValueError(msg)
        err.warnings = warnings  # type: ignore[attr-defined]
        raise err
    return ConversionResult(
        xml=generate_xml(questions, doc, warnings),
        questions=questions,
        warnings=warnings,
    )


def convert_stream(docx_fileobj) -> str:
    """Backwards-compatible wrapper: return only the XML string."""
    return convert(docx_fileobj).xml
