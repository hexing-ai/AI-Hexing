"""Single persistent worker. External execution is not claimed to be exactly-once."""

import base64
import hashlib
import json
import logging
import os
import signal
import threading
import time

from sqlalchemy import and_, or_, select, update

from backend.app import models as m
from backend.app.core.config import get_settings
from backend.app.core.db import factory, transaction
from backend.app.core.errors import AppError, ProviderError, error_dict
from backend.app.schemas import validate_analysis
from backend.app.services import costs
from backend.app.services import task_lifecycle as life
from backend.app.services.assets import (
    asset_path,
    delete_asset_file,
    inspect_wav,
    normalize_cosyvoice_wav,
    save_asset,
)
from backend.app.services.audio import save_audio
from backend.app.services.common import digest, owned
from backend.app.services.creation import save_generation
from backend.app.services.discovery import rank, search
from backend.app.services.exports import build_package, save_export
from backend.app.services.jobs import validate_inputs
from backend.app.services.providers import Providers, bounded_get
from backend.app.services.quality import inspect_writing


class Worker:
    def __init__(self, settings=None, session_factory=None, providers=None):
        self.s = settings or get_settings()
        self.sf = session_factory or factory(self.s)
        self.providers = providers or Providers(self.s)
        self.identity = m.uid()

    def checkpoint_path(self, job_id):
        return asset_path(self.s, f"checkpoints/{job_id}.json")

    def checkpoint(self, job, result):
        path = self.checkpoint_path(job.id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = path.with_suffix(".part")
        payload = {"input_hash": job.input_hash, "result": result}
        with temp.open("w") as f:
            os.chmod(temp, 0o600)
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)

    def recover(self):
        recoverable = []
        with transaction(self.sf) as db:
            rows = db.scalars(
                select(m.Job).where(
                    m.Job.data_mode == self.s.provider_mode,
                    or_(
                        and_(m.Job.status == "running", m.Job.lease_expires_at < time.time()),
                        m.Job.status == "needs_reconciliation",
                        and_(m.Job.status == "cancelled", m.Job.phase == "task_trashed"),
                    ),
                )
            )
            for j in rows:
                if (
                    j.status in ("needs_reconciliation", "cancelled")
                    and not self.checkpoint_path(j.id).is_file()
                ):
                    continue
                attempt = db.scalar(
                    select(m.Attempt).where(m.Attempt.job_id == j.id).order_by(m.Attempt.attempt_no.desc())
                )
                if self.checkpoint_path(j.id).is_file():
                    recoverable.append(j.id)
                    if j.status != "cancelled":
                        j.status = "needs_reconciliation"
                elif j.kind == "export" or not attempt or attempt.dispatch_state == "not_sent":
                    j.status, j.phase, j.lease_owner = "queued", "recovered_not_sent", None
                else:
                    costs.stop(db, j)
                    j.status, j.phase = "needs_reconciliation", "interrupted_after_dispatch"
                    j.error = error_dict(
                        AppError("JOB_NEEDS_RECONCILIATION", "服务调用结果待核对，不会自动重复发送。")
                    )
                j.updated_at = time.time()
        for id in recoverable:
            try:
                saved = json.loads(self.checkpoint_path(id).read_text())
                with self.sf() as db:
                    job = db.get(m.Job, id)
                    valid = saved["input_hash"] == job.input_hash
                if valid:
                    self.finish(id, saved["result"])
            except Exception:
                logging.getLogger("hexing").warning("checkpoint_recovery_pending job_id=%s", id)

    def heartbeat(self, job_id, stopped):
        while not stopped.wait(self.s.job_heartbeat_seconds):
            with transaction(self.sf) as db:
                db.execute(
                    update(m.Job)
                    .where(m.Job.id == job_id, m.Job.lease_owner == self.identity, m.Job.status == "running")
                    .values(lease_expires_at=time.time() + self.s.job_lease_seconds)
                )

    def recover_audio(self, id):
        """Repair a known local WAV validation failure from its saved response."""
        with transaction(self.sf) as db:
            j = db.get(m.Job, id)
            if not j or j.data_mode != self.s.provider_mode or j.data_mode != "live":
                raise AppError("AUDIO_RECOVERY_INVALID", "没有可恢复的真实音频作业。")
            if j.status == "succeeded":
                return
            if (
                j.kind not in ("voice_preview", "voice_reuse")
                or j.status != "failed"
                or (j.error or {}).get("code") != "AUDIO_FORMAT_INVALID"
                or not self.checkpoint_path(id).is_file()
            ):
                raise AppError("AUDIO_RECOVERY_INVALID", "该作业不符合本地音频格式恢复条件。")
            saved = json.loads(self.checkpoint_path(id).read_text())
            result = saved["result"]
            if (
                saved.get("input_hash") != j.input_hash
                or result.get("metrics", {}).get("model") != "cosyvoice-v3.5-flash"
            ):
                raise AppError("AUDIO_RECOVERY_INVALID", "已保存结果与原作业不匹配。")
            inspect_wav(normalize_cosyvoice_wav(base64.b64decode(result["audio_base64"], validate=True)))
            db.add(
                m.EvidenceEvent(
                    owner_id=j.owner_id,
                    data_mode=j.data_mode,
                    event_type="audio_local_recovery",
                    object_id=j.id,
                    details={
                        "original_error": j.error,
                        "source": "saved_provider_response",
                        "new_provider_calls": 0,
                    },
                )
            )
            j.status, j.phase, j.error = "needs_reconciliation", "recovering_saved_audio", None
            j.updated_at = time.time()
        self.finish(id, result)

    def claim(self):
        with transaction(self.sf) as db:
            j = db.scalar(
                select(m.Job)
                .where(m.Job.status == "queued", m.Job.data_mode == self.s.provider_mode)
                .order_by(m.Job.created_at)
                .limit(1)
            )
            if not j:
                return None
            j.status, j.phase = "running", "preparing"
            j.lease_owner = self.identity
            j.lease_expires_at = time.time() + self.s.job_lease_seconds
            j.updated_at = time.time()
            return j.id

    def prepare(self, id):
        with transaction(self.sf) as db:
            j = db.get(m.Job, id)
            if j.status != "running":
                return None
            if life.state(db, life.task_job_id(j)) in life.CLOSED:
                costs.stop(db, j)
                j.status, j.phase = "cancelled", "task_trashed"
                return None
            inputs = dict(j.input_json)
            if j.kind not in ("import", "discovery_search", "voice_delete", "export"):
                validate_inputs(db, self.s, j.owner_id, j.kind, inputs)
            if j.kind not in ("import", "discovery_search", "voice_delete", "export"):
                self.providers.check(j.kind)
            latest = db.scalar(
                select(m.Attempt).where(m.Attempt.job_id == j.id).order_by(m.Attempt.attempt_no.desc())
            )
            number = latest.attempt_no + 1 if latest else 1
            attempt = m.Attempt(
                job_id=id,
                attempt_no=number,
                dispatch_state="not_sent",
                settings_summary={
                    "kind": j.kind,
                    "mode": j.data_mode,
                    "model": self.s.qwen_model
                    if j.kind in ("analysis", "writing", "generation", "discovery_score", "discovery_brief")
                    else (
                        None
                        if j.kind in ("import", "discovery_search", "export")
                        else self.s.voice_target_model
                    ),
                    "region": self.s.qwen_region
                    if j.kind in ("analysis", "writing", "generation", "discovery_score", "discovery_brief")
                    else self.s.voice_region,
                    "enable_thinking": False
                    if j.kind in ("analysis", "discovery_score", "discovery_brief")
                    else self.s.qwen_writing_thinking,
                },
            )
            db.add(attempt)
            db.flush()
            if j.kind == "export":
                item = owned(db, m.ExportManifest, inputs["export_id"], j.owner_id, j.data_mode)
                inputs = {**inputs, "manifest": item.payload}
                if item.payload["audio"]:
                    asset = owned(db, m.Asset, item.payload["audio"]["asset_id"], j.owner_id, j.data_mode)
                    if asset.state != "ready" or not asset_path(self.s, asset.storage_key).is_file():
                        raise AppError(
                            "ASSET_UNAVAILABLE", "固定版本的音频不存在，无法重新打包；不会调用模型。", 404
                        )
                    inputs["audio_key"] = asset.storage_key
            elif j.kind == "audio_generation":
                version = db.scalar(select(m.AudioVersion).where(m.AudioVersion.job_id == j.id))
                voice = owned(db, m.ProbeVoice, version.voice_id, j.owner_id, j.data_mode)
                inputs = {
                    **inputs,
                    "provider_voice_id": voice.provider_voice_id,
                    "text": version.payload["synthesis_text"],
                }
            elif j.kind in ("analysis", "discovery_score", "discovery_brief", "generation", "writing"):
                from backend.app.services.jobs import text_inputs

                inputs = text_inputs(db, self.s, j.owner_id, j.kind, inputs)
            elif j.kind == "voice_enrollment":
                a = owned(db, m.Asset, inputs["sample_asset_id"], j.owner_id, j.data_mode)
                v = m.ProbeVoice(
                    owner_id=j.owner_id,
                    data_mode=j.data_mode,
                    sample_asset_id=a.id,
                    provider_scope=self.providers.scope(),
                )
                db.add(v)
                db.flush()
                inputs = {
                    **inputs,
                    "voice_id": v.id,
                    "signed_url": a.source_url or ("mock://" + a.id),
                    "sample_sha": a.sha256,
                }
                j.result_ref = {"probe_voice_id": v.id}
            elif j.kind in ("voice_preview", "voice_reuse", "voice_delete"):
                v = owned(db, m.ProbeVoice, inputs["probe_voice_id"], j.owner_id, j.data_mode)
                inputs = {**inputs, "provider_voice_id": v.provider_voice_id}
            if j.authorization_id:
                auth = db.get(m.CostAuthorization, j.authorization_id)
                if (
                    not auth
                    or auth.consumed_by != j.id
                    or auth.used_calls >= auth.max_calls
                    or auth.expires_at <= time.time()
                ):
                    raise AppError("COST_AUTHORIZATION_EXHAUSTED", "本次许可已到期或次数已用完。")
                auth.used_calls += 1
            costs.before_dispatch(db, self.s, j, inputs)
            # Commit the ambiguous sending boundary BEFORE any external call.
            attempt.dispatch_state = "dispatching"
            j.phase, j.updated_at = "calling_provider", time.time()
            return j, inputs

    def perform(self, job, inputs):
        if job.kind == "export":
            try:
                audio = (
                    asset_path(self.s, inputs["audio_key"]).read_bytes() if inputs.get("audio_key") else None
                )
                data = build_package(self.s, inputs["manifest"], audio)
                return {"package_base64": base64.b64encode(data).decode(), "metrics": {}}
            except OSError:
                raise AppError(
                    "EXPORT_FILE_UNAVAILABLE", "素材文件读取失败，请恢复文件后重新打包；不会调用模型。"
                )
        if job.kind == "import":
            meta, readme, missing = self.providers.github(inputs["repository_url"])
            return {"metadata": meta, "readme": readme, "missing": missing, "metrics": {}}
        if job.kind == "discovery_search":
            return {**search(self.providers, inputs), "metrics": {}}
        if job.kind in ("discovery_score", "discovery_brief"):
            raw, metrics = self.providers.text(job.kind, inputs)
            return {"raw_output": raw, "metrics": metrics, "candidates": inputs["candidates"]}
        if job.kind in ("analysis", "writing", "generation"):
            output, metrics = self.providers.text(job.kind, inputs)
            return {
                "raw_output": output,
                "metrics": metrics,
                "fragments": inputs["fragments"],
                "tool_name": inputs.get("tool_name"),
                "quality_context": {
                    k: inputs.get(k)
                    for k in (
                        "metadata",
                        "missing_fields",
                        "unknowns",
                        "highlights",
                        "creator_tests",
                        "context_id",
                        "tool_name",
                        "fragments",
                        "source_script",
                        "source_evidence_map",
                        "rewrite_section",
                    )
                },
            }
        if job.kind == "voice_enrollment":
            if job.data_mode == "live":
                import httpx

                try:
                    with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
                        sample = bounded_get(client, inputs["signed_url"], max_bytes=10 * 1024 * 1024)
                except ProviderError as error:
                    if error.code == "UPSTREAM_403":
                        raise AppError(
                            "SAMPLE_DOWNLOAD_FORBIDDEN",
                            "OSS 拒绝读取录音：签名可能已过期或文件权限不正确。请重新生成并绑定签名 URL。"
                            "本次尚未调用百炼声音复刻。",
                            422,
                        )
                    raise
                if hashlib.sha256(sample).hexdigest() != inputs["sample_sha"]:
                    raise AppError("SAMPLE_URL_MISMATCH", "受控 URL 的文件与已确认录音不一致。")
            voice_id, metrics = self.providers.enroll(inputs["signed_url"])
            return {"voice_id": inputs["voice_id"], "provider_voice_id": voice_id, "metrics": metrics}
        if job.kind in ("voice_preview", "voice_reuse", "audio_generation"):
            if job.kind == "audio_generation":
                audio, metrics = self.providers.synthesize(
                    inputs["provider_voice_id"], inputs["text"], formal=True
                )
            else:
                audio, metrics = self.providers.synthesize(inputs["provider_voice_id"], inputs["text"])
            return {
                "audio_base64": base64.b64encode(audio).decode(),
                "voice_fingerprint": digest(inputs["provider_voice_id"]),
                "metrics": metrics,
            }
        if job.kind == "voice_delete":
            confirmed = bool(inputs["provider_voice_id"]) and self.providers.delete_voice(
                inputs["provider_voice_id"]
            )
            return {"remote_deleted": confirmed, "metrics": {}}
        raise AppError("JOB_KIND_INVALID", "未知作业类型。", 422)

    def finish(self, id, result):
        with transaction(self.sf) as db:
            j = db.get(m.Job, id)
            if not j or j.status in ("succeeded", "failed"):
                return
            attempt = db.scalar(
                select(m.Attempt).where(m.Attempt.job_id == id).order_by(m.Attempt.attempt_no.desc())
            )
            run = db.scalar(select(m.ValidationRun).where(m.ValidationRun.job_id == id))
            metrics = result.get("metrics", {})
            costs.settle(db, j, metrics)
            if attempt:
                attempt.dispatch_state = "finished"
                attempt.provider_request_id = metrics.get("request_id")
                attempt.usage = metrics.get("usage")
                attempt.elapsed_ms = result.get("elapsed_ms")
            if life.state(db, life.task_job_id(j)) in life.CLOSED:
                j.status, j.phase = "cancelled", "task_trashed"
            if j.status == "cancelled":
                if j.kind == "voice_enrollment":
                    v = db.get(m.ProbeVoice, result["voice_id"])
                    v.provider_voice_id = result["provider_voice_id"]
                    v.status, v.revision = "remote_deletion_pending", v.revision + 1
                if run:
                    run.verdict = "blocked"
                db.commit()
                self.checkpoint_path(id).unlink(missing_ok=True)
                return
            owner, mode = j.owner_id, j.data_mode
            if j.kind == "import":
                meta = result["metadata"]
                tool = db.scalar(
                    select(m.Tool).where(
                        m.Tool.owner_id == owner,
                        m.Tool.data_mode == mode,
                        m.Tool.github_repo_id == meta["id"],
                    )
                )
                if not tool:
                    tool = m.Tool(
                        owner_id=owner,
                        data_mode=mode,
                        github_repo_id=meta["id"],
                        canonical_url=meta["html_url"],
                        full_name=meta["full_name"],
                        metadata_json=meta,
                    )
                    db.add(tool)
                    db.flush()
                else:
                    tool.canonical_url, tool.full_name, tool.metadata_json = (
                        meta["html_url"],
                        meta["full_name"],
                        meta,
                    )
                for url in {j.input_json["repository_url"], meta["html_url"].lower()}:
                    alias = db.scalar(
                        select(m.ToolAlias).where(
                            m.ToolAlias.owner_id == owner,
                            m.ToolAlias.data_mode == mode,
                            m.ToolAlias.url == url,
                        )
                    )
                    if not alias:
                        db.add(m.ToolAlias(owner_id=owner, data_mode=mode, tool_id=tool.id, url=url))
                text = result["readme"]
                missing = list(result["missing"])
                if not text:
                    missing.append("README missing")
                if not meta.get("license"):
                    missing.append("license unknown")
                if len(text) > 24000:
                    missing.append("README truncated: original retained privately")
                # Keep full acquired text private; only bounded fragments go to the model.
                fragments = [
                    {
                        "id": f"f{i + 1}",
                        "text": text[start : start + 3000],
                        "source_url": meta["html_url"] + "#readme",
                    }
                    for i, start in enumerate(range(0, min(len(text), 24000), 3000))
                ]
                if not fragments and meta.get("description"):
                    fragments = [
                        {
                            "id": "metadata-description",
                            "text": meta["description"],
                            "source_url": meta["html_url"],
                        }
                    ]
                snap = m.Snapshot(
                    owner_id=owner,
                    data_mode=mode,
                    tool_id=tool.id,
                    source_sha=digest({"metadata": meta, "readme": text}),
                    payload={
                        "canonical_url": meta["html_url"],
                        "metadata": meta,
                        "fragments": fragments,
                        "missing_fields": missing,
                        "readme_text": text,
                    },
                )
                db.add(snap)
                db.flush()
                j.result_ref = {"tool_id": tool.id, "snapshot_id": snap.id}
            elif j.kind == "discovery_search":
                batch = db.get(m.Discovery, j.input_json["discovery_id"])
                batch.payload = {**batch.payload, **{k: v for k, v in result.items() if k != "metrics"}}
                j.result_ref = {"discovery_id": batch.id}
            elif j.kind == "discovery_brief":
                from backend.app.services.discovery_briefs import validate_batch

                batch = db.get(m.Discovery, j.input_json["discovery_id"])
                updated = validate_batch(result["raw_output"], result["candidates"])
                by_id = {c["metadata"]["id"]: c["brief"] for c in updated}
                candidates = [
                    {**c, "brief": by_id.get(c["metadata"]["id"], c.get("brief"))}
                    for c in batch.payload["candidates"]
                ]
                record = {
                    "job_id": j.id,
                    "raw_output": result["raw_output"],
                    "raw_text": metrics.get("raw_text", json.dumps(result["raw_output"], ensure_ascii=False)),
                    "metrics": {k: v for k, v in metrics.items() if k != "raw_text"},
                    "prompt_version": "discovery-brief-v1",
                }
                batch.payload = {
                    **batch.payload,
                    "candidates": candidates,
                    "brief_runs": [*batch.payload.get("brief_runs", []), record],
                }
                j.result_ref = {"discovery_id": batch.id}
            elif j.kind == "discovery_score":
                batch = db.get(m.Discovery, j.input_json["discovery_id"])
                scores = rank(result["raw_output"], result["candidates"])
                record = {
                    **scores,
                    "job_id": j.id,
                    "raw_output": result["raw_output"],
                    "raw_text": metrics.get("raw_text", json.dumps(result["raw_output"], ensure_ascii=False)),
                    "metrics": {k: v for k, v in metrics.items() if k != "raw_text"},
                    "prompt_version": metrics.get("prompt_version", "discovery-v2"),
                }
                batch.payload = {
                    **batch.payload,
                    "status": "scored",
                    "score_runs": [*batch.payload.get("score_runs", []), record],
                }
                j.result_ref = {"discovery_id": batch.id}
            elif j.kind == "export":
                save_export(db, self.s, j, result)
            elif j.kind == "audio_generation":
                if not save_audio(db, self.s, j, result):
                    j.updated_at = time.time()
                    db.commit()
                    self.checkpoint_path(id).unlink(missing_ok=True)
                    return
            elif j.kind == "generation":
                save_generation(db, j, result)
            elif j.kind == "analysis":
                data = validate_analysis(result["raw_output"], result["fragments"])
                a = m.Analysis(
                    owner_id=owner,
                    data_mode=mode,
                    snapshot_id=j.input_json["snapshot_id"],
                    context_hash=j.input_hash,
                    prompt_version="analysis-v1",
                    payload={
                        **data,
                        "audience": j.input_json["audience"],
                        "topic": j.input_json["topic"],
                        "prompt_version": "analysis-v1",
                        "schema_version": 1,
                        "mode": mode,
                    },
                )
                db.add(a)
                db.flush()
                j.result_ref = {"analysis_id": a.id}
            elif j.kind == "writing":
                report = inspect_writing(
                    result["raw_output"],
                    result["tool_name"],
                    result["fragments"],
                    result.get("quality_context"),
                )
                run.output = {
                    **report,
                    "metrics": {k: v for k, v in metrics.items() if k != "raw_text"},
                    "raw_text": metrics.get("raw_text", json.dumps(result["raw_output"], ensure_ascii=False)),
                    "raw_output": result["raw_output"],
                    "prompt_version": metrics.get("prompt_version", "writing-v3"),
                    "schema_version": 2,
                    "context_id": (result.get("quality_context") or {}).get("context_id"),
                }
                run.verdict = "passed" if all(report["checks"].values()) else "failed"
                j.result_ref = {"validation_run_id": run.id}
            elif j.kind == "voice_enrollment":
                v = db.get(m.ProbeVoice, result["voice_id"])
                v.provider_voice_id = result["provider_voice_id"]
                v.status = "awaiting_preview" if v.status == "cloning" else "remote_deletion_pending"
                v.revision += 1
                run.output, run.verdict = {"probe_voice_id": v.id}, "passed"
                j.result_ref = {"validation_run_id": run.id, "probe_voice_id": v.id}
            elif j.kind in ("voice_preview", "voice_reuse"):
                v = owned(db, m.ProbeVoice, j.input_json["probe_voice_id"], owner, mode)
                if v.status not in ("awaiting_preview", "ready"):
                    j.status, j.phase = "cancelled", "voice_disabled_before_adoption"
                    run.verdict = "blocked"
                    db.commit()
                    self.checkpoint_path(id).unlink(missing_ok=True)
                    return
                audio = base64.b64decode(result["audio_base64"], validate=True)
                if mode == "live" and metrics.get("model") == "cosyvoice-v3.5-flash":
                    audio = normalize_cosyvoice_wav(audio)
                a = save_asset(db, self.s, owner, mode, audio, "validation_audio")
                run.output = {
                    "asset_id": a.id,
                    "duration_ms": a.duration_ms,
                    "probe_voice_id": v.id,
                    "voice_fingerprint": result["voice_fingerprint"],
                    "text_hash": digest(j.input_json["text"]),
                    "metrics": metrics,
                    "is_test_tone": mode == "mock",
                }
                run.verdict = "passed"
                j.result_ref = {"validation_run_id": run.id, "asset_id": a.id}
            elif j.kind == "voice_delete":
                v = owned(db, m.ProbeVoice, j.input_json["probe_voice_id"], owner, mode)
                v.status = "deleted" if result["remote_deleted"] else "remote_deletion_pending"
                if result["remote_deleted"]:
                    v.provider_voice_id = None
                v.revision += 1
                sample = db.get(m.Asset, v.sample_asset_id)
                dependent_jobs = db.scalars(
                    select(m.Job).where(
                        m.Job.id != j.id, m.Job.status.in_(["queued", "running", "needs_reconciliation"])
                    )
                )
                another_voice = db.scalar(
                    select(m.ProbeVoice).where(
                        m.ProbeVoice.id != v.id,
                        m.ProbeVoice.sample_asset_id == sample.id,
                        m.ProbeVoice.status.in_(["cloning", "needs_reconciliation"]),
                    )
                )
                held = bool(another_voice) or any(
                    other.input_json.get("sample_asset_id") == sample.id for other in dependent_jobs
                )
                if not held:
                    delete_asset_file(db, self.s, sample)
                    sample.source_url = None
                j.result_ref = {
                    "probe_voice_id": v.id,
                    "state": v.status,
                    "sample_cleanup": "deferred_in_use" if held else "deleted",
                }
            j.status, j.phase, j.updated_at = "succeeded", "completed", time.time()
            j.error = None
            db.add(
                m.EvidenceEvent(
                    owner_id=owner,
                    data_mode=mode,
                    event_type=j.kind + "_completed",
                    object_id=j.id,
                    details={"result_ref": j.result_ref, "elapsed_ms": result.get("elapsed_ms")},
                )
            )
        self.checkpoint_path(id).unlink(missing_ok=True)

    def fail(self, id, error):
        with transaction(self.sf) as db:
            j = db.get(m.Job, id)
            costs.stop(db, j)
            if j.status == "cancelled":
                return
            uncertain = getattr(error, "uncertain", False)
            j.status = "needs_reconciliation" if uncertain else "failed"
            j.error, j.updated_at = error_dict(error), time.time()
            run = db.scalar(select(m.ValidationRun).where(m.ValidationRun.job_id == id))
            if run:
                run.verdict = "blocked" if uncertain else "failed"
            if (
                j.kind
                in (
                    "analysis",
                    "writing",
                    "generation",
                    "discovery_score",
                    "discovery_brief",
                    "audio_generation",
                )
                and self.checkpoint_path(id).is_file()
            ):
                saved = json.loads(self.checkpoint_path(id).read_text())
                if saved.get("input_hash") == j.input_hash:
                    result = saved["result"]
                    metrics = result.get("metrics", {})
                    costs.settle(db, j, metrics)
                    attempt = db.scalar(
                        select(m.Attempt).where(m.Attempt.job_id == id).order_by(m.Attempt.attempt_no.desc())
                    )
                    if attempt:
                        attempt.dispatch_state = "finished"
                        attempt.provider_request_id = metrics.get("request_id")
                        attempt.usage = metrics.get("usage")
                        attempt.elapsed_ms = result.get("elapsed_ms")
                    j.result_ref = (
                        {"audio_response_saved": True}
                        if j.kind == "audio_generation"
                        else {
                            "text_failure": {
                                "raw_output": result.get("raw_output"),
                                "raw_text": metrics.get("raw_text"),
                                "metrics": {k: v for k, v in metrics.items() if k != "raw_text"},
                            }
                        }
                    )
            if j.kind == "discovery_search":
                batch = db.get(m.Discovery, j.input_json["discovery_id"])
                batch.payload = {**batch.payload, "status": "failed", "error": j.error}
            if j.kind == "voice_enrollment" and j.result_ref:
                v = db.get(m.ProbeVoice, j.result_ref.get("probe_voice_id"))
                if v and v.status == "cloning":
                    v.status = "needs_reconciliation" if uncertain else "failed"

    def run_once(self):
        self.recover()
        id = self.claim()
        if not id:
            return False
        stopped = threading.Event()
        heartbeat = threading.Thread(target=self.heartbeat, args=(id, stopped), daemon=True)
        heartbeat.start()
        start = time.monotonic()
        try:
            prepared = self.prepare(id)
            if prepared:
                job, inputs = prepared
                result = self.perform(job, inputs)
                result["elapsed_ms"] = (time.monotonic() - start) * 1000
                self.checkpoint(job, result)
                self.finish(id, result)
        except AppError as e:
            self.fail(id, e)
        except Exception:
            self.fail(
                id, ProviderError("JOB_RESULT_UNKNOWN", "作业结果待核对，请保留请求编号。", uncertain=True)
            )
            logging.getLogger("hexing").error("worker_failed job_id=%s", id)
        finally:
            stopped.set()
            heartbeat.join(timeout=2)
        return True

    def cleanup(self):
        with transaction(self.sf) as db:
            life.purge_due(db, self.s)
            active = list(
                db.scalars(
                    select(m.Job).where(m.Job.status.in_(["queued", "running", "needs_reconciliation"]))
                )
            )
            held = {j.input_json.get("sample_asset_id") for j in active}
            voices = list(
                db.scalars(
                    select(m.ProbeVoice).where(
                        m.ProbeVoice.status.in_(
                            ["cloning", "needs_reconciliation", "remote_deletion_pending"]
                        )
                    )
                )
            )
            held.update(v.sample_asset_id for v in voices)
            rows = db.scalars(
                select(m.Asset).where(m.Asset.state == "ready", m.Asset.cleanup_after <= time.time())
            )
            for a in rows:
                if a.id in held:
                    continue
                delete_asset_file(db, self.s, a)
                a.source_url = None
                db.add(
                    m.EvidenceEvent(
                        owner_id=a.owner_id,
                        data_mode=a.data_mode,
                        event_type="sample_cleaned",
                        object_id=a.id,
                        details={"reason": "experiment_deadline"},
                    )
                )
            referenced = {a.storage_key for a in db.scalars(select(m.Asset))}
        # Only old, unreferenced artifacts in this application's managed directory.
        root = self.s.data_dir / "private"
        if root.is_dir():
            for p in root.rglob("*"):
                if p.is_file() and not p.is_symlink() and time.time() - p.stat().st_mtime > 3600:
                    if str(p.relative_to(self.s.data_dir)) not in referenced:
                        p.unlink()


def main():
    logging.basicConfig(level=logging.WARNING)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    worker = Worker()
    last_cleanup = 0
    while not stop.is_set():
        worker.run_once()
        if time.time() - last_cleanup > 60:
            worker.cleanup()
            last_cleanup = time.time()
        stop.wait(0.5)


if __name__ == "__main__":
    main()
