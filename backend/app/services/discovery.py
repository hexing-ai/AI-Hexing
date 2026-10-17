"""Bounded GitHub discovery and evidence-backed, deterministic ranking."""

import json
import re
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone

import httpx

from backend.app.core.errors import ProviderError
from backend.app.services.common import normalize_repository
from backend.app.services.discovery_briefs import brief_for
from backend.app.services.providers import bounded_get


def search(providers, inputs):
    if providers.s.provider_mode == "mock":
        names = providers.mock_search_names(inputs)
    else:
        providers.check("import")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"}
        if providers.s.github_token.get_secret_value():
            headers["Authorization"] = "Bearer " + providers.s.github_token.get_secret_value()
        try:
            with httpx.Client(timeout=30, trust_env=False, follow_redirects=False) as client:
                url = httpx.URL(
                    providers.s.github_api_base_url + "/search/repositories",
                    params={"q": inputs["topic"] + " " + inputs.get("category", ""), "per_page": 8},
                )
                data = json.loads(bounded_get(client, str(url), headers))
            names = [v["full_name"] for v in data["items"][:8]]
        except (httpx.HTTPError, KeyError, ValueError):
            raise ProviderError(
                "DISCOVERY_SEARCH_FAILED", "GitHub 搜索失败，已保留查询，可稍后重试或导入链接。"
            )
    candidates, missing, seen = [], [], set()
    for name in names:
        try:
            url = normalize_repository("https://github.com/" + name)
            meta, readme, errors = providers.github(url)
            if meta.get("id") in seen:
                continue
            seen.add(meta["id"])
            text = readme or meta.get("description") or ""
            fragments = [
                {
                    "id": f"r{meta['id']}-f{i + 1}",
                    "text": text[start : start + 3000],
                    "source_url": meta["html_url"] + "#readme",
                }
                for i, start in enumerate(range(0, min(len(text), 6000), 3000))
            ]
            candidates.append(
                {
                    "metadata": meta,
                    "fragments": fragments,
                    "missing_fields": [*errors, *([] if meta.get("license") else ["license unknown"])],
                    "read_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        except ProviderError as error:
            missing.append({"repository": name, "code": error.code, "message": error.message})
    return {
        "candidates": candidates,
        "missing": missing,
        "status": "sources_ready" if candidates else "empty",
    }


RULE_VERSION = "discovery-v2"
DIMENSIONS = {
    "relevance": (3, "主题匹配"),
    "completeness": (2, "说明完整度"),
    "demonstration": (2, "演示依据"),
    "accessibility": (1, "使用门槛"),
}
FACT_PATTERNS = {
    "feature": r"功能|特性|特点|核心|features?|core logic",
    "usage": r"使用方式|部署|快速开始|使用指南|quick start|mobile dashboard",
    "requirements": r"运行环境|系统要求|依赖|requirements|prerequisites",
    "limitations": r"限制|注意事项|limitations|不支持",
}
FACT_CONTENT = {
    "feature": r"支持|提供|实现|自动|追踪|记录|tracks?|supports?|records?|readiness|coaching|training",
    "usage": r"小程序|Android|PyQt|网页|Web 界面|浏览器|命令行|Claude Code|dashboard|本地文件驱动",
    "requirements": r"需要.{0,30}(?:Python|安装|API|环境|设备)|pip install|文件读写|可读取和写入",
    "limitations": r"暂不支持|仅支持|不再更新|不支持|not supported|cannot |can't ",
}


def source_facts(candidate):
    """Conservative literal excerpts. Prefer named sections, skip headings/TOCs.

    No network/model call. Missing facts stay missing; snippets are source claims,
    not proof that the software works or that setup instructions are complete.
    """
    options = []
    for fragment in candidate.get("fragments", []):
        heading = ""
        for block in re.split(r"\n\s*\n", fragment["text"]):
            lines = block.strip().splitlines()
            for line in lines:
                if line.strip().startswith("#"):
                    heading = line.strip().lstrip("# ")
            if any(line.strip().startswith("```") for line in lines):
                # Only quote dependency commands under an explicit requirements heading.
                if re.search(FACT_PATTERNS["requirements"], heading, re.I):
                    commands = [line.strip() for line in lines if line.strip().startswith("pip install ")]
                    if commands:
                        options.append(("requirements", 1, commands[0], fragment))
                continue
            lines = [
                line
                for line in lines
                if not line.strip().startswith(("![", "<", "#"))
                and not re.fullmatch(r"\s*[-*_]{3,}\s*", line)
                and not re.fullmatch(r"\s*\*\*[^*]+\*\*[:：]?\s*", line)
            ]
            # Keep prose paragraphs together so wrapped English isn't turned into half a sentence.
            chunks = (
                lines
                if any(re.match(r"\s*(?:[-*] |\d+[.)] |\|)", line) for line in lines)
                else ["\n".join(lines)]
            )
            for chunk in chunks:
                quote = re.sub(r"^\s*(?:[-*] |\d+[.)] |> )", "", chunk).strip()
                if not quote or quote.startswith(("#", "![", "[!", "<", "```")):
                    continue
                # A standalone link, heading, or table separator is not a product fact.
                if re.fullmatch(r"\[.*?\]\([^\n]+\)|[*_\s\-|:]+", quote):
                    continue
                if re.fullmatch(r"\*\*[^\n]+\*\*[:：]?", quote) or quote.endswith((":", "：")):
                    continue
                if quote.startswith("|") and ("---" in quote or "最低要求" in quote or "推荐配置" in quote):
                    continue
                # Use the first complete sentence when a paragraph is too long.
                if len(quote) > 600:
                    sentence = re.match(r"[\s\S]*?[。！？]|[\s\S]*?[.!?](?:\s|$)", quote)
                    quote = sentence.group().strip() if sentence else ""
                if not 8 <= len(quote) <= 600 or re.match(r"^[a-z]", quote) or quote not in fragment["text"]:
                    continue
                for key, pattern in FACT_PATTERNS.items():
                    in_section = bool(re.search(pattern, heading, re.I))
                    has_content = bool(re.search(FACT_CONTENT[key], quote, re.I))
                    if (in_section or has_content) and (
                        not quote.startswith("|") or key == "requirements" and in_section
                    ):
                        options.append(
                            (key, (2 if in_section else 0) + (3 if has_content else 0), quote, fragment)
                        )
    facts, used = {}, set()
    for key in FACT_PATTERNS:
        choices = sorted((v for v in options if v[0] == key), key=lambda v: -v[1])
        for _, _, quote, fragment in choices:
            if quote in used:
                continue
            facts[key] = {
                "text": quote,
                "refs": [fragment["id"]],
                "source_url": fragment.get("source_url"),
                "kind": "source_quote",
            }
            used.add(quote)
            break
    return facts


def rank(raw, candidates, now=None):
    now = now or datetime.now(timezone.utc)
    by_id = {c["metadata"]["id"]: c for c in candidates}
    values = raw.get("scores", []) if isinstance(raw, dict) else []
    if not isinstance(values, list):
        values = []
    counts = Counter(
        v.get("repo_id") for v in values if isinstance(v, dict) and type(v.get("repo_id")) is int
    )
    by_score = {v["repo_id"]: v for v in values if isinstance(v, dict) and type(v.get("repo_id")) is int}
    evaluated, rejected = [], []
    for rid in counts.keys() - by_id.keys():
        rejected.append(
            {
                "repo_id": rid,
                "code": "UNKNOWN_REPOSITORY",
                "message": "返回的仓库编号不属于本次候选，未采用。",
            }
        )
    for rid, candidate in by_id.items():
        name = candidate["metadata"]["full_name"]
        item = {**candidate, "facts": source_facts(candidate)}
        issues = []

        def issue(code, message, field=None):
            issues.append(
                {"repo_id": rid, "repository": name, "code": code, "field": field, "message": message}
            )

        value = by_score.get(rid)
        refs = {f["id"]: f for f in candidate.get("fragments", [])}
        if value is None:
            issue("SCORE_MISSING", "没有收到可对应到此项目的评分。")
        elif counts[rid] != 1:
            issue("DUPLICATE_REPOSITORY", "同一项目返回了多份评分，未采用冲突结果。")
        else:
            evidence = value.get("evidence")
            if not isinstance(evidence, dict) or any(
                not isinstance(v, list) or any(not isinstance(ref, str) or ref not in refs for ref in v)
                for v in evidence.values()
            ):
                issue("INVALID_REFERENCE", "来源引用无效或属于其他项目，本项目评分未采用。")
            else:
                effective, verified = {}, {}
                for key, (maximum, label) in DIMENSIONS.items():
                    number = value.get(key)
                    if type(number) is not int or not 0 <= number <= maximum:
                        effective[key] = None
                        issue("INVALID_DIMENSION", label + "分值格式或范围有误，未计分。", key)
                    elif number > 0 and not evidence.get(key):
                        effective[key] = None
                        issue("MISSING_EVIDENCE", label + "缺少来源依据，未计分。", key)
                    else:
                        effective[key] = number
                        verified[key] = evidence.get(key, [])
                stopped = value.get("stopped")
                excluded = value.get("excluded")
                if type(excluded) is not bool:
                    issue("INVALID_EXCLUSION", "候选类型判断格式有误，需核对。", "excluded")
                maintenance = None
                if type(stopped) is not bool or (stopped and not evidence.get("stopped")):
                    issue("STOPPED_UNVERIFIED", "停止维护判断缺少有效依据，维护项未计分。", "maintenance")
                elif stopped:
                    maintenance = 0
                    verified["stopped"] = evidence["stopped"]
                else:
                    try:
                        updated = datetime.fromisoformat(
                            candidate["metadata"]["updated_at"].replace("Z", "+00:00")
                        )
                        maintenance = 2 if 0 <= (now - updated).total_seconds() <= 365 * 86400 else 1
                    except (ValueError, TypeError, KeyError):
                        issue("MAINTENANCE_UNKNOWN", "更新时间未知，维护项未计分。", "maintenance")
                # Only restore prose when its corresponding scoring evidence is available.
                reason = value.get("reason") if verified.get("relevance") else None
                barrier = value.get("barrier") if verified.get("accessibility") else None
                if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1500:
                    if verified.get("relevance"):
                        issue("EXPLANATION_MISSING", "选题判断缺少有效说明，需核对。", "reason")
                    reason = None
                if not isinstance(barrier, str) or not 1 <= len(barrier.strip()) <= 1000:
                    if verified.get("accessibility"):
                        issue("EXPLANATION_MISSING", "使用条件缺少有效说明，需核对。", "barrier")
                    barrier = None
                # Future structured facts must carry literal, locally verifiable quotations.
                model_facts = value.get("facts")
                for key, fact in model_facts.items() if isinstance(model_facts, dict) else []:
                    if key not in {*FACT_PATTERNS, "angle"}:
                        continue
                    quote = fact.get("quote") if isinstance(fact, dict) else None
                    ids = fact.get("refs") if isinstance(fact, dict) else None
                    if (
                        isinstance(quote, str)
                        and 1 <= len(quote) <= 240
                        and isinstance(ids, list)
                        and ids
                        and all(
                            isinstance(ref, str) and ref in refs and quote in refs[ref]["text"] for ref in ids
                        )
                    ):
                        item["facts"][key] = {
                            "text": quote,
                            "refs": ids,
                            "source_url": refs[ids[0]].get("source_url"),
                            "kind": "source_quote",
                        }
                    else:
                        issue("FACT_UNVERIFIED", "产品信息的原文依据无法核对，已保留来源摘录。", key)
                total = sum(v for v in [*effective.values(), maintenance] if v is not None)
                item["score"] = {
                    **effective,
                    "maintenance": maintenance,
                    "total": total,
                    "reason": reason,
                    "barrier": barrier,
                    "evidence": verified,
                    "partial": any(v is None for v in [*effective.values(), maintenance]),
                }
                item["maintenance_reason"] = (
                    "停止维护声明"
                    if stopped is True and maintenance == 0
                    else "一年内更新"
                    if maintenance == 2
                    else "一年未更新"
                    if maintenance == 1
                    else "更新时间或停止声明待核对"
                )
                meta = candidate["metadata"]
                exclusions = []
                if meta.get("archived"):
                    exclusions.append("仓库已归档")
                if meta.get("fork"):
                    exclusions.append("属于 fork 仓库")
                if excluded is True:
                    exclusions.append("模型判断为课程、资料清单或不适用类型，需结合原文复核")
                if effective["relevance"] is not None and effective["relevance"] < 2:
                    exclusions.append("主题匹配不足")
                if not issues and total < 6:
                    exclusions.append("资料评估分未达到 6 分")
                item["disposition"] = (
                    "not_recommended" if exclusions else "pending" if issues else "recommended"
                )
                item["decision_reasons"] = exclusions
        if "disposition" not in item:
            item["disposition"] = "pending"
        item["evaluation_issues"] = issues
        item["evaluation"] = "仅资料评估，未验证实际使用效果"
        evaluated.append(item)
        rejected.extend(issues)
        if item["disposition"] == "not_recommended":
            rejected.append(
                {
                    "repo_id": rid,
                    "repository": name,
                    "code": "BELOW_THRESHOLD",
                    "message": "；".join(item["decision_reasons"]),
                }
            )
    eligible = sorted(
        (c for c in evaluated if c["disposition"] == "recommended"),
        key=lambda c: (-c["score"]["total"], -c["score"]["relevance"], c["metadata"]["full_name"]),
    )
    for item in eligible[5:]:
        item["disposition"] = "not_recommended"
        item["decision_reasons"] = ["资料评分有效，本轮优先推荐前 5 项"]
    summary = {
        key: sum(c["disposition"] == key for c in evaluated)
        for key in ("recommended", "pending", "not_recommended")
    }
    return {
        "recommendations": eligible[:5],
        "evaluated_candidates": evaluated,
        "rejected": rejected,
        "summary": summary,
        "rule_version": RULE_VERSION,
        "evaluated_at": now.isoformat(),
        "note": "保留全部候选；缺失依据的分项不计分，不自动重试模型。",
    }


def discovery_payload(payload, created_at):
    """Read-only projection: replay saved outputs, keeping historical bytes intact."""
    output = deepcopy(payload)
    candidates = output.get("candidates", [])
    output["candidates"] = [{**c, "facts": source_facts(c), "brief": brief_for(c)} for c in candidates]
    for run in output.get("score_runs", []):
        if run.get("rule_version") != RULE_VERSION:
            old_version = run.get("rule_version", "discovery-v1")
            try:
                now = datetime.fromisoformat(run["evaluated_at"])
            except (KeyError, TypeError, ValueError):
                now = datetime.fromtimestamp(created_at, timezone.utc)
            run.update(rank(run.get("raw_output"), candidates, now=now))
            run["revalidated_from"] = old_version
    briefs = {c["metadata"]["id"]: c["brief"] for c in output["candidates"]}
    for run in output.get("score_runs", []):
        for key in ("recommendations", "evaluated_candidates"):
            run[key] = [
                {**c, "brief": briefs.get(c["metadata"]["id"], brief_for(c))} for c in run.get(key, [])
            ]
    covered = sum(any(f["evidence"] for f in c["brief"]["fields"].values()) for c in output["candidates"])
    output["brief_status"] = (
        "ready"
        if covered == len(candidates) and covered
        else "incomplete"
        if output.get("brief_runs")
        else "missing"
    )
    return output
