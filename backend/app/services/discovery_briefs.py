"""Short Chinese product briefs, independently validated from candidate scores."""

import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

from backend.app.services.common import digest

BRIEF_VERSION = "discovery-brief-v1"
BRIEF_FIELDS = {"purpose": "用途", "feature": "核心特点", "usage": "使用方式", "requirements": "准备条件"}


def source_hash(candidate):
    return digest(
        {
            "repository": candidate["metadata"]["full_name"],
            "description": candidate["metadata"].get("description"),
            "fragments": candidate.get("fragments", []),
        }
    )


def sources(candidate):
    result = {f["id"]: f["text"] for f in candidate.get("fragments", [])}
    result["description"] = candidate["metadata"].get("description") or ""
    return result


def validate_brief(value, candidate):
    """References validate provenance, not the semantic truth of a model's summary."""
    refs = sources(candidate)
    fields = {}
    value = value if isinstance(value, dict) else {}
    for key in BRIEF_FIELDS:
        item = value.get(key)
        text = item.get("text") if isinstance(item, dict) else None
        evidence = item.get("evidence") if isinstance(item, dict) else None
        valid = (
            isinstance(text, str)
            and 4 <= len(text.strip()) <= 72
            and re.search(r"[\u4e00-\u9fff]", text)
            and not re.search(r"```|https?://|pip install|npm install|git clone", text, re.I)
            and isinstance(evidence, list)
            and 1 <= len(evidence) <= 4
        )
        if valid:
            valid = all(
                isinstance(e, dict)
                and isinstance(e.get("ref"), str)
                and e["ref"] in refs
                and isinstance(e.get("quote"), str)
                and 4 <= len(e["quote"]) <= 500
                and e["quote"] in refs[e["ref"]]
                for e in evidence
            )
        fields[key] = (
            {"text": text.strip(), "evidence": evidence} if valid else {"text": "资料不足", "evidence": []}
        )
    return {"version": BRIEF_VERSION, "source_hash": source_hash(candidate), "fields": fields}


@lru_cache(maxsize=1)
def reviewed_repairs():
    return json.loads((Path(__file__).parent / "catalog/discovery-brief-repairs.json").read_text())


def brief_for(candidate):
    saved = candidate.get("brief") or reviewed_repairs().get(source_hash(candidate), {})
    if saved.get("source_hash") == source_hash(candidate):
        return validate_brief(saved.get("fields"), candidate)
    # No English snippets, install commands or model scores masquerading as briefs.
    return validate_brief({}, candidate)


def validate_batch(raw, candidates):
    values = raw.get("briefs", []) if isinstance(raw, dict) else []
    values = values if isinstance(values, list) else []
    counts = Counter(
        v.get("repo_id") for v in values if isinstance(v, dict) and type(v.get("repo_id")) is int
    )
    by_id = {v["repo_id"]: v for v in values if isinstance(v, dict) and type(v.get("repo_id")) is int}
    return [
        {
            **c,
            "brief": validate_brief(
                by_id.get(c["metadata"]["id"], {}).get("fields") if counts[c["metadata"]["id"]] == 1 else {},
                c,
            ),
        }
        for c in candidates
    ]
