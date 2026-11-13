"""
Original worksheet columns and rows, in source order, for display only.

The parser output is normalized (one quantity value under several keys, month
columns summed into quarters). The workbench and the Extracted Data view show
the attachment as the distributor sent it instead: the header row of the parsed
sheet and every non-empty data row below it, cell values untouched.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.core.logging import get_logger
from app.services.product_master.normalize import clean_display

logger = get_logger(__name__)

MAX_SOURCE_ROWS = 20_000
_HEADER_SCAN_ROWS = 500
_READABLE_SUFFIXES = {".xlsx", ".xlsm"}

UNAVAILABLE: Dict[str, Any] = {"available": False}


def _cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _column_index(preview: Dict[str, Any], field: str) -> Optional[int]:
    """0-based worksheet column of a mapped field (parser positions are 1-based)."""
    pos = (preview.get("column_positions") or {}).get(field)
    try:
        value = int(pos)
    except (TypeError, ValueError):
        return None
    return value - 1 if value >= 1 else None


def build_source_table(
    path: Path,
    preview: Optional[Dict[str, Any]],
    parsed_products: Iterable[str],
) -> Dict[str, Any]:
    """Header + rows of the parsed sheet, or ``UNAVAILABLE`` when they cannot be trusted."""
    if not preview or path.suffix.lower() not in _READABLE_SUFFIXES:
        return dict(UNAVAILABLE)
    product_col = _column_index(preview, "product")
    customer_col = _column_index(preview, "customer")
    sheet_name = str(preview.get("sheet_name") or "")
    wanted = {clean_display(p).casefold() for p in parsed_products if clean_display(p)}
    if product_col is None or not sheet_name or not wanted:
        return dict(UNAVAILABLE)
    try:
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            if sheet_name not in wb.sheetnames:
                return dict(UNAVAILABLE)
            matrix: List[List[Any]] = []
            for row in wb[sheet_name].iter_rows(values_only=True):
                matrix.append([_cell(v) for v in row])
                if len(matrix) > MAX_SOURCE_ROWS + _HEADER_SCAN_ROWS:
                    break
        finally:
            wb.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Source table unavailable | file={} | err={}", path.name, type(exc).__name__)
        return dict(UNAVAILABLE)

    def product_of(row: List[Any]) -> str:
        return clean_display(row[product_col]) if product_col < len(row) else ""

    # Header = last non-empty row above the first row that holds a parsed product.
    first_data = next(
        (i for i, row in enumerate(matrix[:_HEADER_SCAN_ROWS]) if product_of(row).casefold() in wanted),
        None,
    )
    if first_data is None:
        return dict(UNAVAILABLE)
    header_idx = next(
        (i for i in range(first_data - 1, -1, -1) if any(clean_display(v) for v in matrix[i])),
        None,
    )
    if header_idx is None:
        return dict(UNAVAILABLE)
    header = matrix[header_idx]
    body = [row for row in matrix[header_idx + 1:] if any(clean_display(v) for v in row)][:MAX_SOURCE_ROWS]

    width = 0
    for i in range(max([len(header)] + [len(r) for r in body] or [0])):
        in_header = i < len(header) and clean_display(header[i])
        in_body = any(i < len(r) and clean_display(r[i]) for r in body)
        if in_header or in_body:
            width = i + 1
    columns = [
        {"key": f"c{i}", "label": clean_display(header[i]) if i < len(header) and clean_display(header[i]) else f"Column {i + 1}"}
        for i in range(width)
    ]

    rows: List[Dict[str, Any]] = []
    found: set = set()
    for row in body:
        product = product_of(row)
        found.add(product.casefold())
        customer = clean_display(row[customer_col]) if customer_col is not None and customer_col < len(row) else ""
        rows.append(
            {
                "cells": {f"c{i}": (row[i] if i < len(row) else None) for i in range(width)},
                "product": product,
                "customer": customer,
            }
        )
    # Multi-sheet / block layouts: if this sheet does not hold every parsed
    # product, it is not the whole picture; fall back to the extracted rows.
    if not wanted <= found:
        return dict(UNAVAILABLE)
    return {
        "available": True,
        "sheet_name": sheet_name,
        "columns": columns,
        "product_key": f"c{product_col}",
        "customer_key": f"c{customer_col}" if customer_col is not None and customer_col < width else None,
        "rows": rows,
    }
