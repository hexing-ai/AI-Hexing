"""Official service adapters; mock is explicit and never a fallback for live."""

import base64
import hashlib
import io
import json
import math
import struct
import wave
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from backend.app.core.errors import AppError, ProviderError
from backend.app.schemas import parse_json
from backend.app.services.common import digest

PROMPTS = Path(__file__).parent / "prompts"


def enrollment_request(settings, signed_url):
    """The same create-only payload is used for pricing and dispatch."""
    endpoint = urlsplit(settings.voice_enrollment_endpoint)
    if (
        settings.voice_region != "beijing"
        or settings.voice_target_model != "cosyvoice-v3.5-flash"
        or endpoint.scheme != "https"
        or endpoint.username
        or endpoint.password
        or endpoint.netloc != endpoint.hostname
        or endpoint.query
        or endpoint.fragment
        or endpoint.path != "/api/v1/services/audio/tts/customization"
        or not (
            endpoint.hostname == "dashscope.aliyuncs.com"
            or (endpoint.hostname or "").endswith(".cn-beijing.maas.aliyuncs.com")
        )
    ):
        raise AppError("PRICE_UNVERIFIED", "该声音复刻型号、地域或端点尚未核实计价。")
    return {
        "model": "voice-enrollment",
        "input": {
            "action": "create_voice",
            "target_model": settings.voice_target_model,
            "prefix": "hexing",
            "url": signed_url,
        },
    }


def bounded_get(client, url, headers=None, max_bytes=2_000_000):
    with client.stream("GET", url, headers=headers) as r:
        if r.is_redirect:
            raise ProviderError("UPSTREAM_REDIRECT", "服务地址发生跳转，请核对官方地址。")
        if r.status_code != 200:
            message = (
                "仓库不存在、私有或暂时不可访问。"
                if r.status_code == 404
                else "服务拒绝请求，请检查权限或额度。"
            )
            wait = r.headers.get("retry-after") or r.headers.get("x-ratelimit-reset")
            if r.status_code in (403, 429) and wait:
                message = "服务限流，请按返回时间重试：" + wait[:80]
            raise ProviderError("UPSTREAM_" + str(r.status_code), message)
        body = bytearray()
        for chunk in r.iter_bytes():
            body.extend(chunk)
            if len(body) > max_bytes:
                raise ProviderError("UPSTREAM_TOO_LARGE", "来源超过读取上限，请补充必要资料。")
        return bytes(body)


def wav_tone(seconds=2.0):
    """Audible test tone, intentionally not speech or a cloned voice."""
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        frames = b"".join(
            struct.pack("<h", int(1500 * math.sin(i * 2 * math.pi * 440 / 24000)))
            for i in range(int(seconds * 24000))
        )
        w.writeframes(frames)
    return out.getvalue()


def text_request(settings, kind, inputs):
    prompt_file = {
        "analysis": "analysis-v1.txt",
        "discovery_score": "discovery-v2.txt",
        "discovery_brief": "discovery-brief-v1.txt",
    }.get(kind, "writing-v3.txt")
    return {
        "model": settings.qwen_model,
        "messages": [
            {"role": "system", "content": (PROMPTS / prompt_file).read_text()},
            {"role": "user", "content": json.dumps(inputs, ensure_ascii=False)},
        ],
        "enable_thinking": False
        if kind in ("analysis", "discovery_score", "discovery_brief")
        else settings.qwen_writing_thinking,
        "stream": False,
        "max_tokens": 8000 if kind == "discovery_brief" else 4000,
    }


class Providers:
    def __init__(self, settings):
        self.s = settings

    def scope(self):
        return {
            "region": self.s.voice_region,
            "model": self.s.voice_target_model,
            "credential_fingerprint": digest(self.s.voice_api_key.get_secret_value())
            if self.s.provider_mode == "live"
            else "mock",
        }

    def readiness(self, kind):
        if self.s.provider_mode == "mock":
            return []
        missing = []
        if not self.s.live_calls_enabled:
            missing.append("LIVE_CALLS_ENABLED")
        if kind == "import":
            return missing
        if self.s.provider_contract_version != "S1-2026-09-08":
            missing.append("PROVIDER_CONTRACT_VERSION=S1-2026-09-08")
        if kind in ("analysis", "writing", "generation", "discovery_score", "discovery_brief"):
            if not self.s.qwen_api_key.get_secret_value():
                missing.append("QWEN_API_KEY")
            if self.s.qwen_model != "qwen3.7-flash":
                missing.append("QWEN_MODEL=qwen3.7-flash")
            if self.s.qwen_region != "beijing":
                missing.append("QWEN_REGION=beijing")
            if self.s.qwen_analysis_thinking:
                missing.append("QWEN_ANALYSIS_THINKING=false")
            host = urlsplit(self.s.qwen_base_url).hostname or ""
            if not (
                self.s.qwen_base_url.startswith("https://")
                and (host == "dashscope.aliyuncs.com" or host.endswith(".cn-beijing.maas.aliyuncs.com"))
            ):
                missing.append("QWEN_BASE_URL=official-beijing-endpoint")
        else:
            if not self.s.voice_api_key.get_secret_value():
                missing.append("VOICE_API_KEY")
            if self.s.voice_region != "beijing" or self.s.voice_target_model != "cosyvoice-v3.5-flash":
                missing.append("VOICE_REGION/MODEL")
            if kind == "voice_enrollment" and not self.s.voice_sample_allowed_host:
                missing.append("VOICE_SAMPLE_ALLOWED_HOST")
            for endpoint, scheme in [
                (self.s.voice_enrollment_endpoint, "https"),
                (self.s.voice_synthesis_endpoint, "wss"),
            ]:
                parsed = urlsplit(endpoint)
                if (
                    parsed.scheme != scheme
                    or parsed.username
                    or parsed.password
                    or parsed.port
                    or not (
                        parsed.hostname == "dashscope.aliyuncs.com"
                        or (parsed.hostname or "").endswith(".cn-beijing.maas.aliyuncs.com")
                    )
                ):
                    missing.append("VOICE_ENDPOINT=official-beijing-endpoint")
        return missing

    def check(self, kind):
        if self.readiness(kind):
            raise AppError("CAPABILITY_UNAVAILABLE", "真实服务尚未配置完成，请查看接入状态。", 503)

    def github(self, url):
        full_name = url.removeprefix("https://github.com/")
        if self.s.provider_mode == "mock":
            return (
                {
                    "id": int(digest(full_name)[:12], 16),
                    "full_name": full_name,
                    "html_url": url,
                    "description": "模拟工具：将 Markdown 转为可预览页面。",
                    "archived": False,
                    "fork": False,
                    "license": None,
                    "stargazers_count": 0,
                    "updated_at": "2026-09-08T00:00:00Z",
                },
                "# 模拟仓库\n本工具支持把 Markdown 转为可预览页面。\n使用命令导入 Markdown 文件后预览。\n运行需要 Node.js。",
                [],
            )
        self.check("import")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"}
        if self.s.github_token.get_secret_value():
            headers["Authorization"] = "Bearer " + self.s.github_token.get_secret_value()
        try:
            with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as c:
                target = self.s.github_api_base_url + "/repos/" + full_name
                # Renamed repos redirect only within GitHub's fixed REST origin.
                for _ in range(3):
                    r = c.get(target, headers=headers)
                    if r.is_redirect:
                        next_url = r.headers.get("location", "")
                        parsed = urlsplit(next_url)
                        if (
                            parsed.scheme != "https"
                            or parsed.netloc != "api.github.com"
                            or not parsed.path.startswith(("/repos/", "/repositories/"))
                        ):
                            raise ProviderError("UPSTREAM_REDIRECT", "仓库重定向地址不可信。")
                        target = next_url
                        continue
                    if r.status_code != 200:
                        if r.status_code in (403, 429):
                            raise ProviderError(
                                "GITHUB_RATE_LIMIT",
                                "GitHub 暂时拒绝请求；重试时间："
                                + (
                                    r.headers.get("retry-after")
                                    or r.headers.get("x-ratelimit-reset")
                                    or "未提供"
                                )[:80],
                            )
                        raise ProviderError("GITHUB_UNAVAILABLE", "仓库不存在、私有或暂时不可访问。")
                    break
                else:
                    raise ProviderError("UPSTREAM_REDIRECT", "仓库跳转次数过多。")
                meta = r.json()
                full_name = meta["full_name"]
                readme = ""
                missing = []
                try:
                    readme_data = json.loads(
                        bounded_get(
                            c, self.s.github_api_base_url + "/repos/" + full_name + "/readme", headers
                        )
                    )
                    readme = base64.b64decode(readme_data["content"]).decode("utf-8", errors="replace")
                except ProviderError as e:
                    missing.append(e.code)
                selected = {
                    key: meta.get(key)
                    for key in [
                        "id",
                        "full_name",
                        "html_url",
                        "description",
                        "archived",
                        "fork",
                        "license",
                        "stargazers_count",
                        "updated_at",
                    ]
                }
                return selected, readme, missing
        except (httpx.HTTPError, KeyError, ValueError):
            raise ProviderError("GITHUB_READ_FAILED", "GitHub 读取失败，已保留原作业。")

    def text(self, kind, inputs):
        if self.s.provider_mode == "mock":
            if kind == "discovery_brief":
                return {
                    "briefs": [
                        {
                            "repo_id": c["metadata"]["id"],
                            "fields": {
                                "purpose": {
                                    "text": "模拟：根据仓库资料整理工具用途。",
                                    "evidence": [
                                        {"ref": "description", "quote": c["metadata"].get("description", "")}
                                    ],
                                },
                                "feature": None,
                                "usage": None,
                                "requirements": None,
                            },
                        }
                        for c in inputs["candidates"]
                    ]
                }, {"model": "mock", "enable_thinking": False, "prompt_version": "discovery-brief-v1"}
            if kind == "discovery_score":
                return {
                    "scores": [
                        {
                            "repo_id": c["metadata"]["id"],
                            "relevance": 3,
                            "completeness": 2,
                            "demonstration": 2,
                            "accessibility": 1,
                            "evidence": {
                                k: [f["id"] for f in c["fragments"][:1]]
                                for k in ("relevance", "completeness", "demonstration", "accessibility")
                            },
                            "reason": "模拟：资料介绍了可演示的工具功能",
                            "barrier": "Node.js",
                            "excluded": False,
                            "stopped": False,
                        }
                        for c in inputs["candidates"]
                    ]
                }, {"model": "mock", "enable_thinking": False}
            refs = [f["id"] for f in inputs["fragments"][:1]]
            if kind == "analysis":
                result = {
                    "summary": "模拟分析：Markdown 页面预览工具。",
                    "facts": [
                        {
                            "claim": "支持 Markdown 页面预览",
                            "evidence_refs": refs,
                            "source_type": "repo_statement",
                        }
                    ]
                    if refs
                    else [],
                    "highlights": [
                        {
                            "id": "h1",
                            "capability": "Markdown 页面预览",
                            "benefit": "检查文档展示效果",
                            "demo": "导入 Markdown 文件后预览",
                            "conditions": "需要 Node.js",
                            "evidence_refs": refs,
                        }
                    ]
                    if refs
                    else [],
                    "unknowns": ["价格未知/待核实", "许可证未知/待核实"],
                    "warnings": ["模拟结果，不是真实模型分析。"],
                }
            else:
                result = {
                    "script": {
                        "pain": "文档写完，想先看看展示效果？",
                        "intro": f"每天分享好用的工具，今天讲{inputs['tool_name']}。",
                        "feature": "这个工具支持 Markdown 页面预览，需要 Node.js 环境。",
                        "example": "导入一份 Markdown 文件，就可以检查页面显示效果。",
                    },
                    "copy": {
                        "body": "从 Markdown 文件查看页面预览效果。",
                        "tags": ["AI工具", "Markdown"],
                        "title": "文档预览",
                    },
                    "evidence_map": {"feature": refs, "example": refs, "copy": refs},
                }
            return result, {"request_id": None, "usage": None, "model": "mock", "enable_thinking": False}
        self.check(kind)
        prompt_file = {
            "analysis": "analysis-v1.txt",
            "discovery_score": "discovery-v2.txt",
            "discovery_brief": "discovery-brief-v1.txt",
        }.get(kind, "writing-v3.txt")
        thinking = (
            False
            if kind in ("analysis", "discovery_score", "discovery_brief")
            else self.s.qwen_writing_thinking
        )
        try:
            with httpx.Client(
                timeout=self.s.external_call_timeout_seconds, follow_redirects=False, trust_env=False
            ) as c:
                r = c.post(
                    self.s.qwen_base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + self.s.qwen_api_key.get_secret_value()},
                    json=text_request(self.s, kind, inputs),
                )
            if r.status_code != 200:
                raise ProviderError(
                    "QWEN_REJECTED",
                    f"文本服务返回状态 {r.status_code}，请核对账号权限、型号和额度。",
                    uncertain=r.status_code >= 500,
                )
            data = r.json()
            raw_text = data["choices"][0]["message"]["content"]
            metrics = {
                "request_id": data.get("id"),
                "usage": data.get("usage"),
                "model": self.s.qwen_model,
                "enable_thinking": thinking,
                "prompt_version": prompt_file.removesuffix(".txt"),
                "schema_version": 2 if kind != "analysis" else 1,
                "raw_text": raw_text,
            }
            try:
                parsed = parse_json(raw_text) if isinstance(raw_text, str) else None
            except AppError:
                parsed = None
            return parsed, metrics
        except (httpx.HTTPError, KeyError, ValueError):
            raise ProviderError("QWEN_RESULT_UNKNOWN", "文本调用结果待核对，不能自动重试。", uncertain=True)

    def enroll(self, signed_url):
        if self.s.provider_mode == "mock":
            return "mock-" + hashlib.sha256(signed_url.encode()).hexdigest()[:24], {"request_id": None}
        self.check("voice_enrollment")
        try:
            with httpx.Client(
                timeout=self.s.external_call_timeout_seconds, follow_redirects=False, trust_env=False
            ) as c:
                r = c.post(
                    self.s.voice_enrollment_endpoint,
                    headers={"Authorization": "Bearer " + self.s.voice_api_key.get_secret_value()},
                    json=enrollment_request(self.s, signed_url),
                )
            if r.status_code != 200:
                # Use only a known error code; upstream messages may contain
                # request input, signed sample URLs, or other private data.
                try:
                    rejection = r.json()
                except ValueError:
                    rejection = {}
                if (
                    r.status_code == 403
                    and isinstance(rejection, dict)
                    and rejection.get("code") == "AllocationQuota.FreeTierOnly"
                ):
                    raise ProviderError(
                        "VOICE_FREE_TIER_ONLY",
                        "百炼免费额度限制阻止了声音复刻（AllocationQuota.FreeTierOnly）。"
                        "请到百炼控制台检查免费额度与免费额度用完即停用设置；"
                        "确认价格后再决定是否开启付费使用。处理前请勿重复创建音色。",
                    )
                raise ProviderError(
                    "VOICE_REJECTED",
                    f"声音复刻返回状态 {r.status_code}，请核对样本与权限。",
                    uncertain=r.status_code >= 500,
                )
            data = r.json()
            if not isinstance(data, dict) or not isinstance(data.get("output"), dict):
                raise ValueError("invalid enrollment response")
            voice_id = data["output"]["voice_id"]
            if not isinstance(voice_id, str) or not voice_id.strip():
                raise ValueError("missing voice id")
            return voice_id, {
                "request_id": data.get("request_id"),
                "model": self.s.voice_target_model,
                # Local success evidence, not fabricated provider usage or an invoice.
                "enrollment_completed": True,
            }
        except (httpx.HTTPError, ValueError, KeyError):
            raise ProviderError("VOICE_RESULT_UNKNOWN", "音色创建结果待核对，请勿重复克隆。", uncertain=True)

    def synthesize(self, voice_id, text, *, formal=False):
        if self.s.provider_mode == "mock":
            return wav_tone(32 if formal else 2), {"request_id": None, "model": "mock-tone"}
        self.check("voice_preview")
        try:
            import dashscope
            from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

            dashscope.api_key = self.s.voice_api_key.get_secret_value()
            synth = SpeechSynthesizer(
                model=self.s.voice_target_model,
                voice=voice_id,
                format=AudioFormat.WAV_24000HZ_MONO_16BIT,
                url=self.s.voice_synthesis_endpoint,
            )
            audio = synth.call(text, timeout_millis=self.s.external_call_timeout_seconds * 1000)
            if not audio:
                raise ValueError("empty audio")
            # Usage is optional; never discard a valid WAV if SDK metrics are unavailable.
            usage = None
            try:
                response = synth.get_response()
                if isinstance(response, str):
                    response = json.loads(response)
                usage = response.get("payload", {}).get("usage") if isinstance(response, dict) else None
                if not isinstance(usage, dict):
                    usage = None
            except Exception:
                pass
            return audio, {
                "request_id": synth.get_last_request_id(),
                "model": self.s.voice_target_model,
                "usage": usage,
            }
        except Exception:
            raise ProviderError(
                "SYNTHESIS_RESULT_UNKNOWN", "语音合成结果待核对，请检查原作业。", uncertain=True
            )

    def delete_voice(self, voice_id):
        if self.s.provider_mode == "mock":
            return True
        if self.readiness("voice_preview"):
            return False
        try:
            with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as c:
                r = c.post(
                    self.s.voice_enrollment_endpoint,
                    headers={"Authorization": "Bearer " + self.s.voice_api_key.get_secret_value()},
                    json={
                        "model": "voice-enrollment",
                        "input": {"action": "delete_voice", "voice_id": voice_id},
                    },
                )
            return r.status_code == 200 and not r.json().get("code")
        except (httpx.HTTPError, ValueError):
            return False
