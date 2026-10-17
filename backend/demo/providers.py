"""Mock-mode providers that replay reviewed outputs for a few public repositories."""

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from backend.app.services.providers import Providers

METRICS = {"request_id": None, "usage": None, "model": "demo-replay", "enable_thinking": False}


@lru_cache(maxsize=1)
def fixture():
    return json.loads((Path(__file__).parent / "fixture.json").read_text())


def repository(full_name):
    for name, value in fixture()["repositories"].items():
        if name.lower() == (full_name or "").lower():
            return name, value
    return None, None


class DemoProviders(Providers):
    """Unknown repositories fall back to the ordinary mock outputs."""

    def __init__(self, settings):
        if settings.provider_mode != "mock":
            raise ValueError("Demo replay is only available in mock mode")
        super().__init__(settings)

    def mock_search_names(self, inputs):
        return list(fixture()["discovery"]["repositories"])

    def github(self, url):
        name, value = repository(url.removeprefix("https://github.com/"))
        if not value:
            return super().github(url)
        return deepcopy(value["metadata"]), value["readme"], []

    def text(self, kind, inputs):
        data = fixture()
        if kind in ("discovery_brief", "discovery_score"):
            raw, metrics = super().text(kind, inputs)
            items = raw["briefs" if kind == "discovery_brief" else "scores"]
            by_id = {c["metadata"]["id"]: c for c in inputs["candidates"]}
            for i, item in enumerate(items):
                candidate = by_id[item["repo_id"]]
                name, _ = repository(candidate["metadata"]["full_name"])
                if kind == "discovery_brief" and name in data["discovery"]["briefs"]:
                    items[i] = {"repo_id": item["repo_id"], "fields": deepcopy(data["discovery"]["briefs"][name])}
                elif kind == "discovery_score" and name in data["discovery"]["scores"]:
                    score = data["discovery"]["scores"][name]
                    first = [f["id"] for f in candidate["fragments"][:1]]
                    items[i] = {
                        **item,
                        **score,
                        "evidence": {
                            k: first
                            for k in ("relevance", "completeness", "demonstration", "accessibility")
                            if score[k]
                        },
                    }
            return raw, {**metrics, "model": METRICS["model"]}
        if kind == "analysis":
            name, _ = repository((inputs.get("metadata") or {}).get("full_name"))
            if not name:
                head = (inputs.get("fragments") or [{}])[0].get("text", "")
                name = next(
                    (n for n, v in data["repositories"].items() if head and v["readme"].startswith(head)),
                    None,
                )
            if name in data["analyses"]:
                return deepcopy(data["analyses"][name]), dict(METRICS)
        elif inputs.get("tool_name") in data["writings"]:
            return deepcopy(data["writings"][inputs["tool_name"]]), dict(METRICS)
        return super().text(kind, inputs)
