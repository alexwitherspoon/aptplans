"""Minimal XLSX sheet reader. SpreadsheetML only; no openpyxl."""

from __future__ import annotations

import io
import zipfile
from collections import defaultdict
from xml.etree import ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _col_row(cell_ref: str) -> tuple[int, int]:
    col = "".join(char for char in cell_ref if char.isalpha())
    row = int("".join(char for char in cell_ref if char.isdigit()))
    number = 0
    for char in col:
        number = number * 26 + (ord(char) - 64)
    return number, row


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall("m:si", NS):
        texts = [node.text or "" for node in item.findall(".//m:t", NS)]
        values.append("".join(texts))
    return values


def _sheet_rows(archive: zipfile.ZipFile, path: str, strings: list[str]) -> dict[int, dict[int, str]]:
    root = ET.fromstring(archive.read(path))
    rows: dict[int, dict[int, str]] = defaultdict(dict)
    for cell in root.findall(".//m:c", NS):
        ref = cell.attrib.get("r")
        if not ref:
            continue
        col, row = _col_row(ref)
        kind = cell.attrib.get("t")
        if kind == "inlineStr":
            texts = [node.text or "" for node in cell.findall(".//m:t", NS)]
            rows[row][col] = "".join(texts)
            continue
        node = cell.find("m:v", NS)
        if node is None or node.text is None:
            value = ""
        elif kind == "s":
            value = strings[int(node.text)]
        else:
            value = node.text
        rows[row][col] = value
    return rows


def rows_from_xlsx(data: bytes, sheet: str = "xl/worksheets/sheet1.xml") -> dict[int, dict[int, str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return _sheet_rows(archive, sheet, _shared_strings(archive))
