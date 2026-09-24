"""S4.1 explicit private publishing and anonymous whitelist reads."""

import asyncio
from typing import Literal

from fastapi import Query, Request
from sqlalchemy import select

from backend.app import models as m
from backend.app import schemas as s
from backend.app.core.db import transaction
from backend.app.core.errors import AppError
from backend.app.services import ranking as r
from backend.app.services.common import owned
from backend.app.services.public_events import EventLimiter, record, stats


def install(app, settings, sf, read, mutate, ok):
    mode = settings.provider_mode
    limiter = EventLimiter(settings)
    app.state.public_event_limiter = limiter

    @app.post("/api/v1/ranking/entries")
    def create(request: Request, body: s.RankingCreateIn):
        return mutate(request, body.model_dump(), lambda db, owner: r.create_entry(db, owner, mode, body))

    @app.get("/api/v1/ranking/entries")
    def entries(
        request: Request,
        state: Literal["draft", "published", "withdrawn"] | None = None,
        cursor: str | None = Query(None, max_length=256),
        limit: int = Query(20, ge=1, le=100),
    ):
        with read(request) as (db, owner, _):
            query = select(m.PublicEntry).where(
                m.PublicEntry.owner_id == owner, m.PublicEntry.data_mode == mode
            )
            if state:
                query = query.where(m.PublicEntry.state == state)
            rows, next_cursor = r.page_rows(db, query, limit, cursor)
            return ok(request, {"items": [r.entry_view(db, e) for e in rows], "next_cursor": next_cursor})

    @app.get("/api/v1/ranking/entries/{id}")
    def entry(id: str, request: Request):
        with read(request) as (db, owner, _):
            return ok(request, r.entry_view(db, owned(db, m.PublicEntry, id, owner, mode)))

    @app.get("/api/v1/ranking/entries/{id}/stats")
    def entry_stats(id: str, request: Request):
        with read(request) as (db, owner, _):
            return ok(request, stats(db, owned(db, m.PublicEntry, id, owner, mode), settings))

    @app.patch("/api/v1/ranking/entries/{id}")
    def edit(id: str, request: Request, body: s.RankingEditIn):
        return mutate(
            request,
            body.model_dump(),
            lambda db, owner: r.edit_entry(db, owned(db, m.PublicEntry, id, owner, mode), body),
        )

    @app.get("/api/v1/ranking/entries/{id}/preview")
    def preview(id: str, request: Request):
        with read(request) as (db, owner, _):
            return ok(request, r.preview(db, owned(db, m.PublicEntry, id, owner, mode)))

    @app.post("/api/v1/ranking/entries/{id}/publish")
    def publish(id: str, request: Request, body: s.RankingPublishIn):
        return mutate(
            request,
            body.model_dump(),
            lambda db, owner: r.publish(db, owned(db, m.PublicEntry, id, owner, mode), body),
        )

    @app.post("/api/v1/ranking/entries/{id}/withdraw")
    def withdraw(id: str, request: Request, body: s.RevisionIn):
        return mutate(
            request,
            body.model_dump(),
            lambda db, owner: r.withdraw(db, owned(db, m.PublicEntry, id, owner, mode), body),
        )

    @app.get("/api/v1/public/tools")
    def public_tools(
        request: Request,
        cursor: str | None = Query(None, max_length=256),
        limit: int = Query(50, ge=1, le=100),
    ):
        with sf() as db:
            rows, next_cursor = r.page_rows(db, r.public_query(), limit, cursor, public=True)
            return ok(
                request,
                {
                    "items": [
                        r.projection(e, db.get(m.PublicRevision, e.published_revision_id)) for e in rows
                    ],
                    "next_cursor": next_cursor,
                },
            )

    @app.get("/api/v1/public/tools/{public_id}")
    def public_tool(public_id: str, request: Request):
        with sf() as db:
            return ok(request, r.public_detail(db, public_id))

    @app.post("/api/v1/public/tools/{public_id}/events")
    async def public_event(public_id: str, request: Request):
        # Deploy with --no-proxy-headers; forwarding through Next shares a peer bucket.
        # Even under a misconfigured proxy, arbitrary forwarded values never become keys.
        peer = (
            "proxied"
            if "x-forwarded-for" in request.headers
            else (request.client.host if request.client else "unknown")
        )
        limiter.admit(peer)
        if request.headers.get("origin") != settings.web_origin:
            raise AppError("PUBLIC_EVENT_ORIGIN_INVALID", "交互记录仅接受同源请求。", 403)
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            raise AppError("PUBLIC_EVENT_CONTENT_TYPE", "请使用 JSON 提交交互记录。", 415)
        raw = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > 1024:
                        raise AppError("PUBLIC_EVENT_TOO_LARGE", "交互记录超过长度限制。", 413)
                    raw.extend(chunk)
        except TimeoutError:
            raise AppError("PUBLIC_EVENT_TIMEOUT", "交互记录接收超时。", 408)
        body = s.PublicEventIn.model_validate_json(bytes(raw))

        # Offload the serialized SQLite write so an anonymous counter never blocks the event loop.
        def write():
            with transaction(sf) as db:
                return record(db, settings, public_id, body)

        from starlette.concurrency import run_in_threadpool

        result = await run_in_threadpool(write)
        if result is None:
            raise AppError("PUBLIC_EVENT_CAPACITY", "交互记录空间暂满，仍可继续浏览。", 503)
        return ok(request, result, 202)
