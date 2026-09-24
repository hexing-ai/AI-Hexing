import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import select

from backend.app.core.errors import AppError


def digest(value):
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def owned(db, cls, id, owner, mode):
    obj = db.scalar(select(cls).where(cls.id == id, cls.owner_id == owner, cls.data_mode == mode))
    if obj is None:
        raise AppError("NOT_FOUND", "记录不存在或不可访问。", 404)
    from backend.app.services.task_lifecycle import check_object

    check_object(db, obj)
    return obj


def normalize_repository(value):
    try:
        u = urlsplit(value)
        if (
            u.scheme != "https"
            or u.hostname != "github.com"
            or u.username
            or u.password
            or u.port is not None
        ):
            raise ValueError()
        parts = u.path.rstrip("/").split("/")
        if len(parts) != 3:
            raise ValueError()
        owner, repo = parts[1:]
        if repo.endswith(".git"):
            repo = repo[:-4]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", owner):
            raise ValueError()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", repo) or repo in (".", ".."):
            raise ValueError()
        return f"https://github.com/{owner.lower()}/{repo.lower()}"
    except ValueError:
        raise AppError("REPOSITORY_URL_INVALID", "请输入 https://github.com/作者/仓库 格式的仓库地址。", 422)
