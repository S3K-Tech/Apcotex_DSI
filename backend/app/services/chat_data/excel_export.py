"""Excel (.xlsx) export for Chat with Data responses."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.schemas.chat_data import ChatExportRequest, ChatTable

_HEADER_FILL = PatternFill(start_color="0F766E", end_color="0F766E", fill_type="solid")
_SUBHEADER_FILL = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
_HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
_TITLE_FONT = Font(name="Calibri", size=13, bold=True, color="0F172A")
_LABEL_FONT = Font(name="Calibri", size=10, bold=True, color="475569")
_BODY_FONT = Font(name="Calibri", size=10, color="0F172A")
_THIN = Side(border_style="thin", color="CBD5E1")
_CELL_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def build_export_filename(question: Optional[str] = None) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = ""
    if question:
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", question.strip()).strip("_").lower()
        slug = cleaned[:40].strip("_")
    if slug:
        return f"chat_data_{slug}_{stamp}.xlsx"
    return f"chat_data_export_{stamp}.xlsx"


def _autofit(ws) -> None:
    for col_idx in range(1, ws.max_column + 1):
        letter = get_column_letter(col_idx)
        max_len = 0
        for row in ws.iter_rows(min_col=col_idx, max_col=col_idx, values_only=True):
            val = row[0]
            if val is None:
                continue
            text = str(val)
            if len(text) > max_len:
                max_len = len(text)
        ws.column_dimensions[letter].width = min(max(max_len + 4, 12), 48)


def build_chat_excel(payload: ChatExportRequest, full_table: Optional[ChatTable] = None) -> bytes:
    """Render a Chat with Data answer into an .xlsx byte stream."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Chat Data Export"

    row_idx = 1

    # Title / Question
    title_text = (payload.question or "").strip() or "Chat with Data Export"
    ws.cell(row=row_idx, column=1, value=title_text).font = _TITLE_FONT
    row_idx += 1

    if payload.message:
        ws.cell(row=row_idx, column=1, value=payload.message).font = _BODY_FONT
        row_idx += 1

    if payload.periods:
        ws.cell(row=row_idx, column=1, value="Periods").font = _LABEL_FONT
        ws.cell(row=row_idx, column=2, value=", ".join(payload.periods)).font = _BODY_FONT
        row_idx += 1

    if payload.filters:
        filter_str = ", ".join(f"{k}: {v}" for k, v in payload.filters.items())
        ws.cell(row=row_idx, column=1, value="Filters").font = _LABEL_FONT
        ws.cell(row=row_idx, column=2, value=filter_str).font = _BODY_FONT
        row_idx += 1

    # Summary facts
    if payload.facts:
        row_idx += 1
        ws.cell(row=row_idx, column=1, value="Summary").font = _LABEL_FONT
        ws.cell(row=row_idx, column=1).fill = _SUBHEADER_FILL
        ws.cell(row=row_idx, column=2, value="Value").font = _LABEL_FONT
        ws.cell(row=row_idx, column=2).fill = _SUBHEADER_FILL
        row_idx += 1
        for fact in payload.facts:
            c1 = ws.cell(row=row_idx, column=1, value=fact.label)
            c2 = ws.cell(row=row_idx, column=2, value=fact.display)
            c1.font = _BODY_FONT
            c2.font = _BODY_FONT
            c1.border = _CELL_BORDER
            c2.border = _CELL_BORDER
            row_idx += 1

    table = full_table if full_table is not None else payload.table

    # Main tabular data
    if table and table.columns and table.rows:
        row_idx += 1
        for col_idx, col in enumerate(table.columns, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=col.label)
            cell.font = _HEADER_FONT
            cell.fill = _HEADER_FILL
            cell.border = _CELL_BORDER
            cell.alignment = Alignment(
                horizontal="right" if col.numeric else "left",
                vertical="center",
            )
        row_idx += 1

        for data_row in table.rows:
            for col_idx, col in enumerate(table.columns, start=1):
                raw_val: Any = data_row.get(col.key)
                cell = ws.cell(row=row_idx, column=col_idx, value=raw_val)
                cell.font = _BODY_FONT
                cell.border = _CELL_BORDER
                if col.numeric and isinstance(raw_val, (int, float)):
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                    if col.key == "share_pct":
                        cell.number_format = '0.0"%"'
                    elif col.key == "rank":
                        cell.number_format = "0"
                    elif "MT" in col.label or "MT" in str(data_row.get("metric") or ""):
                        cell.number_format = "#,##0.000"
                    else:
                        cell.number_format = "#,##0"
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center")
            row_idx += 1
    elif payload.chart and payload.chart.points:
        # Fallback: export chart series if no table was attached
        row_idx += 1
        unit_label = payload.chart.unit or "Value"
        headers: List[str] = ["Name", f"Value ({unit_label})"]
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=h)
            cell.font = _HEADER_FONT
            cell.fill = _HEADER_FILL
            cell.border = _CELL_BORDER
            cell.alignment = Alignment(
                horizontal="right" if col_idx == 2 else "left",
                vertical="center",
            )
        row_idx += 1
        for pt in payload.chart.points:
            c1 = ws.cell(row=row_idx, column=1, value=pt.name)
            c2 = ws.cell(row=row_idx, column=2, value=float(pt.value))
            c1.font = _BODY_FONT
            c2.font = _BODY_FONT
            c1.border = _CELL_BORDER
            c2.border = _CELL_BORDER
            c2.alignment = Alignment(horizontal="right", vertical="center")
            c2.number_format = "#,##0.000" if unit_label == "MT" else "#,##0"
            row_idx += 1

    _autofit(ws)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
