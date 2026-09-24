import base64
import json

import httpx
import pytest
import respx
from pydantic import SecretStr

from backend.app.core.errors import AppError, ProviderError
from backend.app.services.providers import Providers, bounded_get


@pytest.fixture
def live_provider(env):
    settings = env.settings.model_copy(
        update={
            "provider_mode": "live",
            "live_calls_enabled": True,
            "provider_contract_version": "S1-2026-09-08",
            "qwen_api_key": SecretStr("fixture-key"),
            "voice_api_key": SecretStr("fixture-key"),
            "voice_sample_allowed_host": "hexing.oss-cn-beijing.aliyuncs.com",
        }
    )
    return Providers(settings)


@respx.mock
def test_github_redirect_missing_readme_and_rate_limit(live_provider):
    meta = {
        "id": 42,
        "full_name": "new/repo",
        "html_url": "https://github.com/new/repo",
        "description": "工具说明",
    }
    respx.get("https://api.github.com/repos/old/repo").mock(
        return_value=httpx.Response(301, headers={"location": "https://api.github.com/repositories/42"})
    )
    respx.get("https://api.github.com/repositories/42").mock(return_value=httpx.Response(200, json=meta))
    respx.get("https://api.github.com/repos/new/repo/readme").mock(return_value=httpx.Response(404))
    metadata, readme, missing = live_provider.github("https://github.com/old/repo")
    assert metadata["id"] == 42 and not readme and "UPSTREAM_404" in missing
    respx.get("https://api.github.com/repos/limited/repo").mock(
        return_value=httpx.Response(429, headers={"retry-after": "60"})
    )
    with pytest.raises(ProviderError, match="GITHUB_RATE_LIMIT") as error:
        live_provider.github("https://github.com/limited/repo")
    assert "60" in error.value.message


@respx.mock
def test_github_redirect_blocks_private_network(live_provider):
    respx.get("https://api.github.com/repos/a/b").mock(
        return_value=httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
    )
    with pytest.raises(ProviderError):
        live_provider.github("https://github.com/a/b")
    assert len(respx.calls) == 1


@respx.mock
def test_github_readme_real_contract(live_provider):
    respx.get("https://api.github.com/repos/a/b").mock(
        return_value=httpx.Response(
            200, json={"id": 1, "full_name": "a/b", "html_url": "https://github.com/a/b"}
        )
    )
    respx.get("https://api.github.com/repos/a/b/readme").mock(
        return_value=httpx.Response(
            200, json={"content": base64.b64encode(b"# Tool\nUsage").decode(), "encoding": "base64"}
        )
    )
    _, text, _ = live_provider.github("https://github.com/a/b")
    assert text.startswith("# Tool")


@respx.mock
def test_qwen_exact_model_thinking_and_no_retry(live_provider):
    route = respx.post(live_provider.s.qwen_base_url + "/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "req-test",
                "usage": {"prompt_tokens": 10},
                "choices": [{"message": {"content": '{"summary":"x"}'}}],
            },
        )
    )
    output, metrics = live_provider.text("analysis", {"fragments": []})
    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "qwen3.7-flash" and body["enable_thinking"] is False and body["stream"] is False
    assert output["summary"] == "x" and metrics["usage"]["prompt_tokens"] == 10
    route.mock(side_effect=httpx.ReadTimeout("fixture"))
    with pytest.raises(ProviderError) as error:
        live_provider.text("analysis", {"fragments": []})
    assert error.value.uncertain and route.call_count == 2


@respx.mock
def test_enrollment_and_delete_contract(live_provider):
    route = respx.post(live_provider.s.voice_enrollment_endpoint).mock(
        return_value=httpx.Response(
            200, json={"request_id": "req-test", "output": {"voice_id": "private-id"}}
        )
    )
    voice, _ = live_provider.enroll("https://hexing.oss-cn-beijing.aliyuncs.com/sample.wav?signature=test")
    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "voice-enrollment" and body["input"]["target_model"] == "cosyvoice-v3.5-flash"
    assert body["input"]["action"] == "create_voice" and voice == "private-id"
    route.mock(return_value=httpx.Response(503))
    assert live_provider.delete_voice(voice) is False


@respx.mock
def test_bounded_asset_download():
    respx.get("https://example.test/audio").mock(
        return_value=httpx.Response(302, headers={"location": "http://127.0.0.1"})
    )
    with httpx.Client() as client:
        with pytest.raises(ProviderError):
            bounded_get(client, "https://example.test/audio")
    respx.get("https://example.test/audio").mock(return_value=httpx.Response(200, content=b"x" * 101))
    with httpx.Client() as client:
        with pytest.raises(ProviderError):
            bounded_get(client, "https://example.test/audio", max_bytes=100)


def test_model_and_endpoint_guardrails(live_provider):
    for field, value in [
        ("qwen_model", "other-model"),
        ("qwen_analysis_thinking", True),
        ("qwen_base_url", "https://evil.test"),
        ("voice_synthesis_endpoint", "wss://evil.test"),
    ]:
        provider = Providers(live_provider.s.model_copy(update={field: value}))
        with pytest.raises(AppError):
            provider.check("voice_preview" if field.startswith("voice") else "analysis")


@respx.mock
def test_enrollment_free_tier_failure_is_actionable_without_leaking_inputs(live_provider):
    route = respx.post(live_provider.s.voice_enrollment_endpoint).mock(
        return_value=httpx.Response(
            403,
            json={
                "code": "AllocationQuota.FreeTierOnly",
                "message": "private-url-signature-and-api-key-must-not-appear",
            },
        )
    )
    with pytest.raises(ProviderError) as error:
        live_provider.enroll("https://hexing.oss-cn-beijing.aliyuncs.com/sample.wav?signature=test")
    assert error.value.code == "VOICE_FREE_TIER_ONLY"
    assert "百炼控制台" in error.value.message
    assert "private-url" not in error.value.message
    assert not error.value.uncertain
    assert route.call_count == 1


@respx.mock
def test_oss_denial_is_not_misattributed_to_bailian(env, monkeypatch):
    from types import SimpleNamespace

    url = "https://bucket.oss-cn-beijing.aliyuncs.com/a.wav?Signature=private-test"
    respx.get(url).mock(return_value=httpx.Response(403))

    def must_not_enroll(*args):
        pytest.fail("OSS failure must not submit voice enrollment")

    monkeypatch.setattr(env.worker.providers, "enroll", must_not_enroll)
    with pytest.raises(AppError) as error:
        env.worker.perform(SimpleNamespace(kind="voice_enrollment", data_mode="live"), {"signed_url": url})
    assert error.value.code == "SAMPLE_DOWNLOAD_FORBIDDEN"
    assert "尚未调用百炼" in error.value.message
    assert "private-test" not in error.value.message
