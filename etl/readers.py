"""按文件头嗅探格式并读取工作簿（值 + 单元格底色），不看扩展名。

  * OOXML（PK 头，147 个 .xls 实为 xlsx）→ openpyxl，传文件句柄绕开扩展名校验，可读 fill；
  * OLE2/BIFF8（D0CF11E0）→ xlrd==1.2.0，formatting_info=True 读调色板底色。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from etl.normalize import HEADER_SCAN_COLS

OLE2_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")


def sniff(path: Path) -> str:
    head = _head(path)
    if head[:2] == b"PK":
        return "ooxml"
    if head == OLE2_MAGIC:
        return "ole2"
    if head[:4] == b"%PDF":
        return "pdf"
    if head[:4] == b"Rar!":
        return "rar"
    if head[:6] == b"SQLite":
        return "sqlite"
    return "unknown"


def _head(path: Path) -> bytes:
    with path.open("rb") as fh:
        return fh.read(8)


@dataclass
class SheetData:
    name: str
    # 每行 (值列表, 底色 token 列表)，下标 0 对应 Excel 第 1 行
    rows: list[tuple[list, list]] = field(default_factory=list)


def read_workbook(path: Path, fmt: str) -> list[SheetData]:
    if fmt == "ooxml":
        return _read_ooxml(path)
    if fmt == "ole2":
        return _read_ole2(path)
    raise ValueError(f"不支持的工作簿格式: {fmt}")


def _ooxml_fill(cell) -> str | None:
    fill = cell.fill
    if fill is None or fill.fill_type is None:
        return None
    color = fill.fgColor
    if color.type == "rgb" and isinstance(color.rgb, str):
        return "FF" + color.rgb[-6:].upper()
    if color.type == "indexed":
        from openpyxl.styles.colors import COLOR_INDEX
        if 0 <= color.indexed < len(COLOR_INDEX):
            return "FF" + COLOR_INDEX[color.indexed][-6:].upper()
        return f"indexed{color.indexed}"
    if color.type == "theme":
        return f"theme{color.theme}" + (f"{color.tint:+.2f}" if color.tint else "")
    return color.type


def _read_ooxml(path: Path) -> list[SheetData]:
    import openpyxl

    with path.open("rb") as fh:
        wb = openpyxl.load_workbook(fh)
        sheets = []
        for ws in wb.worksheets:
            data = SheetData(ws.title)
            for row in ws.iter_rows(max_col=HEADER_SCAN_COLS):
                data.rows.append(([c.value for c in row], [_ooxml_fill(c) for c in row]))
            sheets.append(data)
        wb.close()
    return sheets


def _read_ole2(path: Path) -> list[SheetData]:
    import xlrd

    wb = xlrd.open_workbook(str(path), formatting_info=True)
    try:
        sheets = []
        for ws in wb.sheets():
            data = SheetData(ws.name)
            ncols = min(ws.ncols, HEADER_SCAN_COLS)
            for r in range(ws.nrows):
                values, fills = [], []
                for c in range(min(ncols, ws.row_len(r))):
                    values.append(ws.cell_value(r, c))
                    xf = wb.xf_list[ws.cell_xf_index(r, c)]
                    bg = xf.background
                    rgb = wb.colour_map.get(bg.pattern_colour_index) if bg.fill_pattern else None
                    fills.append("FF%02X%02X%02X" % rgb if rgb else None)
                data.rows.append((values, fills))
            sheets.append(data)
        return sheets
    finally:
        wb.release_resources()
