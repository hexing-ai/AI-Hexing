"""Source-scoped publication checks, independent of content approval and model calls."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

POLICY_PATH = Path(__file__).resolve().parents[1] / "config/platforms/douyin.json"
FIELDS = {"title", "body", "tags_count", "tag_length", "combined_text"}
LABELS = {
    "title": "标题",
    "body": "正文",
    "tags_count": "话题数量",
    "tag_length": "最长话题",
    "combined_text": "组合文本",
}
NOTICE = "仅检查已核实且适用于手动发布的格式限制，不代表平台审核通过或保证发布成功。"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(Strict):
    id: str = Field(min_length=1, max_length=80)
    url: str = Field(max_length=2000)
    title: str = Field(min_length=1, max_length=200)
    surface: Literal["manual_copy", "openapi"]
    accessed_at: AwareDatetime
    available: bool = Field(strict=True)
    excerpt: str = Field(min_length=5, max_length=2000)

    @model_validator(mode="after")
    def official(self):
        u = urlsplit(self.url)
        if (
            u.scheme != "https"
            or u.netloc != u.hostname
            or u.query
            or u.hostname
            not in {"creator.douyin.com", "developer.open-douyin.com", "open.douyin.com", "www.douyin.com"}
        ):
            raise ValueError("Official public source required")
        return self


class Rule(Strict):
    field: Literal["title", "body", "tags_count", "tag_length", "combined_text"]
    status: Literal["unverified", "verified"]
    maximum: int | None = Field(default=None, gt=0, le=1_000_000, strict=True)
    unit: Literal["unicode_codepoints", "utf16_units", "items"] | None = None
    source_id: str | None = None
    verified_at: AwareDatetime | None = None
    valid_until: AwareDatetime | None = None

    @model_validator(mode="after")
    def evidence(self):
        values = (self.maximum, self.unit, self.source_id, self.verified_at, self.valid_until)
        if self.status == "unverified":
            if any(v is not None for v in values):
                raise ValueError("Unknown rules must not imply a limit")
        else:
            if any(v is None for v in values):
                raise ValueError("Incomplete verification")
            if (self.field == "tags_count") != (self.unit == "items"):
                raise ValueError("Unsupported counting unit")
            if self.valid_until <= self.verified_at:
                raise ValueError("Invalid verification period")
        return self


class Policy(Strict):
    version: str = Field(min_length=1, max_length=100)
    platform: Literal["douyin"]
    surface: Literal["manual_copy"]
    reviewed_at: AwareDatetime
    review_note: str = Field(min_length=5, max_length=2000)
    sources: list[Source] = Field(max_length=20)
    rules: list[Rule] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def scoped_sources(self):
        if {r.field for r in self.rules} != FIELDS:
            raise ValueError("All five rules required")
        sources = {s.id: s for s in self.sources}
        if len(sources) != len(self.sources):
            raise ValueError("Duplicate source")
        for r in self.rules:
            if r.status == "verified":
                source = sources.get(r.source_id)
                if (
                    not source
                    or not source.available
                    or source.surface != self.surface
                    or source.accessed_at > r.verified_at
                    or r.verified_at > self.reviewed_at
                ):
                    raise ValueError("Rule needs matching evidence and dates")
        return self


def now():
    return datetime.now(timezone.utc)


def load():
    try:
        raw = POLICY_PATH.read_bytes()
        if len(raw) > 64_000:
            raise ValueError("Policy too large")
        p = Policy.model_validate_json(raw)
        if p.reviewed_at > now() or any(s.accessed_at > now() for s in p.sources):
            raise ValueError("Future review date")
        return p, hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError):
        # No raw config/path/parse error leaks and no false success fallback.
        return None, None


def compose_copy(content):
    """Exact local TXT layout; not an assumed mapping to Douyin's publisher fields."""
    content = content or {}
    return "\n\n".join(
        str(v)
        for v in [
            content.get("title"),
            content.get("body"),
            " ".join("#" + str(t) for t in content.get("tags", [])),
        ]
        if v
    )


def count(value, unit):
    # Surrogate code points count as one UTF-16 unit; do not normalize stored text.
    return (
        len(value.encode("utf-16-le", errors="surrogatepass")) // 2 if unit == "utf16_units" else len(value)
    )


def describe():
    policy, fingerprint = load()
    if policy is None:
        return {
            "platform": "douyin",
            "surface": "manual_copy",
            "version": None,
            "policy_hash": None,
            "status": "config_error",
            "reviewed_at": None,
            "verified_at": None,
            "rules": [],
            "sources": [],
            "review_note": "平台配置暂不可用，请继续保存草稿并在发布前核对。",
            "notice": NOTICE,
        }
    data = policy.model_dump(mode="json")
    for r in data["rules"]:
        original = next(rule for rule in policy.rules if rule.field == r["field"])
        if original.status == "verified" and original.valid_until <= now():
            r["status"] = "expired"
    active = [r for r in data["rules"] if r["status"] == "verified"]
    status = (
        "verified"
        if len(active) == 5
        else "partial"
        if active
        else "expired"
        if any(r["status"] == "expired" for r in data["rules"])
        else "unverified"
    )
    verified = max(
        (r.verified_at for r in policy.rules if r.status == "verified" and r.valid_until > now()),
        default=None,
    )
    return {
        **data,
        "status": status,
        "verified_at": verified.isoformat() if verified else None,
        "policy_hash": fingerprint,
        "notice": NOTICE,
    }


def evaluate(content, kind="copy"):
    if kind != "copy":
        return {"status": "not_applicable", "result": "not_applicable", "verified_at": None, "checks": []}
    policy = describe()
    malformed = not isinstance(content, dict)
    content = content if isinstance(content, dict) else {}
    tags = content.get("tags", [])
    tags = tags if isinstance(tags, list) else []
    malformed = (
        malformed
        or not isinstance(content.get("body"), str)
        or any(not isinstance(t, str) for t in tags)
        or any(content.get(k) is not None and not isinstance(content.get(k), str) for k in ("title", "body"))
        or not isinstance(content.get("tags", []), list)
    )
    safe = {k: content.get(k) if isinstance(content.get(k), str) else "" for k in ("title", "body")}
    safe["tags"] = [t for t in tags if isinstance(t, str)]
    rules = {r["field"]: r for r in policy["rules"]}
    checks = []
    for field in ("title", "body", "tags_count", "tag_length", "combined_text"):
        rule = rules.get(field, {})
        active = rule.get("status") == "verified"
        unit = rule.get("unit") if active else "items" if field == "tags_count" else "unicode_codepoints"
        used = (
            len(safe["tags"])
            if field == "tags_count"
            else max((count(t, unit) for t in safe["tags"]), default=0)
            if field == "tag_length"
            else count(compose_copy(safe) if field == "combined_text" else safe[field], unit)
        )
        result = "not_checked" if not active else "exceeded" if used > rule["maximum"] else "within_limit"
        checks.append(
            {
                "field": field,
                "label": LABELS[field],
                "count": used,
                "unit": unit,
                "maximum": rule.get("maximum") if active else None,
                "status": rule.get("status", policy["status"]),
                "result": result,
                "source_id": rule.get("source_id"),
                "verified_at": rule.get("verified_at"),
                "valid_until": rule.get("valid_until"),
            }
        )
    result = (
        "needs_review"
        if malformed
        else "exceeded"
        if any(c["result"] == "exceeded" for c in checks)
        else "within_known_limits"
        if policy["status"] == "verified"
        else "needs_review"
    )
    return {
        **policy,
        "checks": checks,
        "result": result,
        "count_note": "未核实字段仅按本地 Unicode 码点统计；组合文本包含标题、正文、#话题、空格和换行，非平台字段映射。",
        "content_invalid": malformed,
    }
