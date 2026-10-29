"""Communication watchdog between the control cabinets and the server, at 0.5 Hz.

Each program of a cabinet (the agent, the sensor simulator) sends POST /api/v1/device/watchdog every
PERIOD_S seconds. A source that misses MISSED_BEATS signals in a row is "lost": the server raises the
alarm device.cabinet_communication_lost on the cabinet, and clears it as soon as the signal is back.

The state is kept in memory (single API process, like the WebSocket broadcaster). After a server
restart a source is unknown until its first signal, so a restart raises no false alarm.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Controller, DeviceEvent
from app.services.realtime import broadcaster

PERIOD_S = 2.0          # 0.5 Hz
MISSED_BEATS = 3        # lost after 3 missing signals (6 s)
LOST_AFTER = timedelta(seconds=PERIOD_S * MISSED_BEATS)
SOURCES = {"agent": "WD-AGENT", "simulator": "WD-SIM"}  # event code of each source
MESSAGE_KEY = "device.cabinet_communication_lost"


@dataclass
class Beat:
    last: datetime
    seq: int = 0
    count: int = 0
    lost_since: datetime | None = None
    event_uid: str | None = None
    history: list[float] = field(default_factory=list)  # intervals between the last signals, seconds


_lock = threading.Lock()
_beats: dict[tuple[uuid.UUID, str], Beat] = {}


def beat(db: Session, controller: Controller, source: str, seq: int) -> Beat:
    now = datetime.now(UTC)
    with _lock:
        b = _beats.get((controller.id, source))
        if b is None:
            b = _beats[(controller.id, source)] = Beat(last=now)
        else:
            b.history = (b.history + [round((now - b.last).total_seconds(), 2)])[-10:]
        b.last, b.seq, b.count = now, seq, b.count + 1
        recovered = b.lost_since is not None
        uid, b.lost_since, b.event_uid = b.event_uid, None, None
    if recovered and uid:
        _clear(db, controller.id, uid, now)
    return b


def status(controller_id: uuid.UUID) -> list[dict]:
    now = datetime.now(UTC)
    with _lock:
        items = [(source, b) for (cid, source), b in _beats.items() if cid == controller_id]
    out = []
    for source, b in sorted(items):
        age = (now - b.last).total_seconds()
        out.append({"source": source, "ok": age <= LOST_AFTER.total_seconds(), "age_s": round(age, 1), "seq": b.seq,
                    "count": b.count, "last": b.last, "lost_since": b.lost_since,
                    "mean_interval_s": round(sum(b.history) / len(b.history), 2) if b.history else None})
    return out


def scan(db: Session) -> int:
    """Raise the alarm for every source that went silent. Called every PERIOD_S seconds."""
    now = datetime.now(UTC)
    raised = 0
    with _lock:
        lost = [(cid, source, b) for (cid, source), b in _beats.items() if b.lost_since is None and now - b.last > LOST_AFTER]
        for _cid, _source, b in lost:
            b.lost_since, b.event_uid = b.last, f"wd-{uuid.uuid4().hex[:12]}"
    for cid, source, b in lost:
        db.add(DeviceEvent(controller_id=cid, event_uid=b.event_uid, code=SOURCES.get(source, "WD-" + source.upper()),
                           priority="alarm", message_key=MESSAGE_KEY, raised_at=b.last))
        broadcaster.publish("device_event", {"controller_id": cid, "code": SOURCES.get(source), "priority": "alarm", "cleared": False})
        raised += 1
    return raised


def _clear(db: Session, controller_id: uuid.UUID, uid: str, at: datetime) -> None:
    event = db.query(DeviceEvent).filter_by(controller_id=controller_id, event_uid=uid).one_or_none()
    if event is not None and event.cleared_at is None:
        event.cleared_at = at
    broadcaster.publish("device_event", {"controller_id": controller_id, "code": uid, "priority": "alarm", "cleared": True})
