"""Deterministic checks are guardrails, never a substitute for source review."""

import json
import re

from pydantic import ValidationError

from backend.app.schemas import Copy, Script


def inspect_writing(raw, tool_name, fragments, context=None):
    checks = dict(script=False, copy=False, evidence=False, claims=True)
    output, issues = {}, []

    def issue(path, code, message, severity="error"):
        issues.append(dict(path=path, code=code, message=message, severity=severity))

    if not isinstance(raw, dict):
        issue("$", "JSON_OBJECT_REQUIRED", "返回内容不是有效 JSON 对象，请查看原文并手工整理。")
        return dict(content=output, checks=checks, issues=issues, human_review_required=True)
    for key in raw.keys() - {"script", "copy", "evidence_map"}:
        issue("$." + str(key), "EXTRA_FIELD", "包含模板外字段，请删除。")
        checks["claims"] = False
    for key, model in [("script", Script), ("copy", Copy)]:
        try:
            value = model.model_validate(raw.get(key)).model_dump()
            output[key] = value
            checks[key] = True
            if key == "script":
                if value["intro"] != f"每天分享好用的工具，今天讲{tool_name}。":
                    issue("script.intro", "FIXED_INTRO", "栏目句须含实际工具名并与固定模板完全一致。")
                    checks[key] = False
                if any(word in "".join(value.values()) for word in ("关注我", "封神榜", "【", "】")):
                    issue("script", "TEMPLATE_CONTENT", "请删除收尾引导、占位符或榜单指引。")
                    checks[key] = False
            else:
                value["tags"] = list(
                    dict.fromkeys(t.strip().lstrip("#") for t in value["tags"] if t.strip().lstrip("#"))
                )
                if not value["tags"]:
                    checks[key] = False
                    issue("copy.tags", "EMPTY_TAGS", "请填写至少一个非空标签。")
        except ValidationError as error:
            for e in error.errors(include_input=False, include_url=False):
                issue(
                    ".".join([key, *map(str, e["loc"])]),
                    e["type"],
                    "字段缺失、类型或长度不符合要求，请对照四段口播和文案格式修改。",
                )
    test_refs = {
        n["id"] for n in (context or {}).get("creator_tests", []) if n.get("source_type") == "creator_test"
    }
    refs = {f["id"] for f in fragments} | test_refs
    mapping = raw.get("evidence_map")
    checks["evidence"] = True
    for key in ("feature", "example", "copy"):
        values = mapping.get(key) if isinstance(mapping, dict) else None
        if not (
            isinstance(values, list) and values and all(isinstance(v, str) and v in refs for v in values)
        ):
            checks["evidence"] = False
            issue(f"evidence_map.{key}", "EVIDENCE_INVALID", "须填写存在的来源片段编号列表，不能留空。")
    if isinstance(mapping, dict):
        output["evidence_map"] = mapping
    # Conservative lexical blockers; paraphrases still require explicit human review.
    if context is not None:
        license_info = (context.get("metadata") or {}).get("license")
        unknown_license = not license_info or (
            isinstance(license_info, dict) and license_info.get("spdx_id") in (None, "NOASSERTION")
        )
        for key in ("script", "copy"):
            text = json.dumps(raw.get(key), ensure_ascii=False)
            rules = [
                (r"免费|商用|商业使用", "PRICE_LICENSE_CLAIM", "价格或商用授权未独立核实，请删去承诺。"),
                (r"封神榜|实时热门", "UNSUPPORTED_PROMOTION", "请删除未核实的榜单或热门标签指引。"),
            ]
            if unknown_license:
                rules.append(
                    (r"开源|open[ -]?source", "LICENSE_UNKNOWN", "许可证未知，不能断言开源；可写源码可查看。")
                )
            for pattern, code, message in rules:
                if re.search(pattern, text, re.I):
                    issue(key, code, message)
                    checks["claims"] = False
            parts = raw.get(key) if isinstance(raw.get(key), dict) else {}
            for section, value in parts.items():
                if not re.search(r"我(?:亲测|测试了|试用过)|亲测有效", str(value)):
                    continue
                ref_key = section if key == "script" else "copy"
                values = mapping.get(ref_key, []) if isinstance(mapping, dict) else []
                if ref_key not in ("feature", "example", "copy") or not (
                    isinstance(values, list) and any(isinstance(v, str) and v in test_refs for v in values)
                ):
                    issue(
                        key + "." + section,
                        "UNVERIFIED_TEST",
                        "亲测描述须引用对应的本人实测记录；没有记录请改为资料介绍。",
                    )
                    checks["claims"] = False
            if key == "script" and checks["script"]:
                for h in context.get("highlights") or []:
                    condition = (h.get("conditions") or "").strip()
                    if condition and condition not in "".join(output[key].values()):
                        issue("script", "CONDITION_REVIEW", "请核对是否保留必要条件：" + condition, "warning")
        issue(
            "$",
            "SOURCE_REVIEW",
            "引用编号有效不代表事实成立；确认前须逐句核对痛点、所选亮点、示例及适用条件。",
            "warning",
        )
    return dict(content=output, checks=checks, issues=issues, human_review_required=True)
