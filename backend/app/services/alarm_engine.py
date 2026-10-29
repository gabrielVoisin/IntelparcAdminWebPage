"""Turns open parking sessions into payment alarms.

Runs every ALARM_SCAN_INTERVAL_S seconds (background task started in app.main), and
can also be run by hand: `python -m app.services.alarm_engine`.

A session raises:
* no_payment  - no subscription, no reservation, nothing paid, older than the grace period
* overstay    - paid_until (or reservation end) exceeded by more than the tolerance
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import settings_store
from app.services.realtime import broadcaster

SQL_NO_PAYMENT = """
INSERT INTO payment_alarms (site_id, bay_id, session_id, plate_display, reason, detected_at)
SELECT p.site_id, p.bay_id, p.id, p.plate_display, 'no_payment', now()
FROM parking_sessions p
LEFT JOIN reservations r ON r.id = p.reservation_id
WHERE p.status = 'open'
  AND p.subscription_id IS NULL
  AND p.paid_until IS NULL
  AND (r.id IS NULL OR r.status = 'cancelled')
  AND p.entered_at < now() - make_interval(mins => :grace)
  AND NOT EXISTS (SELECT 1 FROM payment_alarms a WHERE a.session_id = p.id AND (a.reason = 'no_payment' OR a.status = 'open'))
ON CONFLICT DO NOTHING
RETURNING id, site_id, plate_display, reason
"""

SQL_OVERSTAY = """
INSERT INTO payment_alarms (site_id, bay_id, session_id, plate_display, reason, detail, detected_at)
SELECT p.site_id, p.bay_id, p.id, p.plate_display, 'overstay',
       floor(extract(epoch FROM now() - coalesce(p.paid_until, r.ends_at)) / 60)::int::text, now()
FROM parking_sessions p
LEFT JOIN reservations r ON r.id = p.reservation_id
WHERE p.status = 'open'
  AND p.subscription_id IS NULL
  AND coalesce(p.paid_until, r.ends_at) IS NOT NULL
  AND coalesce(p.paid_until, r.ends_at) < now() - make_interval(mins => :tolerance)
  AND NOT EXISTS (SELECT 1 FROM payment_alarms a WHERE a.session_id = p.id AND (a.reason = 'overstay' OR a.status = 'open'))
ON CONFLICT DO NOTHING
RETURNING id, site_id, plate_display, reason
"""


def scan(db: Session) -> int:
    values = settings_store.get_all(db)
    created = []
    created += db.execute(text(SQL_NO_PAYMENT), {"grace": int(values["control.grace_alarm_min"])}).all()
    created += db.execute(text(SQL_OVERSTAY), {"tolerance": int(values["control.overstay_min"])}).all()
    for row in created:
        broadcaster.publish("payment_alarm.created", dict(row._mapping))
    return len(created)


if __name__ == "__main__":
    from app.db import SessionLocal

    with SessionLocal() as session:
        count = scan(session)
        session.commit()
    print(f"{count} new alarm(s)")
