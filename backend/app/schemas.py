import json
import re
from datetime import date, datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.core.errors import AppError


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginIn(Strict):
    login_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class InviteIn(Strict):
    code: str = Field(min_length=1, max_length=40)


class ImportIn(Strict):
    repository_url: str = Field(max_length=1000)


class AnalysisIn(Strict):
    topic: str = Field(default="", max_length=100)
    audience: str = Field(default="希望了解实用 AI 工具的抖音观众", min_length=1, max_length=500)
    cost_authorization_id: str | None = None


class ReviewIn(Strict):
    primary_highlight_id: str = Field(max_length=100)
    secondary_highlight_id: str | None = Field(default=None, max_length=100)
    confirmed: Literal[True]


class TaskIn(Strict):
    review_id: str
    audience: str = Field(min_length=1, max_length=500)


class RevisionIn(Strict):
    expected_revision: int = Field(ge=1)


class CostReconcileIn(Strict):
    expected_revision: int = Field(ge=0)
    resolution: Literal["billed", "not_charged", "reopened"]
    actual_amount: str | None = Field(
        default=None, pattern=r"^(0|[1-9][0-9]{0,6})(\.[0-9]{1,6})?$", max_length=14
    )
    billing_month: str | None = Field(default=None, pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$")
    evidence_type: Literal["bill_line", "provider_support"] | None = None
    evidence_reference: str = Field(default="", max_length=240)
    match_details: str = Field(default="", max_length=1000)
    reason: str = Field(min_length=5, max_length=1000)


class CostReconcileConfirmIn(CostReconcileIn):
    preview_token: str = Field(min_length=64, max_length=64)
    confirm: Literal[True]


class CreatorTestIn(Strict):
    snapshot_id: str = Field(min_length=1, max_length=36)
    tested_on: date
    environment: str = Field(min_length=1, max_length=300)
    procedure: str = Field(min_length=1, max_length=600)
    observation: str = Field(min_length=1, max_length=600)
    limitations: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def past_date(self):
        if self.tested_on > datetime.now(timezone.utc).date():
            raise ValueError("实测日期不能在未来（按 UTC 日期校验）。")
        return self


class TaskContextIn(RevisionIn):
    analysis_id: str = Field(min_length=1, max_length=36)
    primary_highlight_id: str = Field(min_length=1, max_length=100)
    secondary_highlight_id: str | None = Field(default=None, max_length=100)
    audience: str = Field(min_length=1, max_length=500)
    creator_tests: list[CreatorTestIn] = Field(default_factory=list, max_length=5)
    facts_reviewed: Literal[True]


class TaskTrashIn(RevisionIn):
    confirm: Literal[True]
    preview_token: str = Field(min_length=64, max_length=64)


class RankingAvatar(Strict):
    preset: Literal["orbit", "pixel", "spark", "pebble", "capsule", "triangle", "cloud", "drop"] = "orbit"
    palette: Literal[
        "mint", "violet", "amber", "red", "orange", "green", "blue", "pink", "brown", "gray", "ink", "cream"
    ] = "mint"


class RankingHighlight(Strict):
    text: str = Field(max_length=300)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)


class RankingContent(Strict):
    name: str = Field(max_length=100)
    bot_avatar: RankingAvatar = Field(default_factory=RankingAvatar)
    highlights: list[RankingHighlight] = Field(default_factory=list, max_length=3)
    github_url: str = Field(max_length=1000)
    source_snapshot_id: str = Field(max_length=36)
    analysis_id: str = Field(max_length=36)
    category: str = Field(default="", max_length=40)
    one_line_use: str = Field(default="", max_length=160)


class RankingCreateIn(Strict):
    tool_id: str = Field(max_length=36)
    source_snapshot_id: str = Field(max_length=36)
    analysis_id: str = Field(max_length=36)


class RankingEditIn(RevisionIn):
    content: RankingContent


class RankingPublishIn(RevisionIn):
    draft_revision_id: str = Field(max_length=36)
    content_hash: str = Field(min_length=64, max_length=64)
    content_reviewed: Literal[True]


class PublicToolOut(Strict):
    id: str
    version: int
    first_published_at: str | None
    name: str
    bot_avatar: dict[str, str]
    highlights: list[str]
    github_url: str
    last_verified_at: str | None
    verification_status: Literal["verified", "unavailable", "unknown"]
    category: str | None
    one_line_use: str | None


class PublicEventIn(Strict):
    event_id: UUID
    version: int = Field(strict=True, ge=1)
    kind: Literal["intro_open", "github_click"]


class VoiceConfirmIn(RevisionIn):
    preview_run_id: str
    accepted: Literal[True]


class DeleteIn(Strict):
    confirm: Literal[True]


class VoiceDeleteIn(RevisionIn, DeleteIn):
    pass


class ValidationIn(Strict):
    kind: Literal["writing", "voice_enrollment", "voice_preview", "voice_reuse"]
    contract_version: str = Field(min_length=1, max_length=100)
    cost_authorization_id: str | None = None
    task_id: str | None = None
    sample_asset_id: str | None = None
    voice_label: str = Field(default="我的实验音色", max_length=80)
    consent_record_id: str | None = None
    probe_voice_id: str | None = None
    text: str = Field(default="", max_length=2000)
    previous_run_id: str | None = None

    @model_validator(mode="after")
    def required_by_kind(self):
        required = {
            "writing": ["task_id"],
            "voice_enrollment": ["sample_asset_id", "consent_record_id"],
            "voice_preview": ["probe_voice_id", "text"],
            "voice_reuse": ["probe_voice_id", "text", "previous_run_id"],
        }
        if any(not getattr(self, k) for k in required[self.kind]):
            raise ValueError("missing kind-specific fields")
        return self


class PreviewIn(Strict):
    action: Literal[
        "audio_generation",
        "analysis",
        "writing",
        "generation",
        "discovery_score",
        "discovery_brief",
        "voice_enrollment",
        "voice_preview",
        "voice_reuse",
    ]
    input: dict


class AuthorizationIn(Strict):
    preview_id: str
    action: str
    input_hash: str
    max_calls: int = Field(ge=1, le=2)
    max_cost: float | None = Field(default=None, ge=0)
    accept_unknown_cost: bool


class ConsentIn(Strict):
    is_own_voice: Literal[True]
    purpose: Literal["voice_clone_validation"]
    notice_version: Literal["S1-2026-09-08"]
    cleanup_acknowledged: Literal[True]


class Fact(Strict):
    claim: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[str] = Field(min_length=1, max_length=20)
    source_type: Literal["repo_statement", "creator_test", "ai_inference"] = "repo_statement"


class Highlight(Strict):
    id: str = Field(min_length=1, max_length=100)
    capability: str = Field(min_length=1, max_length=1000)
    benefit: str = Field(min_length=1, max_length=1000)
    demo: str = Field(min_length=1, max_length=1000)
    evidence_refs: list[str] = Field(min_length=1, max_length=20)
    conditions: str = Field(default="", max_length=1000)


class AnalysisOutput(Strict):
    summary: str = Field(min_length=1, max_length=2000)
    facts: list[Fact] = Field(max_length=30)
    highlights: list[Highlight] = Field(max_length=20)
    unknowns: list[str] = Field(default_factory=list, max_length=30)
    warnings: list[str] = Field(default_factory=list, max_length=30)


class Script(Strict):
    pain: str = Field(min_length=1, max_length=1000)
    intro: str = Field(min_length=1, max_length=500)
    feature: str = Field(min_length=1, max_length=2000)
    example: str = Field(min_length=1, max_length=2000)


class Copy(Strict):
    body: str = Field(min_length=1, max_length=3000)
    tags: list[str] = Field(min_length=1, max_length=20)
    title: str | None = Field(default=None, max_length=200)


def parse_json(text):
    if len(text) > 200_000:
        raise AppError("MODEL_OUTPUT_INVALID", "模型输出过长，请核对原作业。", 422)
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)[:-3].strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        raise AppError("MODEL_OUTPUT_INVALID", "模型未返回有效 JSON，实验结果待复核。", 422)


def validate_analysis(raw, fragments):
    try:
        result = AnalysisOutput.model_validate(raw)
    except ValueError:
        raise AppError("MODEL_OUTPUT_INVALID", "分析结构不完整，请查看作业记录。", 422)
    refs = {f["id"] for f in fragments}
    for fact in result.facts:
        if fact.source_type == "creator_test":
            raise AppError("UNSUPPORTED_FACT", "没有实测记录，不能生成亲测结论。", 422)
    for item in [*result.facts, *result.highlights]:
        if not set(item.evidence_refs).issubset(refs):
            raise AppError("EVIDENCE_INVALID", "模型引用了不存在的来源片段。", 422)
    if len({h.id for h in result.highlights}) != len(result.highlights):
        raise AppError("MODEL_OUTPUT_INVALID", "亮点标识重复。", 422)
    data = result.model_dump()
    if len(data["highlights"]) > 3:
        data["warnings"].append(f"模型原始亮点 {len(data['highlights'])} 条，按顺序保留前三条。")
        data["highlights"] = data["highlights"][:3]
    return data


def validate_writing(raw, tool_name, fragments):
    # Compatibility for S1 integrations; richer diagnostics live in quality.py.
    from backend.app.services.quality import inspect_writing

    result = inspect_writing(raw, tool_name, fragments)
    return result["content"], {k: result["checks"][k] for k in ("script", "copy", "evidence")}


class DiscoveryIn(Strict):
    topic: str = Field(min_length=2, max_length=100)
    category: str = Field(default="", max_length=100)
    threshold: str = Field(default="", max_length=100)


class ScoreIn(Strict):
    discovery_id: str
    cost_authorization_id: str | None = None


class GenerationIn(RevisionIn):
    task_id: str
    mode: Literal["joint", "script", "copy"] = "joint"
    base_script_id: str | None = None
    rewrite_instruction: str = Field(default="", max_length=1000)
    rewrite_section: Literal["all", "pain", "feature", "example"] = "all"
    tone: str = Field(default="自然、具体、不夸张", max_length=200)
    cost_authorization_id: str | None = None


class TextEditIn(RevisionIn):
    kind: Literal["script", "copy"]
    content: dict
    evidence_map: dict
    parent_id: str | None = None
    script_id: str | None = None


class TextConfirmIn(RevisionIn):
    facts_reviewed: Literal[True]


class TextCleanupIn(RevisionIn):
    preview_token: str = Field(min_length=64, max_length=64)
    confirm: Literal[True]


class AudioGenerationIn(RevisionIn):
    task_id: str
    script_id: str
    probe_voice_id: str
    voice_revision: int = Field(ge=1)
    pronunciation: str = Field(default="", max_length=60)
    pronunciation_reviewed: bool = False
    cost_authorization_id: str | None = None

    @model_validator(mode="after")
    def reviewed(self):
        if self.pronunciation and not self.pronunciation_reviewed:
            raise ValueError("请确认替换只用于工具名读音，不改变正文含义。")
        return self


class AudioConfirmIn(RevisionIn):
    accepted: Literal[True]


class ExportIn(RevisionIn):
    complete: bool = True
