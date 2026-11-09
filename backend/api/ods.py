"""
Minimal OpenDocument Spreadsheet (.ods) reader: the first sheet as text.

Handles the compressed `number-columns-repeated` / `number-rows-repeated`
encoding, ignoring the huge empty runs LibreOffice uses to pad a sheet.
"""
from __future__ import annotations

import io
import zipfile

from lxml import etree

O = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
T = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
X = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"

_CELL_TAGS = (f"{T}table-cell", f"{T}covered-table-cell")
_MAX_REPEAT = 1000  # longer empty runs are sheet padding


class OdsError(ValueError):
    pass


def _repeat(el, attr) -> int:
    try:
        return max(1, int(el.get(f"{T}{attr}", "1")))
    except ValueError:
        return 1


def cell_text(cell) -> str:
    paras = cell.findall(f"{X}p")
    if paras:
        return "\n".join("".join(p.itertext()) for p in paras).strip()
    return (cell.get(f"{O}string-value") or cell.get(f"{O}value") or "").strip()


class OdsSheet:
    def __init__(self, data: bytes):
        try:
            content = zipfile.ZipFile(io.BytesIO(data)).read("content.xml")
        except (zipfile.BadZipFile, KeyError) as exc:
            raise OdsError("The file is not a valid .ods spreadsheet.") from exc
        parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
        root = etree.fromstring(content, parser)
        self.table = next(root.iter(f"{T}table"), None)
        if self.table is None:
            raise OdsError("The .ods file contains no sheet.")

    def _rows(self):
        # rows of this table only (incl. header-rows / row-groups), not of sub-tables
        for row in self.table.iter(f"{T}table-row"):
            node = row.getparent()
            while node is not None and node.tag != f"{T}table":
                node = node.getparent()
            if node is self.table:
                yield row

    def text_matrix(self) -> list[list[str]]:
        matrix: list[list[str]] = []
        for row in self._rows():
            values: list[str] = []
            for cell in row:
                if cell.tag not in _CELL_TAGS:
                    continue
                text = cell_text(cell)
                n = _repeat(cell, "number-columns-repeated")
                values.extend([text] * (n if text or n <= _MAX_REPEAT else 1))
            while values and not values[-1]:
                values.pop()
            n = _repeat(row, "number-rows-repeated")
            if not values and n > _MAX_REPEAT:
                continue
            matrix.extend([list(values) for _ in range(n)])
        while matrix and not matrix[-1]:
            matrix.pop()
        width = max((len(r) for r in matrix), default=0)
        return [r + [""] * (width - len(r)) for r in matrix]
