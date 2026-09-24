"""Private immutable drafts and explicit, atomic public projections. No providers."""

import base64
import json
import math
import time

from sqlalchemy import and_, or_, select

from backend.app import models as m
from backend.app import schemas as s
from backend.app.core.errors import AppError
from backend.app.services.common import digest, iso, normalize_repository, owned


def conflict(entry, expected):
    if entry.revision != expected:
        raise AppError("RANKING_REVISION_CONFLICT", "条目已在其他窗口修改，请刷新后核对再操作。")


def source(db, entry, content):
    snap = owned(db, m.Snapshot, content["source_snapshot_id"], entry.owner_id, entry.data_mode)
    analysis = owned(db, m.Analysis, content["analysis_id"], entry.owner_id, entry.data_mode)
    if snap.tool_id != entry.tool_id or analysis.snapshot_id != snap.id:
        raise AppError("RANKING_CONTENT_INVALID", "亮点分析和来源必须属于当前工具。", 422)
    refs = {f["id"] for f in snap.payload.get("fragments", []) if f.get("text", "").strip()}
    for h in content["highlights"]:
        if any(r not in refs for r in h["evidence_refs"]):
            raise AppError("RANKING_CONTENT_INVALID", "亮点引用的来源片段不存在，请重新选择。", 422)
    return snap, analysis


def canonical_link(db, entry, url):
    normalized = normalize_repository(url)
    tool = db.get(m.Tool, entry.tool_id)
    aliases = db.scalars(select(m.ToolAlias.url).where(m.ToolAlias.tool_id == entry.tool_id))
    known = {normalize_repository(u) for u in [tool.canonical_url, *aliases]}
    if normalized not in known:
        raise AppError("RANKING_CONTENT_INVALID", "链接与当前仓库不一致，请先刷新资料核实更名。", 422)
    return normalized


def snapshot_status(snap):
    missing = snap.payload.get("missing_fields", [])
    unavailable = any(str(x).startswith("UPSTREAM_") or x == "README missing" for x in missing)
    has_source = bool(snap.payload.get("fragments"))
    return "unavailable" if unavailable else "verified" if has_source else "unknown"


def new_revision(db, entry, content):
    snap, _ = source(db, entry, content)
    if content["github_url"]:
        content["github_url"] = canonical_link(db, entry, content["github_url"])
    # These values belong to the source; the browser cannot invent a verification date.
    content = {**content, "last_verified_at": iso(snap.created_at) if snap.payload.get("fragments") else None,
               "verification_status": snapshot_status(snap)}
    previous = db.get(m.PublicRevision, entry.draft_revision_id) if entry.draft_revision_id else None
    if previous and previous.content_hash == digest(content):
        return previous, False
    row = m.PublicRevision(entry_id=entry.id, version=1 if not previous else previous.version + 1,
                           content_json=content, content_hash=digest(content),
                           source_snapshot_id=snap.id, analysis_id=content["analysis_id"])
    db.add(row)
    db.flush()
    entry.draft_revision_id = row.id
    entry.updated_at = time.time()
    return row, True


def create_entry(db, owner, mode, body):
    tool = owned(db, m.Tool, body.tool_id, owner, mode)
    existing = db.scalar(select(m.PublicEntry).where(m.PublicEntry.data_mode == mode,
                                                     m.PublicEntry.github_repo_id == tool.github_repo_id))
    if existing:
        if existing.owner_id != owner:
            raise AppError("RANKING_REPOSITORY_CONFLICT", "此仓库已有榜单条目，无法重复加入。")
        return {"entry": entry_view(db, existing), "created": False}, 200
    entry = m.PublicEntry(owner_id=owner, data_mode=mode, tool_id=tool.id, github_repo_id=tool.github_repo_id)
    db.add(entry)
    db.flush()
    analysis = owned(db, m.Analysis, body.analysis_id, owner, mode)
    highlights = []
    for h in analysis.payload.get("highlights", [])[:3]:
        text = h.get("capability", "")
        if h.get("conditions"):
            text += "（" + h["conditions"] + "）"
        # Never truncate necessary conditions to fit a publishable claim.
        if len(text) <= 300:
            highlights.append({"text": text, "evidence_refs": h.get("evidence_refs", [])})
    content = s.RankingContent(name=tool.full_name[:100], github_url=tool.canonical_url,
                              source_snapshot_id=body.source_snapshot_id, analysis_id=body.analysis_id,
                              highlights=highlights).model_dump()
    new_revision(db, entry, content)
    db.flush()
    return {"entry": entry_view(db, entry), "created": True}, 201


def projection(entry, row):
    c = row.content_json
    avatar = c["bot_avatar"]
    return s.PublicToolOut(
        id=entry.public_id, version=row.version, first_published_at=iso(entry.first_published_at),
        name=c["name"], bot_avatar={"preset": avatar["preset"], "palette": avatar["palette"],
                                  "path": f'/bots/{avatar["preset"]}-{avatar["palette"]}.svg'},
        highlights=[h["text"] for h in c["highlights"]], github_url=c["github_url"],
        last_verified_at=c["last_verified_at"], verification_status=c["verification_status"],
        category=c.get("category") or None, one_line_use=c.get("one_line_use") or None,
    ).model_dump()


def issues(entry, row):
    c = row.content_json
    problems = []
    if not c["name"]:
        problems.append("请填写工具名称。")
    if not c["github_url"]:
        problems.append("请填写当前工具的 GitHub 链接。")
    if not c["highlights"] or any(not h["text"] or not h["evidence_refs"] for h in c["highlights"]):
        problems.append("至少保留一条有效亮点，每条亮点都需要文字和来源证据。")
    if entry.data_mode != "live":
        problems.append("模拟条目仅供编辑预览，不能进入公开榜单。")
    return problems


def entry_view(db, entry):
    draft = db.get(m.PublicRevision, entry.draft_revision_id)
    published = db.get(m.PublicRevision, entry.published_revision_id) if entry.published_revision_id else None
    snap = db.get(m.Snapshot, draft.source_snapshot_id)
    content = {k: v for k, v in draft.content_json.items() if k in s.RankingContent.model_fields}
    return {"id": entry.id, "public_id": entry.public_id, "state": entry.state, "revision": entry.revision,
            "data_mode": entry.data_mode, "draft_revision_id": draft.id, "draft_version": draft.version,
            "content": content, "updated_at": iso(entry.updated_at),
            "has_unpublished_changes": bool(published and published.id != draft.id),
            "published": projection(entry, published) if published else None,
            "issues": issues(entry, draft), "evidence": snap.payload.get("fragments", []),
            "history": [{"version": r.version, "created_at": iso(r.created_at),
                         "published_at": iso(r.published_at), "content": projection(entry, r)}
                        for r in db.scalars(select(m.PublicRevision).where(m.PublicRevision.entry_id == entry.id)
                                           .order_by(m.PublicRevision.version.desc()))]}


def edit_entry(db, entry, body):
    conflict(entry, body.expected_revision)
    _, changed = new_revision(db, entry, body.content.model_dump())
    if changed:
        entry.revision += 1
    db.flush()
    return entry_view(db, entry), 200


def preview(db, entry):
    row = db.get(m.PublicRevision, entry.draft_revision_id)
    return {"tool": projection(entry, row), "draft_revision_id": row.id, "entry_revision": entry.revision,
            "content_hash": row.content_hash, "issues": issues(entry, row)}


def publish(db, entry, body):
    conflict(entry, body.expected_revision)
    row = db.get(m.PublicRevision, entry.draft_revision_id)
    if row.id != body.draft_revision_id or row.content_hash != body.content_hash:
        raise AppError("RANKING_PREVIEW_STALE", "预览内容已经改变，请重新预览后发布。")
    problems = issues(entry, row)
    if problems:
        raise AppError("RANKING_CONTENT_INVALID", " ".join(problems), 422)
    source(db, entry, row.content_json)
    canonical_link(db, entry, row.content_json["github_url"])
    if entry.state == "published" and entry.published_revision_id == row.id:
        return entry_view(db, entry), 200
    now = time.time()
    kind = "update" if entry.first_published_at else "publish"
    row.reviewed_at, row.reviewed_by = now, entry.owner_id
    row.published_at = row.published_at or now
    entry.first_published_at = entry.first_published_at or now
    entry.published_revision_id, entry.state, entry.withdrawn_at = row.id, "published", None
    entry.revision += 1
    entry.updated_at = now
    db.add(m.PublicEntryEvent(entry_id=entry.id, revision_id=row.id, owner_id=entry.owner_id, kind=kind))
    db.flush()
    return entry_view(db, entry), 200


def withdraw(db, entry, body):
    conflict(entry, body.expected_revision)
    if entry.state == "draft":
        raise AppError("RANKING_CONTENT_INVALID", "草稿尚未公开，无需下架。")
    if entry.state != "withdrawn":
        entry.state, entry.withdrawn_at, entry.updated_at = "withdrawn", time.time(), time.time()
        entry.revision += 1
        db.add(m.PublicEntryEvent(entry_id=entry.id, revision_id=entry.published_revision_id,
                                 owner_id=entry.owner_id, kind="withdraw"))
        db.flush()
    return entry_view(db, entry), 200


def public_query():
    return select(m.PublicEntry).where(m.PublicEntry.data_mode == "live", m.PublicEntry.state == "published")


def public_detail(db, public_id):
    row = db.scalar(public_query().where(m.PublicEntry.public_id == public_id))
    if row is None:
        raise AppError("PUBLIC_TOOL_NOT_FOUND", "此工具尚未公开或已下架。", 404)
    return projection(row, db.get(m.PublicRevision, row.published_revision_id))


def page_rows(db, query, limit, cursor, public=False):
    timestamp = m.PublicEntry.first_published_at if public else m.PublicEntry.updated_at
    identity = m.PublicEntry.public_id if public else m.PublicEntry.id
    if cursor:
        try:
            value, id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if type(value) not in (int, float) or not math.isfinite(value) or not isinstance(id, str) or len(id) != 36:
                raise ValueError()
        except (ValueError, TypeError, UnicodeError):
            raise AppError("INPUT_INVALID", "分页标记无效，请刷新列表。", 422)
        query = query.where(or_(timestamp < value, and_(timestamp == value, identity < id)))
    rows = list(db.scalars(query.order_by(timestamp.desc(), identity.desc()).limit(limit + 1)))
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = base64.urlsafe_b64encode(json.dumps([
            last.first_published_at if public else last.updated_at,
            last.public_id if public else last.id,
        ]).encode()).decode()
    return rows[:limit], next_cursor
