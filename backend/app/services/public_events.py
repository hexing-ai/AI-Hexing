"""Small anonymous counters; no profiles, cookies, provider calls or unbounded queue."""

import hashlib
import secrets
import threading
import time

from sqlalchemy import delete, func, select

from backend.app import models as m
from backend.app.core.errors import AppError
from backend.app.services.ranking import public_query


class EventLimiter:
    def __init__(self, settings, clock=time.monotonic):
        self.settings, self.clock = settings, clock
        self.salt = secrets.token_bytes(32)
        self.lock = threading.Lock()
        self.window, self.total, self.sources = -1, 0, {}

    def admit(self, source):
        # Do not retain the address. Salt changes on process restart.
        key = hashlib.sha256(self.salt + source.encode()).digest()
        window = int(self.clock() // 60)
        with self.lock:
            if window != self.window:
                self.window, self.total, self.sources = window, 0, {}
            if (self.total >= self.settings.public_event_total_per_minute
                    or self.sources.get(key, 0) >= self.settings.public_event_source_per_minute):
                raise AppError("PUBLIC_EVENT_RATE_LIMIT", "交互记录暂时繁忙，仍可继续浏览。", 429)
            self.total += 1
            self.sources[key] = self.sources.get(key, 0) + 1


def record(db, settings, public_id, body):
    entry = db.scalar(public_query().where(m.PublicEntry.public_id == public_id))
    if entry is None:
        raise AppError("PUBLIC_TOOL_NOT_FOUND", "工具已下架或尚未公开。", 404)
    revision = db.get(m.PublicRevision, entry.published_revision_id)
    if revision.version != body.version:
        raise AppError("PUBLIC_EVENT_VERSION_CONFLICT", "工具介绍已更新，请重新读取。", 409)
    existing = db.scalar(select(m.PublicInteractionEvent).where(m.PublicInteractionEvent.event_id == str(body.event_id)))
    if existing:
        if (existing.entry_id, existing.revision_id, existing.kind) != (entry.id, revision.id, body.kind):
            raise AppError("PUBLIC_EVENT_CONFLICT", "交互标记已使用。", 409)
        return {"accepted": True}
    cutoff = time.time() - settings.public_event_retention_days * 86400
    expired = select(m.PublicInteractionEvent.id).where(m.PublicInteractionEvent.created_at < cutoff).limit(500)
    db.execute(delete(m.PublicInteractionEvent).where(m.PublicInteractionEvent.id.in_(expired)))
    if db.scalar(select(func.count()).select_from(m.PublicInteractionEvent)) >= settings.public_event_max_rows:
        # Cleanup may still commit; callers handle this non-critical capacity response.
        return None
    db.add(m.PublicInteractionEvent(event_id=str(body.event_id), entry_id=entry.id,
                                    revision_id=revision.id, kind=body.kind))
    db.flush()
    return {"accepted": True}


def stats(db, entry, settings):
    cutoff = time.time() - settings.public_event_retention_days * 86400
    values = dict(db.execute(select(m.PublicInteractionEvent.kind, func.count())
                            .where(m.PublicInteractionEvent.entry_id == entry.id,
                                   m.PublicInteractionEvent.created_at >= cutoff)
                            .group_by(m.PublicInteractionEvent.kind)).all())
    return {"intro_open": values.get("intro_open", 0), "github_click": values.get("github_click", 0),
            "retention_days": settings.public_event_retention_days}
