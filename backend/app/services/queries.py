"""Read queries shared by the REST API (Android, website) and the server-rendered pages.

Every function returns plain dicts/lists so the same data can be serialised to JSON
or passed to a Jinja template.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import settings_store


def _ids(site_ids: set[uuid.UUID] | None) -> list[uuid.UUID] | None:
    return None if site_ids is None else list(site_ids)


def _site_filter(column: str, site_ids: set[uuid.UUID] | None) -> str:
    return "" if site_ids is None else f" AND {column} = ANY(:site_ids)"


# --- Sites and dashboard ------------------------------------------------------
SQL_SITES = """
SELECT s.id, s.code, s.name, s.address_line, s.postcode, s.city, s.country, s.timezone,
       s.latitude::float AS latitude, s.longitude::float AS longitude, s.is_active,
       coalesce(o.total_bays, 0) AS total_bays, coalesce(o.free_bays, 0) AS free_bays,
       coalesce(o.occupied_bays, 0) AS occupied_bays
FROM sites s LEFT JOIN v_site_occupancy o ON o.site_id = s.id
WHERE true {filter}
ORDER BY s.code
"""


def sites(db: Session, site_ids: set[uuid.UUID] | None) -> list[dict]:
    sql = SQL_SITES.format(filter=_site_filter("s.id", site_ids))
    return [dict(r._mapping) for r in db.execute(text(sql), {"site_ids": _ids(site_ids)})]


SQL_DASHBOARD = """
WITH scope AS (SELECT id FROM sites WHERE (CAST(:site_id AS uuid) IS NULL OR id = CAST(:site_id AS uuid)) {filter})
SELECT
  (SELECT coalesce(sum(free_bays), 0) FROM v_site_occupancy WHERE site_id IN (SELECT id FROM scope))  AS free_bays,
  (SELECT coalesce(sum(total_bays), 0) FROM v_site_occupancy WHERE site_id IN (SELECT id FROM scope)) AS total_bays,
  (SELECT coalesce(sum(occupied_bays), 0) FROM v_site_occupancy WHERE site_id IN (SELECT id FROM scope)) AS occupied_bays,
  (SELECT count(*) FROM parking_sessions WHERE site_id IN (SELECT id FROM scope) AND entered_at >= :day_start) AS entries_today,
  (SELECT count(*) FROM parking_sessions WHERE site_id IN (SELECT id FROM scope) AND exited_at >= :day_start) AS exits_today,
  (SELECT coalesce(sum(amount_cents), 0) FROM payments WHERE site_id IN (SELECT id FROM scope)
      AND status = 'succeeded' AND paid_at >= :day_start) AS revenue_today,
  (SELECT coalesce(sum(amount_cents), 0) FROM payments WHERE site_id IN (SELECT id FROM scope)
      AND status = 'succeeded' AND paid_at >= :day_start AND subscription_id IS NOT NULL) AS revenue_subscriptions_today,
  (SELECT count(*) FROM payment_alarms WHERE site_id IN (SELECT id FROM scope) AND status = 'open') AS open_alarms
"""


def dashboard(db: Session, site_id: uuid.UUID | None, site_ids: set[uuid.UUID] | None) -> dict:
    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    sql = SQL_DASHBOARD.format(filter=_site_filter("id", site_ids))
    row = dict(db.execute(text(sql), {"site_id": site_id, "site_ids": _ids(site_ids), "day_start": day_start}).one()._mapping)
    total = row["total_bays"] or 0
    row["occupancy_pct"] = round(100 * (row["occupied_bays"] or 0) / total) if total else 0
    row["site_id"] = site_id
    return row


def zones(db: Session, site_id: uuid.UUID) -> list[dict]:
    rows = db.execute(
        text("SELECT id, site_id, code, name, level, sort_order FROM zones WHERE site_id = :s ORDER BY sort_order"),
        {"s": site_id},
    )
    return [dict(r._mapping) for r in rows]


# --- Bays -------------------------------------------------------------------------
SQL_BAYS = """
SELECT v.bay_id AS id, v.zone_id, v.number, v.kind, v.status, v.is_occupied, v.occupied_since,
       v.plan_row, v.plan_col, v.photo_x::float AS photo_x, v.photo_y::float AS photo_y,
       ps.plate_display AS plate
FROM v_bay_status v
LEFT JOIN LATERAL (
  SELECT plate_display FROM parking_sessions p
  WHERE p.bay_id = v.bay_id AND p.status = 'open' ORDER BY p.entered_at DESC LIMIT 1
) ps ON true
WHERE v.zone_id = :zone_id
ORDER BY v.plan_row NULLS LAST, v.plan_col NULLS LAST, v.number
"""


def bays(db: Session, zone_id: uuid.UUID) -> list[dict]:
    return [dict(r._mapping) for r in db.execute(text(SQL_BAYS), {"zone_id": zone_id})]


def bay_detail(db: Session, bay_id: uuid.UUID) -> dict | None:
    row = db.execute(
        text("""
        SELECT v.bay_id AS id, v.zone_id, v.site_id, v.number, v.kind, v.status, v.is_occupied, v.occupied_since,
               v.plan_row, v.plan_col, v.photo_x::float AS photo_x, v.photo_y::float AS photo_y,
               z.name AS zone_name, b.admin_state, b.blocked_reason
        FROM v_bay_status v JOIN zones z ON z.id = v.zone_id JOIN bays b ON b.id = v.bay_id
        WHERE v.bay_id = :b"""),
        {"b": bay_id},
    ).first()
    if row is None:
        return None
    out = dict(row._mapping)
    session = db.execute(
        text("""
        SELECT p.id, p.plate_display AS plate, p.entered_at, p.paid_until,
               (s.id IS NOT NULL) AS subscriber, (p.reservation_id IS NOT NULL) AS reserved
        FROM parking_sessions p LEFT JOIN subscriptions s ON s.id = p.subscription_id
        WHERE p.bay_id = :b AND p.status = 'open' ORDER BY p.entered_at DESC LIMIT 1"""),
        {"b": bay_id},
    ).first()
    out["plate"] = session.plate if session else None
    out["session"] = dict(session._mapping) if session else None
    alarm = db.execute(
        text("""SELECT id, reason, detail, detected_at, status FROM payment_alarms
                WHERE bay_id = :b AND status = 'open' ORDER BY detected_at DESC LIMIT 1"""),
        {"b": bay_id},
    ).first()
    out["alarm"] = dict(alarm._mapping) if alarm else None
    res = db.execute(
        text("""SELECT r.id, r.starts_at, r.ends_at, c.first_name || ' ' || c.last_name AS customer, v.plate_display AS plate
                FROM reservations r JOIN customers c ON c.id = r.customer_id LEFT JOIN vehicles v ON v.id = r.vehicle_id
                WHERE r.bay_id = :b AND r.status IN ('confirmed', 'active') AND r.ends_at > now()
                ORDER BY r.starts_at LIMIT 1"""),
        {"b": bay_id},
    ).first()
    out["reservation"] = dict(res._mapping) if res else None
    history = db.execute(
        text("""
        (SELECT entered_at AS at, 'entry' AS kind, plate_display AS plate FROM parking_sessions WHERE bay_id = :b)
        UNION ALL
        (SELECT exited_at, 'exit', plate_display FROM parking_sessions WHERE bay_id = :b AND exited_at IS NOT NULL)
        UNION ALL
        (SELECT detected_at, 'alarm_' || reason::text, plate_display FROM payment_alarms WHERE bay_id = :b)
        ORDER BY at DESC LIMIT 8"""),
        {"b": bay_id},
    )
    out["history"] = [dict(r._mapping) for r in history]
    return out


# --- Payment alarms ------------------------------------------------------------
def payment_alarms(db: Session, site_ids: set[uuid.UUID] | None, status: str | None = "open", limit: int = 50) -> list[dict]:
    sql = """
    SELECT a.id, a.site_id, a.bay_id, z.name || ' · ' || b.number AS bay_label, z.code AS zone_code, b.number AS bay_number,
           a.plate_display AS plate, a.reason, a.detail, a.detected_at, a.status, a.handled_at,
           ps.identified_by::text AS identified_by,
           CASE WHEN c.id IS NOT NULL THEN c.first_name || ' ' || left(c.last_name, 1) || '.' END AS customer_name,
           (SELECT count(*) FROM alarm_evidence e WHERE e.alarm_id = a.id) AS evidence_count
    FROM payment_alarms a LEFT JOIN bays b ON b.id = a.bay_id LEFT JOIN zones z ON z.id = b.zone_id
    LEFT JOIN parking_sessions ps ON ps.id = a.session_id LEFT JOIN customers c ON c.id = ps.customer_id
    WHERE (CAST(:status AS text) IS NULL OR a.status::text = :status) {filter}
    ORDER BY a.detected_at DESC LIMIT :limit
    """.format(filter=_site_filter("a.site_id", site_ids))
    rows = db.execute(text(sql), {"status": status, "site_ids": _ids(site_ids), "limit": limit})
    return [dict(r._mapping) for r in rows]


# --- Reservations ---------------------------------------------------------------
SQL_RESERVATIONS = """
SELECT r.id, r.customer_id, c.first_name || ' ' || c.last_name AS customer_name, c.company_name,
       r.channel, v.plate_display AS plate, r.bay_id,
       CASE WHEN z.code = 'EXT' THEN 'Ext.' ELSE replace(z.code, 'L', '') END || ' · ' || b.number AS bay_label,
       b.kind AS bay_kind, z.site_id, r.starts_at, r.ends_at, r.status, r.price_cents, r.currency,
       (sub.id IS NOT NULL) AS is_subscriber,
       CASE
         WHEN r.status IN ('completed', 'no_show') THEN 'completed'
         WHEN r.status = 'cancelled' THEN 'cancelled'
         WHEN r.starts_at > now() THEN 'upcoming'
         WHEN r.ends_at <= now() THEN 'overdue'
         ELSE 'ongoing'
       END AS display_status
FROM reservations r
JOIN customers c ON c.id = r.customer_id
LEFT JOIN vehicles v ON v.id = r.vehicle_id
JOIN bays b ON b.id = r.bay_id
JOIN zones z ON z.id = b.zone_id
LEFT JOIN LATERAL (SELECT id FROM subscriptions s WHERE s.customer_id = c.id AND s.status = 'active' LIMIT 1) sub ON true
WHERE (CAST(:q AS text) IS NULL
       OR lower(c.first_name || ' ' || c.last_name) LIKE '%' || lower(:q) || '%'
       OR v.plate_norm LIKE '%' || normalize_plate(:q) || '%')
  {filter}
"""


def reservations(db: Session, site_ids: set[uuid.UUID] | None, tab: str = "ongoing", q: str | None = None) -> list[dict]:
    sql = SQL_RESERVATIONS.format(filter=_site_filter("z.site_id", site_ids))
    rows = [dict(r._mapping) for r in db.execute(text(sql), {"q": q or None, "site_ids": _ids(site_ids)})]
    order = {"overdue": 0, "ongoing": 1, "upcoming": 2, "completed": 3, "cancelled": 4}
    if tab == "ongoing":
        rows = [r for r in rows if r["display_status"] in ("ongoing", "overdue")]
    elif tab in ("upcoming", "completed", "cancelled"):
        rows = [r for r in rows if r["display_status"] == tab]
    rows.sort(key=lambda r: (order[r["display_status"]], r["ends_at"]))
    return rows


def reservation_stats(db: Session, site_ids: set[uuid.UUID] | None) -> dict:
    rows = reservations(db, site_ids, tab="all")
    now = datetime.now(UTC)
    end_of_day = now.replace(hour=23, minute=59, second=59)
    return {
        "ongoing": sum(1 for r in rows if r["display_status"] == "ongoing"),
        "upcoming_today": sum(1 for r in rows if r["display_status"] == "upcoming" and r["starts_at"] <= end_of_day),
        "overdue": sum(1 for r in rows if r["display_status"] == "overdue"),
        "ending_soon": sum(
            1 for r in rows if r["display_status"] == "ongoing" and r["ends_at"] - now <= timedelta(hours=1)
        ),
    }


# --- Subscriptions ------------------------------------------------------------
SQL_SUBSCRIPTIONS = """
SELECT s.id, s.customer_id, c.first_name || ' ' || c.last_name AS customer_name, v.plate_display AS plate,
       p.code AS plan_code, p.name AS plan_name, s.price_cents, s.currency, s.site_id,
       CASE WHEN b.id IS NULL THEN NULL ELSE replace(z.code, 'L', '') || ' · ' || b.number END AS bay_label,
       s.starts_on, s.ends_on, s.auto_renew, s.status, s.payment_overdue,
       (s.ends_on - current_date) AS days_left,
       CASE
         WHEN s.status = 'suspended' THEN 'suspended'
         WHEN s.status IN ('terminated', 'expired') OR (s.ends_on < current_date AND NOT s.payment_overdue) THEN 'ended'
         WHEN s.payment_overdue THEN 'unpaid'
         WHEN s.ends_on - current_date <= 7 THEN 'expiring_soon'
         ELSE 'active'
       END AS display_status
FROM subscriptions s
JOIN customers c ON c.id = s.customer_id
JOIN subscription_plans p ON p.id = s.plan_id
LEFT JOIN vehicles v ON v.id = s.vehicle_id
LEFT JOIN bays b ON b.id = s.bay_id
LEFT JOIN zones z ON z.id = b.zone_id
WHERE (CAST(:q AS text) IS NULL
       OR lower(c.first_name || ' ' || c.last_name) LIKE '%' || lower(:q) || '%'
       OR v.plate_norm LIKE '%' || normalize_plate(:q) || '%')
  {filter}
"""


def subscriptions(db: Session, site_ids: set[uuid.UUID] | None, tab: str = "active", q: str | None = None) -> list[dict]:
    sql = SQL_SUBSCRIPTIONS.format(filter=_site_filter("s.site_id", site_ids))
    rows = [dict(r._mapping) for r in db.execute(text(sql), {"q": q or None, "site_ids": _ids(site_ids)})]
    if tab == "active":
        rows = [r for r in rows if r["display_status"] in ("active", "expiring_soon")]
    elif tab != "all":
        rows = [r for r in rows if r["display_status"] == tab]
    order = {"unpaid": 0, "expiring_soon": 1, "active": 2, "suspended": 3, "ended": 4}
    rows.sort(key=lambda r: (order[r["display_status"]], r["ends_on"]))
    return rows


def subscription_stats(db: Session, site_ids: set[uuid.UUID] | None) -> dict:
    rows = subscriptions(db, site_ids, tab="all")
    live = [r for r in rows if r["display_status"] in ("active", "expiring_soon", "unpaid")]
    return {
        "active": len(live),
        "expiring_soon": sum(1 for r in rows if r["display_status"] == "expiring_soon"),
        "unpaid": sum(1 for r in rows if r["display_status"] == "unpaid"),
        "monthly_revenue_cents": sum(r["price_cents"] for r in live),
        "counts": {k: sum(1 for r in rows if r["display_status"] == k) for k in ("active", "expiring_soon", "unpaid", "suspended", "ended")},
    }


def plans(db: Session) -> list[dict]:
    return [dict(r._mapping) for r in db.execute(text("SELECT * FROM subscription_plans ORDER BY price_cents DESC"))]


def free_bays(db: Session, site_ids: set[uuid.UUID] | None, limit: int = 300) -> list[dict]:
    sql = """
    SELECT v.bay_id AS id, s.code AS site_code, v.zone_code, v.number, v.kind
    FROM v_bay_status v JOIN sites s ON s.id = v.site_id
    WHERE v.status = 'free' {filter}
    ORDER BY s.code, v.zone_code, v.number LIMIT :limit
    """.format(filter=_site_filter("v.site_id", site_ids))
    return [dict(r._mapping) for r in db.execute(text(sql), {"site_ids": _ids(site_ids), "limit": limit})]


# --- Customers -------------------------------------------------------------------
def customer_search(db: Session, q: str, limit: int = 10) -> list[dict]:
    rows = db.execute(
        text("""
        SELECT DISTINCT c.id, c.reference, c.first_name, c.last_name,
               (SELECT plate_display FROM vehicles v2 WHERE v2.customer_id = c.id ORDER BY v2.created_at LIMIT 1) AS plate
        FROM customers c LEFT JOIN vehicles v ON v.customer_id = c.id
        WHERE c.anonymised_at IS NULL AND (:q = ''
           OR lower(c.first_name || ' ' || c.last_name) LIKE '%' || lower(:q) || '%'
           OR lower(c.last_name || ' ' || c.first_name) LIKE '%' || lower(:q) || '%'
           OR v.plate_norm LIKE '%' || normalize_plate(:q) || '%')
        ORDER BY c.last_name, c.first_name LIMIT :limit"""),
        {"q": q, "limit": limit},
    )
    return [dict(r._mapping) for r in rows]


def customer_profile(db: Session, customer_id: uuid.UUID) -> dict | None:
    row = db.execute(text("SELECT * FROM customers WHERE id = :c"), {"c": customer_id}).first()
    if row is None:
        return None
    out = dict(row._mapping)
    out["vehicles"] = [
        dict(r._mapping)
        for r in db.execute(
            text("SELECT id, plate_display, plate_norm, country, make, model, color FROM vehicles WHERE customer_id = :c ORDER BY created_at"),
            {"c": customer_id},
        )
    ]
    loc = db.execute(
        text("""
        SELECT p.entered_at, s.name AS site_name, z.name AS zone_name, b.number AS bay_number, b.kind AS bay_kind
        FROM parking_sessions p JOIN vehicles v ON v.plate_norm = p.plate_norm
        JOIN sites s ON s.id = p.site_id LEFT JOIN bays b ON b.id = p.bay_id LEFT JOIN zones z ON z.id = b.zone_id
        WHERE v.customer_id = :c AND p.status = 'open' ORDER BY p.entered_at DESC LIMIT 1"""),
        {"c": customer_id},
    ).first()
    last_exit = db.execute(
        text("""SELECT max(p.exited_at) FROM parking_sessions p JOIN vehicles v ON v.plate_norm = p.plate_norm
                WHERE v.customer_id = :c"""),
        {"c": customer_id},
    ).scalar()
    out["location"] = dict(loc._mapping) if loc else {"outside": True, "last_exit_at": last_exit}
    sub = next((s for s in subscriptions(db, None, tab="all") if s["customer_id"] == customer_id and s["display_status"] != "ended"), None)
    out["subscription"] = sub
    res = [r for r in reservations(db, None, tab="all") if r["customer_id"] == customer_id and r["display_status"] in ("ongoing", "overdue", "upcoming")]
    out["reservation"] = res[0] if res else None
    has_alarm = db.execute(
        text("""SELECT count(*) FROM payment_alarms a JOIN vehicles v ON v.plate_norm = normalize_plate(a.plate_display)
                WHERE v.customer_id = :c AND a.status = 'open'"""),
        {"c": customer_id},
    ).scalar()
    if has_alarm:
        out["payment_state"] = "unpaid"
    elif sub and sub["payment_overdue"]:
        out["payment_state"] = "overdue"
    elif out["reservation"] and out["reservation"]["display_status"] == "overdue":
        out["payment_state"] = "surcharge_due"
    else:
        out["payment_state"] = "up_to_date"
    out["activity"] = [
        dict(r._mapping)
        for r in db.execute(
            text("""
            (SELECT p.entered_at AS at, 'entry' AS kind, z.name || ' · ' || b.number AS label
               FROM parking_sessions p JOIN vehicles v ON v.plate_norm = p.plate_norm
               LEFT JOIN bays b ON b.id = p.bay_id LEFT JOIN zones z ON z.id = b.zone_id WHERE v.customer_id = :c)
            UNION ALL
            (SELECT p.exited_at, 'exit', NULL FROM parking_sessions p JOIN vehicles v ON v.plate_norm = p.plate_norm
               WHERE v.customer_id = :c AND p.exited_at IS NOT NULL)
            UNION ALL
            (SELECT paid_at, 'payment', (amount_cents / 100.0)::text FROM payments WHERE customer_id = :c AND paid_at IS NOT NULL)
            UNION ALL
            (SELECT a.detected_at, 'alarm_' || a.reason::text, NULL FROM payment_alarms a
               JOIN vehicles v ON v.plate_norm = normalize_plate(a.plate_display) WHERE v.customer_id = :c)
            ORDER BY at DESC LIMIT 10"""),
            {"c": customer_id},
        )
    ]
    return out


# --- Tariffs ---------------------------------------------------------------------
def tariffs(db: Session, site_id: uuid.UUID | None = None) -> dict:
    rows = [
        dict(r._mapping)
        for r in db.execute(
            text("""SELECT t.*, u.full_name AS created_by_name FROM tariffs t LEFT JOIN staff_users u ON u.id = t.created_by
                    WHERE t.site_id IS NOT DISTINCT FROM :s ORDER BY t.valid_from DESC"""),
            {"s": site_id},
        )
    ]
    current = {r["kind"]: r for r in rows if r["valid_to"] is None}
    return {"current": current, "history": rows}


# --- Control cabinets ---------------------------------------------------------
def presence_sensors(db: Session, controller_id: uuid.UUID) -> list[dict]:
    """Presence sensors of one cabinet. A sensor without a wiring address is listed as "not configured"."""
    rows = db.execute(
        text("""SELECT d.id, d.code, d.label, d.address, d.live_url, d.status, z.code AS zone_code, b.number AS bay_number
                FROM devices d LEFT JOIN bays b ON b.id = d.bay_id LEFT JOIN zones z ON z.id = b.zone_id
                WHERE d.controller_id = :c AND d.kind IN ('front_presence_sensor', 'vertical_presence_sensor')
                ORDER BY d.code"""),
        {"c": controller_id},
    )
    out = []
    for r in rows:
        d = dict(r._mapping)
        d["state"] = "not_configured" if not d["address"] else ("active" if d["status"] == "ok" else "fault")
        d["simulated"] = (d["address"] or "").startswith("sim:")  # driven by the CarDetector simulator page
        out.append(d)
    return out


def controllers(db: Session, site_ids: set[uuid.UUID] | None) -> list[dict]:
    offline_after = timedelta(seconds=120)
    sql = """
    SELECT c.id, c.site_id, s.name AS site_name, c.code, c.name, c.last_seen_at, c.agent_version
    FROM controllers c JOIN sites s ON s.id = c.site_id WHERE c.is_active {filter} ORDER BY c.code
    """.format(filter=_site_filter("c.site_id", site_ids))
    out = []
    now = datetime.now(UTC)
    for c in db.execute(text(sql), {"site_ids": _ids(site_ids)}):
        item = dict(c._mapping)
        item["online"] = bool(item["last_seen_at"] and now - item["last_seen_at"] < offline_after)
        events = [
            dict(e._mapping)
            for e in db.execute(
                text("""SELECT id, code, priority, message_key, raised_at, cleared_at, acknowledged_at
                        FROM device_events WHERE controller_id = :c
                          AND (cleared_at IS NULL OR cleared_at > now() - interval '24 hours')
                        ORDER BY (cleared_at IS NULL) DESC, raised_at DESC LIMIT 50"""),
                {"c": item["id"]},
            )
        ]
        for e in events:
            e["state"] = "cleared" if e["cleared_at"] else ("active_acknowledged" if e["acknowledged_at"] else "active_unacknowledged")
        item["events"] = events
        item["active_alarms"] = sum(1 for e in events if e["priority"] == "alarm" and not e["cleared_at"])
        item["active_warnings"] = sum(1 for e in events if e["priority"] == "warning" and not e["cleared_at"])
        item["unacknowledged"] = sum(1 for e in events if e["state"] == "active_unacknowledged")
        item["configured"] = item["last_seen_at"] is not None
        item["sensors"] = presence_sensors(db, item["id"])
        item["sensor_counts"] = {
            state: sum(1 for d in item["sensors"] if d["state"] == state) for state in ("active", "fault", "not_configured")
        }
        out.append(item)
    return out


# --- Team -----------------------------------------------------------------------
def team(db: Session) -> list[dict]:
    rows = db.execute(
        text("""
        SELECT u.id, u.email, u.full_name, u.role, u.locale, u.is_active, u.last_login_at,
               (u.password_hash IS NULL) AS pending_invitation,
               coalesce(array_agg(s.code ORDER BY s.code) FILTER (WHERE s.code IS NOT NULL), '{}') AS site_codes
        FROM staff_users u
        LEFT JOIN staff_site_access a ON a.staff_user_id = u.id LEFT JOIN sites s ON s.id = a.site_id
        GROUP BY u.id ORDER BY u.role, u.full_name""")
    )
    return [dict(r._mapping) for r in rows]


def recent_exports(db: Session, limit: int = 6) -> list[dict]:
    rows = db.execute(
        text("""SELECT e.*, s.name AS site_name FROM report_exports e LEFT JOIN sites s ON s.id = e.site_id
                ORDER BY e.created_at DESC LIMIT :l"""),
        {"l": limit},
    )
    return [dict(r._mapping) for r in rows]


def settings(db: Session) -> dict:
    return settings_store.get_all(db)


def period_bounds(period: str, today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    if period == "day":
        return today, today
    if period == "week":
        return today - timedelta(days=6), today
    if period == "month":
        return today.replace(day=1), today
    if period == "last_month":
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    if period == "year":
        return today.replace(month=1, day=1), today
    raise ValueError(period)
