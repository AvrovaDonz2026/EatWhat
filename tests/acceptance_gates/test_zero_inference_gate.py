"""门禁 10：附录 B 语义门禁（对抗语料 + 产物 + 前端源码 + 打包产物）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.semantic_gate.rules import (
    BANNED_TERMS,
    FORBIDDEN_COLOR_PATTERNS,
    RAW_VALUE_KEYS,
    scan_json,
    scan_text,
    scan_tree,
)
from tests.acceptance_gates.conftest import FIXTURE_V1, ROOT, read_json

CORPUS_PATH = ROOT / "packages" / "semantic_gate" / "adversarial_corpus.json"


def test_gate_blocks_every_adversarial_category():
    """对抗语料的每个非对照类别都必须被拦截。"""
    corpus = read_json(CORPUS_PATH)
    categories = [key for key in corpus if not key.startswith("_") and key != "benign_control"]
    assert len(categories) >= 6, "对抗语料必须覆盖附录 B 全部类别"

    for category in categories:
        samples = corpus[category]
        assert samples, f"{category} 缺少对抗样本"
        for sample in samples:
            findings = scan_text(sample, f"corpus:{category}")
            assert findings, f"未被拦截的对抗样本 [{category}]: {sample}"


def test_gate_does_not_flag_benign_copy():
    """合规文案（免责声明、机械命中提示、状态码）不得被误判。"""
    corpus = read_json(CORPUS_PATH)
    for sample in corpus["benign_control"]:
        findings = scan_text(sample, "corpus:benign")
        assert not findings, f"误报: {sample} -> {[f.term for f in findings]}"


def test_gate_blocks_risk_color_coding():
    corpus = read_json(CORPUS_PATH)
    for sample in corpus["risk_color"]:
        findings = scan_text(sample, "corpus:risk_color")
        assert findings, f"风控配色未被拦截: {sample}"
    assert FORBIDDEN_COLOR_PATTERNS


def test_static_artifacts_are_semantically_clean(built):
    """全部静态产物不得出现附录 B 禁用词（raw_value 原文豁免）。"""
    data_dir: Path = built["data_dir"]
    findings = scan_tree(data_dir)
    assert not findings, "静态产物命中禁用词:\n" + "\n".join(
        f"{f.where} {f.node_path} 「{f.term}」" for f in findings[:10]
    )


def test_raw_value_exemption_is_narrow(built):
    """raw_value 豁免必须可证明：非原文里的同类词仍然会拦截。"""
    payload = {
        "block_id": "label_ingredients",
        "note": "本条为来源原文，允许保留",
        "raw_value": "标签原文：本品含有乳及乳制品",
        "items": [{"label": "配料原文", "raw_value": "全脂乳粉"}],
    }
    assert not scan_json(payload, "inline")

    # 同一句话若出现在非 raw_value 字段，必须被拦截
    bad = {"block_id": "label_ingredients", "note": "该商品同源产品很多", "fields": []}
    findings = scan_json(bad, "inline:bad")
    assert findings, "非 raw_value 字段中的禁用词必须被拦截"
    assert all(f.node_path.endswith(".note") for f in findings)


def test_web_source_has_no_banned_terms():
    """网页源码（apps/web/src）不得出现附录 B 禁用词。"""
    web_src = ROOT / "apps" / "web" / "src"
    findings = scan_tree(web_src)
    assert not findings, "前端源码命中禁用词:\n" + "\n".join(
        f"{f.where}:{f.line} 「{f.term}」" for f in findings[:10]
    )


def test_single_file_build_is_clean():
    """打包后的单文件 HTML 必须干净（若产物存在）。"""
    dist = ROOT / "apps" / "web" / "dist" / "index.html"
    if not dist.exists():
        pytest.skip("尚未构建 apps/web/dist")
    findings = scan_text(dist.read_text(encoding="utf-8"), str(dist))
    assert not findings, "打包产物命中禁用词:\n" + "\n".join(
        f"{f.where}:{f.line} 「{f.term}」" for f in findings[:10]
    )


def test_banned_terms_cover_six_categories():
    categories = {item["category"] for item in BANNED_TERMS}
    expected = {
        "RISK_ASSESSMENT",
        "AUTHENTICITY",
        "LEGALITY",
        "RELATION_INFERENCE",
        "PURCHASE_ADVICE",
        "MOTIVE_JUDGEMENT",
        "EFFICACY_CLAIM",
    }
    assert expected <= categories, f"缺少类别: {expected - categories}"


def test_no_health_efficacy_claims_in_artifacts(built):
    """产物中不得出现功效/健康声称。"""
    data_dir: Path = built["data_dir"]
    efficacy_terms = ("治疗", "治愈", "预防疾病", "增强免疫", "降血糖", "降血压")
    offenders = []
    for path in data_dir.rglob("*.json"):
        payload = read_json(path)

        def walk(node, key=""):
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, k)
            elif isinstance(node, list):
                for item in node:
                    walk(item, key)
            elif isinstance(node, str) and key not in RAW_VALUE_KEYS:
                for term in efficacy_terms:
                    if term in node:
                        offenders.append((path.name, key, term))

        walk(payload)
    assert not offenders, f"出现功效声称: {offenders[:5]}"


def test_dev_field_does_not_leak_into_ui_copy():
    """状态码等工程字段可以出现在 UI，但不能成为对用户的评价性结论。"""
    from packages.semantic_gate.rules import scan_text as gate

    for text in (
        "QUERIED_NO_MATCH：当前已接入静态档案库未匹配到记录",
        "ACCESS_PENDING：该数据源接入待授权，本档案不含其记录",
        "SOURCE_UNAVAILABLE：该数据源当前不可稳定访问，本档案不含其记录",
    ):
        assert not gate(text, "ui:status")
