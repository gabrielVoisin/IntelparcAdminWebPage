"""Administration commands.

    python -m app.cli seed-demo                         # load the demo data of the mockup
    python -m app.cli demo-sensors                      # reset the prototype sensor inventory (3 x 100)
    python -m app.cli create-admin EMAIL "Full Name"    # asks for the password
    python -m app.cli new-controller P1 CTRL-04 "Armoire Parking 4"   # prints the Pi key once
    python -m app.cli bay-labels P1 EXT labels.pdf      # QR code stickers scanned by the Intel Park app
    python -m app.cli ble-public-key                    # Ed25519 key the cabinets use to check phones
"""

from __future__ import annotations

import argparse
import getpass
import re
import sys
from pathlib import Path

from sqlalchemy import select

from app.db.session import SessionLocal, engine
from app.models import Controller, Site, StaffUser
from app.security import hash_password, new_controller_key

SQL_DIR = Path(__file__).parent / "db" / "sql"


def _run_sql(name: str) -> None:
    sql = (SQL_DIR / name).read_text(encoding="utf-8")
    sql = re.sub(r"^\s*(BEGIN|COMMIT);\s*$", "", sql, flags=re.MULTILINE)  # the engine manages the transaction
    with engine.begin() as conn:
        conn.connection.driver_connection.execute(sql)  # raw psycopg: no placeholder parsing


def seed_demo() -> None:
    _run_sql("seed_demo.sql")
    _run_sql("demo_sensors.sql")
    print("Demo data loaded. Staff password: ChangeMe-2026! (change it).")


def demo_sensors() -> None:
    _run_sql("demo_sensors.sql")
    print("Sensor inventory reset: 3 cabinets x 100 presence sensors, only CTRL-01 / CP-001 configured.")


def create_admin(email: str, full_name: str, locale: str) -> None:
    password = getpass.getpass("Password (12+ characters): ")
    if len(password) < 12:
        sys.exit("Password too short.")
    with SessionLocal.begin() as db:
        if db.scalar(select(StaffUser).where(StaffUser.email == email.lower())):
            sys.exit("This email already exists.")
        db.add(StaffUser(email=email.lower(), full_name=full_name, role="admin", locale=locale,
                         password_hash=hash_password(password)))
    print(f"Administrator {email} created.")


def new_controller(site_code: str, code: str, name: str) -> None:
    key, prefix, key_hash = new_controller_key()
    with SessionLocal.begin() as db:
        site = db.scalar(select(Site).where(Site.code == site_code))
        if site is None:
            sys.exit(f"Unknown site {site_code}.")
        db.add(Controller(site_id=site.id, code=code, name=name, api_key_prefix=prefix, api_key_hash=key_hash))
    print("Controller registered. Put this key in /etc/parking-agent/config.yaml (shown only once):")
    print(key)


def bay_labels(site_code: str, zone_code: str | None, out: str) -> None:
    """A4 sheet of QR stickers, 3 x 7 per page. Content "intelpark:bay:P1/EXT/05", also printed below."""
    from reportlab.graphics import renderPDF
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from sqlalchemy import text

    with SessionLocal() as db:
        rows = db.execute(
            text("""SELECT s.code AS site, z.code AS zone, b.number FROM bays b JOIN zones z ON z.id = b.zone_id
                    JOIN sites s ON s.id = z.site_id WHERE s.code = :s AND (CAST(:z AS text) IS NULL OR z.code = :z)
                    ORDER BY z.sort_order, b.number"""),
            {"s": site_code, "z": zone_code},
        ).all()
    if not rows:
        sys.exit("No bay found.")
    pdf = canvas.Canvas(out, pagesize=A4)
    page_w, page_h = A4
    cols, rows_per_page, cell_w, cell_h = 3, 7, 63 * mm, 38 * mm
    left, top = (page_w - cols * cell_w) / 2, page_h - (page_h - rows_per_page * cell_h) / 2
    for i, row in enumerate(rows):
        if i and i % (cols * rows_per_page) == 0:
            pdf.showPage()
        slot = i % (cols * rows_per_page)
        x = left + (slot % cols) * cell_w
        y = top - (slot // cols + 1) * cell_h
        code = f"{row.site}/{row.zone}/{row.number}"
        widget = QrCodeWidget(f"intelpark:bay:{code}", barLevel="M")
        x0, y0, x1, y1 = widget.getBounds()
        size = 30 * mm
        drawing = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
        drawing.add(widget)
        renderPDF.draw(drawing, pdf, x + 3 * mm, y + 4 * mm)
        pdf.setFont("Helvetica-Bold", 15)
        pdf.drawString(x + 36 * mm, y + 24 * mm, "Intel Park")
        pdf.setFont("Helvetica", 9)
        pdf.drawString(x + 36 * mm, y + 18 * mm, "Scannez pour payer")
        pdf.setFont("Courier-Bold", 11)
        pdf.drawString(x + 36 * mm, y + 9 * mm, code)
    pdf.save()
    print(f"{len(rows)} labels written to {out}")


def ble_public_key() -> None:
    from app.services.ble_identity import public_key_b64

    print(public_key_b64())


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed-demo")
    sub.add_parser("demo-sensors")
    p = sub.add_parser("create-admin")
    p.add_argument("email")
    p.add_argument("full_name")
    p.add_argument("--locale", default="fr", choices=["fr", "en", "de"])
    p = sub.add_parser("new-controller")
    p.add_argument("site_code")
    p.add_argument("code")
    p.add_argument("name")
    p = sub.add_parser("bay-labels")
    p.add_argument("site_code")
    p.add_argument("zone_code", nargs="?", default=None)
    p.add_argument("out", nargs="?", default="bay-labels.pdf")
    sub.add_parser("ble-public-key")
    args = parser.parse_args()
    if args.cmd == "seed-demo":
        seed_demo()
    elif args.cmd == "demo-sensors":
        demo_sensors()
    elif args.cmd == "create-admin":
        create_admin(args.email, args.full_name, args.locale)
    elif args.cmd == "bay-labels":
        bay_labels(args.site_code, args.zone_code, args.out)
    elif args.cmd == "ble-public-key":
        ble_public_key()
    else:
        new_controller(args.site_code, args.code, args.name)


if __name__ == "__main__":
    main()
