"""Quarterly executive report PDF: dashboard charts (vector) plus the executive summary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

IST = timezone(timedelta(hours=5, minutes=30))


def _logo_path() -> Optional[Path]:
    """Same asset as the Login page: frontend/src/assets/images/apcotexindustrieslogo.png."""
    root = Path(__file__).resolve().parents[4]
    login_logo = root / "frontend" / "src" / "assets" / "images" / "apcotexindustrieslogo.png"
    return login_logo if login_logo.is_file() else None


def _format_ist(when: datetime) -> str:
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(IST).strftime("%d %b %Y, %H:%M IST")

BLUE = colors.HexColor("#1F5FA8")
TEAL = colors.HexColor("#1FB7B5")
NAVY = colors.HexColor("#0F172A")
MUTED = colors.HexColor("#6B7280")
BORDER = colors.HexColor("#E5E7EB")
LIGHT = colors.HexColor("#F3F6FA")
PALETTE = [
    colors.HexColor(c)
    for c in ("#1F5FA8", "#1FB7B5", "#7C3AED", "#059669", "#D97706", "#D93A2F",
              "#0891B2", "#9333EA", "#16A34A", "#DC2626", "#0D9488")
]

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

_REPLACEMENTS = {"→": "->", "←": "<-", "≥": ">=", "≤": "<=", "≈": "~", "×": "x", "−": "-", "\u00a0": " "}


def _safe(text: Any) -> str:
    """Standard PDF fonts are cp1252; keep the text inside it."""
    value = str(text if text is not None else "")
    for src, dst in _REPLACEMENTS.items():
        value = value.replace(src, dst)
    return value.encode("cp1252", "replace").decode("cp1252")


def _p(text: Any) -> str:
    return escape(_safe(text))


def _short(text: str, limit: int) -> str:
    text = _safe(text)
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _styles() -> Dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    body = ParagraphStyle(
        "Body",
        parent=base["BodyText"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11.5,
        textColor=NAVY,
        alignment=TA_LEFT,
        spaceAfter=3,
    )
    return {
        "body": body,
        "bullet": ParagraphStyle("Bullet", parent=body, fontSize=8.5, leading=11.5, spaceAfter=2),
        "small": ParagraphStyle("Small", parent=body, fontSize=7.5, leading=10, textColor=MUTED, spaceAfter=2),
        "cell": ParagraphStyle("Cell", parent=body, fontSize=8, leading=10.5, spaceAfter=0),
        "cell_bold": ParagraphStyle(
            "CellBold", parent=body, fontName="Helvetica-Bold", fontSize=8, leading=10.5, spaceAfter=0
        ),
        "kpi_label": ParagraphStyle(
            "KpiLabel", parent=body, fontSize=7, leading=9, textColor=MUTED, spaceAfter=0
        ),
        "kpi_value": ParagraphStyle(
            "KpiValue", parent=body, fontName="Helvetica-Bold", fontSize=11.5, leading=14, textColor=BLUE, spaceAfter=0
        ),
        "brand": ParagraphStyle(
            "Brand", parent=body, fontName="Helvetica-Bold", fontSize=9, leading=11, textColor=TEAL, spaceAfter=2
        ),
        "title": ParagraphStyle(
            "Title",
            parent=body,
            fontName="Helvetica-Bold",
            fontSize=17,
            leading=21,
            textColor=NAVY,
            alignment=TA_CENTER,
            spaceAfter=3,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle",
            parent=body,
            fontSize=10.5,
            leading=13,
            textColor=BLUE,
            alignment=TA_CENTER,
            spaceAfter=8,
        ),
        "tagline": ParagraphStyle(
            "Tagline",
            parent=body,
            fontSize=7.5,
            leading=10,
            textColor=MUTED,
            alignment=TA_CENTER,
            spaceAfter=2,
        ),
        "part": ParagraphStyle(
            "Part", parent=body, fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=BLUE, spaceBefore=2, spaceAfter=6
        ),
        "h2": ParagraphStyle(
            "H2", parent=body, fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=NAVY, spaceBefore=6, spaceAfter=3
        ),
        "h3": ParagraphStyle(
            "H3", parent=body, fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=NAVY, spaceBefore=4, spaceAfter=2
        ),
    }


class _NumberedCanvas(pdf_canvas.Canvas):
    """Draws the running header and 'Page X of Y' after the page count is known."""

    def __init__(self, *args, header: str = "", generated: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self._pages: List[Dict[str, Any]] = []
        self._header = header
        self._generated = generated

    def showPage(self):  # noqa: N802
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            self._decorate(total)
            super().showPage()
        super().save()

    def _decorate(self, total: int) -> None:
        self.saveState()
        self.setStrokeColor(BORDER)
        self.setLineWidth(0.6)
        self.line(MARGIN, PAGE_H - 12 * mm, PAGE_W - MARGIN, PAGE_H - 12 * mm)
        self.line(MARGIN, 12 * mm, PAGE_W - MARGIN, 12 * mm)
        text_x = MARGIN
        logo = _logo_path()
        if logo is not None:
            try:
                self.drawImage(
                    str(logo),
                    MARGIN,
                    PAGE_H - 11.8 * mm,
                    width=20 * mm,
                    height=7.5 * mm,
                    mask="auto",
                    preserveAspectRatio=True,
                    anchor="sw",
                )
                text_x = MARGIN + 22 * mm
            except Exception:  # noqa: BLE001
                logo = None
        if logo is None:
            self.setFont("Helvetica-Bold", 7.5)
            self.setFillColor(BLUE)
            self.drawString(MARGIN, PAGE_H - 10 * mm, "APCOTEX")
            text_x = MARGIN + 46
        self.setFont("Helvetica", 7.5)
        self.setFillColor(MUTED)
        self.drawString(text_x, PAGE_H - 10 * mm, _safe(self._header))
        self.drawRightString(PAGE_W - MARGIN, PAGE_H - 10 * mm, _safe(f"Generated {self._generated}"))
        self.drawString(MARGIN, 8 * mm, "Confidential - Secondary Sales Report for APCOTEX management")
        self.drawRightString(PAGE_W - MARGIN, 8 * mm, f"Page {self._pageNumber} of {total}")
        self.restoreState()


# ---------------------------------------------------------------- charts

def _axis_label(value: float) -> str:
    return f"{value:,.1f}" if abs(value) < 1000 else f"{value:,.0f}"


def _no_data(width: float, height: float, message: str) -> Drawing:
    drawing = Drawing(width, height)
    drawing.add(String(width / 2, height / 2, message, textAnchor="middle", fontName="Helvetica",
                       fontSize=9, fillColor=MUTED))
    return drawing


def _trend_chart(points: List[Dict[str, Any]]) -> Drawing:
    if not points:
        return _no_data(CONTENT_W, 60, "No monthly sales for the selected filters.")
    drawing = Drawing(CONTENT_W, 135)
    chart = HorizontalLineChart()
    chart.x, chart.y, chart.width, chart.height = 45, 26, CONTENT_W - 65, 100
    chart.data = [[float(p.get("qty") or 0) for p in points]]
    chart.categoryAxis.categoryNames = [_safe(p.get("tooltip") or p.get("month") or "") for p in points]
    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.categoryAxis.labels.fontSize = 7
    chart.categoryAxis.labels.angle = 30 if len(points) > 6 else 0
    chart.categoryAxis.labels.boxAnchor = "ne" if len(points) > 6 else "n"
    chart.valueAxis.valueMin = 0
    chart.valueAxis.labels.fontName = "Helvetica"
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.labelTextFormat = _axis_label
    chart.valueAxis.visibleGrid = True
    chart.valueAxis.gridStrokeColor = BORDER
    chart.lines[0].strokeColor = BLUE
    chart.lines[0].strokeWidth = 2
    chart.joinedLines = 1
    from reportlab.graphics.widgets.markers import makeMarker

    chart.lines[0].symbol = makeMarker("FilledCircle", size=4, fillColor=BLUE, strokeColor=BLUE)
    drawing.add(chart)
    return drawing


def _hbar_chart(rows: List[Dict[str, Any]], name_key: str, color) -> Drawing:
    if not rows:
        return _no_data(CONTENT_W, 60, "No data for the selected filters.")
    rows = list(reversed(rows))
    height = max(90, 13 * len(rows) + 32)
    drawing = Drawing(CONTENT_W, height)
    chart = HorizontalBarChart()
    chart.x, chart.y = 170, 25
    chart.width, chart.height = CONTENT_W - 200, height - 40
    chart.data = [[float(r.get("qty") or 0) for r in rows]]
    chart.categoryAxis.categoryNames = [_short(str(r.get(name_key) or ""), 34) for r in rows]
    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.categoryAxis.labels.fontSize = 7.5
    chart.categoryAxis.labels.boxAnchor = "e"
    chart.categoryAxis.labels.dx = -4
    chart.valueAxis.valueMin = 0
    chart.valueAxis.labels.fontName = "Helvetica"
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.labelTextFormat = _axis_label
    chart.valueAxis.visibleGrid = True
    chart.valueAxis.gridStrokeColor = BORDER
    chart.bars[0].fillColor = color
    chart.bars[0].strokeColor = None
    chart.barLabelFormat = "%.3f"
    chart.barLabels.fontName = "Helvetica"
    chart.barLabels.fontSize = 6.5
    chart.barLabels.boxAnchor = "w"
    chart.barLabels.dx = 3
    drawing.add(chart)
    return drawing


def _pie_chart(rows: List[Dict[str, Any]]) -> Drawing:
    rows = [r for r in rows if float(r.get("qty") or 0) > 0]
    if not rows:
        return _no_data(CONTENT_W, 60, "No product sales for the selected filters.")
    total = sum(float(r["qty"]) for r in rows)
    drawing = Drawing(CONTENT_W, 145)
    pie = Pie()
    pie.x, pie.y, pie.width, pie.height = 20, 8, 130, 130
    pie.data = [float(r["qty"]) for r in rows]
    pie.labels = None
    pie.slices.strokeColor = colors.white
    pie.slices.strokeWidth = 1
    for i in range(len(rows)):
        pie.slices[i].fillColor = PALETTE[i % len(PALETTE)]
    drawing.add(pie)
    legend = Legend()
    legend.x, legend.y = 185, 135
    legend.alignment = "right"
    legend.fontName = "Helvetica"
    legend.fontSize = 7.5
    legend.dy = 6
    legend.dx = 7
    legend.deltay = 12
    legend.columnMaximum = 12
    legend.colorNamePairs = [
        (PALETTE[i % len(PALETTE)],
         f"{_short(str(r['product']), 36)}  {float(r['qty']):,.3f} MT ({float(r['qty']) / total * 100:.1f}%)")
        for i, r in enumerate(rows)
    ]
    drawing.add(legend)
    return drawing


def _vbar_quarters(rows: List[Dict[str, Any]]) -> Optional[Drawing]:
    if len(rows) < 2:
        return None
    drawing = Drawing(CONTENT_W, 105)
    chart = VerticalBarChart()
    chart.x, chart.y, chart.width, chart.height = 45, 18, CONTENT_W - 65, 80
    chart.data = [[float(r.get("sales_mt") or 0) for r in rows]]
    chart.categoryAxis.categoryNames = [_safe(r.get("label") or "") for r in rows]
    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.categoryAxis.labels.fontSize = 7
    chart.valueAxis.valueMin = 0
    chart.valueAxis.labels.fontName = "Helvetica"
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.labelTextFormat = _axis_label
    chart.valueAxis.visibleGrid = True
    chart.valueAxis.gridStrokeColor = BORDER
    chart.bars[0].fillColor = BLUE
    chart.bars[0].strokeColor = None
    drawing.add(chart)
    return drawing


# ---------------------------------------------------------------- tables

def _table(rows: List[List[Any]], widths: List[float], st: Dict[str, ParagraphStyle], *, align_right=()) -> Table:
    data = []
    for r_index, row in enumerate(rows):
        style = st["cell_bold"] if r_index == 0 else st["cell"]
        data.append([Paragraph(_p(cell), style) for cell in row])
    table = Table(data, colWidths=widths, repeatRows=1)
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for col in align_right:
        commands.append(("ALIGN", (col, 0), (col, -1), "RIGHT"))
    table.setStyle(TableStyle(commands))
    return table


def _kpi_grid(snapshot: Dict[str, Any], st) -> Table:
    k = snapshot["kpis"]
    cards = [
        ("Total Sales (MT)", k.get("total_sales_mt_display") or "0.000"),
        ("Customers Served", f"{k.get('total_customers', 0):,}"),
        ("Products Sold", f"{k.get('total_products', 0):,}"),
        ("Active Distributors", f"{k.get('active_distributors', 0):,}"),
        ("Avg Quarterly Sales (MT)", k.get("average_quarterly_sales_mt_display") or "0.000"),
    ]
    cells = [[Paragraph(_p(label), st["kpi_label"]), Paragraph(_p(value), st["kpi_value"])] for label, value in cards]
    inner = [Table([[c[0]], [c[1]]], colWidths=[CONTENT_W / 5 - 6]) for c in cells]
    for t in inner:
        t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1)]))
    grid = Table([inner], colWidths=[CONTENT_W / 5] * 5)
    grid.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.6, BORDER),
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return grid


# ---------------------------------------------------------------- document

def build_pdf(
    *,
    snapshot: Dict[str, Any],
    summary: Dict[str, Any],
    generated_at: datetime,
    generated_by: Optional[str],
    model: Optional[str],
) -> bytes:
    st = _styles()
    period = snapshot.get("period") or ""
    title = snapshot.get("report_title") or "Secondary Sales Report"
    stamp = _format_ist(generated_at)
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=_safe(f"APCOTEX {title} - {period}"),
        author="APCOTEX Sales Insights",
    )
    story: List[Any] = []

    # Logo appears only in the left page header — not above the title.
    story.append(Paragraph(_p(title), st["title"]))
    story.append(Paragraph("Secondary Sales · Distributor sales to end customers", st["tagline"]))
    story.append(Paragraph(_p(f"Reporting period: {period}"), st["subtitle"]))

    meta = [["Filter", "Selection"]] + [[f["label"], f["value"]] for f in snapshot.get("filter_lines") or []]
    meta.append(["Generated", stamp + (f" by {generated_by}" if generated_by else "")])
    meta.append(["Unit", "Sales quantity in MT (metric tonnes). No monetary values are included."])
    story.append(_table(meta, [110, CONTENT_W - 110], st))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Part A - Secondary Sales Dashboard", st["part"]))
    story.append(Paragraph("KPI Overview", st["h2"]))
    story.append(_kpi_grid(snapshot, st))

    comparisons = snapshot.get("comparisons") or []
    if comparisons:
        rows = [["Comparison", "Period", "Sales (MT)", "Change (MT)", "Change"]]
        for c in comparisons:
            if c.get("available"):
                pct = f"{c['change_pct']:+.1f}%" if c.get("change_pct") is not None else "-"
                rows.append([c["description"], c["period"], c["total_mt_display"], f"{c['change_mt']:+,.3f}", pct])
            else:
                rows.append([c["description"], c["period"], "No data", "Not established", "-"])
        story.append(Spacer(1, 8))
        story.append(_table(rows, [130, 110, 80, 90, CONTENT_W - 410], st, align_right=(2, 3, 4)))

    charts = snapshot.get("charts") or {}
    quarters = charts.get("quarterly_totals") or []
    story.append(Paragraph("Quarterly Overview", st["h2"]))
    if quarters:
        rows = [["Quarter", "Records", "Sales (MT)"]] + [
            [q.get("display_label") or q.get("label"), f"{int(q.get('record_count') or 0):,}", q.get("sales_mt_display")]
            for q in quarters
        ]
        story.append(_table(rows, [CONTENT_W - 180, 80, 100], st, align_right=(1, 2)))
        bars = _vbar_quarters(quarters)
        if bars is not None:
            story.append(bars)
    else:
        story.append(Paragraph("No quarterly sales for the selected filters.", st["small"]))

    story.append(KeepTogether([Paragraph("Sales Trend (MT by month)", st["h2"]), _trend_chart(charts.get("monthly_trend") or [])]))
    basis = (snapshot.get("monthly_quality") or {}).get("monthly_basis")
    if basis and basis not in ("recorded_months", "none"):
        story.append(Paragraph(
            "Note: part or all of the monthly quantity was reported as quarterly totals and is spread evenly "
            "across the quarter's months on this chart.", st["small"]))

    story.append(KeepTogether([
        Paragraph("Top Customers (Top 10 by Sales Quantity, MT)", st["h2"]),
        _hbar_chart(charts.get("top_customers") or [], "customer", TEAL),
    ]))
    story.append(KeepTogether([
        Paragraph("Product Contribution (Share of Sales Quantity)", st["h2"]),
        _pie_chart(charts.get("product_contribution") or []),
    ]))
    distributors = charts.get("distributor_contribution") or []
    if distributors:
        story.append(Paragraph(f"Distributor Contribution — All Distributors ({len(distributors)}) (MT)", st["h2"]))
        story.append(_hbar_chart(distributors, "distributor", BLUE))

    story.append(PageBreak())
    story.append(Paragraph("Part B - Executive Summary", st["part"]))
    story.append(Paragraph(
        _p(
            f"Structured management brief for {period}. Generated with "
            f"{model or 'the configured LLM'} from Part A figures; every number was verified against the report data."
        ),
        st["small"],
    ))
    for index, section in enumerate(summary.get("sections") or [], start=1):
        block: List[Any] = [Paragraph(_p(f"{index}. {section.get('title')}"), st["h2"])]
        for text in (section.get("paragraphs") or [])[:1]:
            block.append(Paragraph(_p(text), st["body"]))
        bullets = (section.get("bullets") or [])[:6]
        if bullets:
            block.append(
                ListFlowable(
                    [
                        ListItem(Paragraph(_p(b), st["bullet"]), leftIndent=10, value="circle")
                        for b in bullets
                    ],
                    bulletType="bullet",
                    start="•",
                    leftIndent=10,
                    bulletFontSize=7,
                    spaceBefore=1,
                    spaceAfter=2,
                )
            )
        story.append(KeepTogether(block))

    doc.build(
        story,
        canvasmaker=lambda *a, **kw: _NumberedCanvas(
            *a, header=f"{title} | {period}", generated=stamp, **kw
        ),
    )
    return buffer.getvalue()
