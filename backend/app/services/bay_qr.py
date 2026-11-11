"""QR code of a bay: the sticker generated in the admin tab "Generate QR code" and scanned by the Intel Park app.

Content of the QR code (plain text, read by the app and sent as is to GET /api/v1/app/bays/by-code):

    intelpark:bay:P1/EXT/05?ref=IPK-7F3K9Q&street=Rue%20du%20Lac%2012%2C%201201%20Gen%C3%A8ve&gps=46.204391,6.143158

* ``P1/EXT/05``  site / zone / bay number (also printed under the code, can be typed by hand in the app);
* ``ref``        reference of the bay in the main database (column bays.reference, unique), used first by the
                 server: the sticker stays valid if the bay is renumbered;
* ``street``     street and address of the bay;
* ``gps``        latitude,longitude of the bay (WGS 84).

Older stickers ("intelpark:bay:P1/EXT/05" from ``python -m app.cli bay-labels``) still work.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import parse_qs, quote

from reportlab.graphics import renderPDF, renderSVG
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

PREFIX = "intelpark:bay:"
QR_SIZE_MM = 50  # the printed code is 5 cm x 5 cm
_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # no 0/O, 1/I: easy to read over the phone


def new_reference() -> str:
    return "IPK-" + "".join(secrets.choice(_ALPHABET) for _ in range(6))


def content(code: str, reference: str, street: str | None, latitude: float | None, longitude: float | None) -> str:
    parts = [f"ref={quote(reference, safe='-')}"]
    if street:
        parts.append(f"street={quote(street, safe='')}")
    if latitude is not None and longitude is not None:
        parts.append(f"gps={float(latitude):.6f},{float(longitude):.6f}")
    return f"{PREFIX}{code}?{'&'.join(parts)}"


@dataclass
class Scanned:
    code: str | None  # "P1/EXT/05", or a bay id
    reference: str | None


def parse(raw: str) -> Scanned:
    """Read what the app scanned or what the driver typed ("P1/EXT/05", a full QR content, a bay id)."""
    text = raw.strip()
    if text.lower().startswith(PREFIX):
        text = text[len(PREFIX):]
    code, _, query = text.partition("?")
    params = parse_qs(query)
    reference = (params.get("ref") or [None])[0]
    return Scanned(code=code.strip() or None, reference=reference.strip().upper() if reference else None)


def _qr_drawing(data: str, size: float) -> Drawing:
    widget = QrCodeWidget(data, barLevel="M", barBorder=0)
    x0, y0, x1, y1 = widget.getBounds()
    drawing = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    drawing.add(widget)
    return drawing


def svg(data: str, size_px: int = 220) -> str:
    """Preview shown on the page (the PDF is what gets printed)."""
    return renderSVG.drawToString(_qr_drawing(data, size_px))


def pdf(data: str, *, title: str, scan_hint: str, bay_line: str, street: str | None, gps_line: str | None,
        reference_line: str) -> bytes:
    """A4 page with one sticker in the middle: the QR code is exactly 5 cm x 5 cm, with a dashed cutting frame."""
    buf = BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    pdf.setTitle(title)
    page_w, page_h = A4
    qr = QR_SIZE_MM * mm
    frame_w, frame_h = qr + 40 * mm, qr + 52 * mm
    fx, fy = (page_w - frame_w) / 2, (page_h - frame_h) / 2
    pdf.setDash(3, 3)
    pdf.setStrokeColor(HexColor("#9AA0A6"))
    pdf.roundRect(fx, fy, frame_w, frame_h, 4 * mm)
    pdf.setDash()
    cx = page_w / 2
    top = fy + frame_h
    pdf.setFillColor(HexColor("#0D1014"))
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawCentredString(cx, top - 11 * mm, "Intel Park")
    pdf.setFont("Helvetica", 9)
    pdf.drawCentredString(cx, top - 16 * mm, _fit(pdf, scan_hint, frame_w - 8 * mm))
    qr_y = top - 20 * mm - qr
    renderPDF.draw(_qr_drawing(data, qr), pdf, cx - qr / 2, qr_y)
    y = qr_y - 7 * mm
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawCentredString(cx, y, bay_line)
    pdf.setFont("Helvetica", 8.5)
    for line in filter(None, [street, gps_line]):
        y -= 5 * mm
        pdf.drawCentredString(cx, y, _fit(pdf, line, frame_w - 8 * mm))
    y -= 5.5 * mm
    pdf.setFont("Courier-Bold", 10)
    pdf.drawCentredString(cx, y, reference_line)
    pdf.showPage()
    pdf.save()
    return buf.getvalue()


def _fit(pdf: canvas.Canvas, text: str, width: float) -> str:
    """Shorten a line that is wider than the sticker."""
    if pdf.stringWidth(text) <= width:
        return text
    while len(text) > 4 and pdf.stringWidth(text + "...") > width:
        text = text[:-1]
    return text.rstrip(" ,") + "..."
