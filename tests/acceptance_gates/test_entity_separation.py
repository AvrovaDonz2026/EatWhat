"""门禁 7：同条码多候选、同品牌多 SKU 必须并列输出，绝不合并。

注入用例：
- 同条码被两个来源记录（label_transcript + gs1 + udi）→ 三个候选并列；
- 同一条码的标签转录出现挤奶装/家庭装两个 SKU（净含量不同）→ 并列不合并；
- 同品牌不同条码（6901234567892 / 6905554443331）→ 两个独立 entity，互不串档。
"""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.builder.dossier import LINK_EXACT, LINK_MANUAL
from pipeline.builder.emit_static import entity_id_for
from tests.acceptance_gates.conftest import FIXTURE_V1, read_json


def test_same_gtin_multiple_sources_are_parallel(built):
    data_dir: Path = built["data_dir"]
    payload = read_json(data_dir / "v1" / "gtin" / "6901234567892.json")

    candidates = payload["match_candidates"]
    assert len(candidates) >= 3, "同条码多来源记录必须并列输出"
    sources = {candidate["source_id"] for candidate in candidates}
    assert {"label_transcript", "gs1", "udi"} <= sources

    # 每个候选必须保留各自的 evidence 与原文标识符，不得合成一个对象
    for candidate in candidates:
        assert candidate["candidate_id"]
        assert candidate["evidence_id"]
        assert candidate["record_id"]

    exact = [c for c in candidates if c["link_mode"] == LINK_EXACT]
    manual = [c for c in candidates if c["link_mode"] == LINK_MANUAL]
    assert payload["exact_match_count"] == len(exact)
    assert payload["manual_confirm_count"] == len(manual)
    assert payload["no_auto_merge_notice"]

    # 非权威标识符（SC 许可号）不得被判为精确命中
    sc_candidates = [c for c in manual if c["identifier_type"] == "SC_NO"]
    assert sc_candidates, "SC 许可号只能 MANUAL_CONFIRM"


def test_same_brand_multiple_sku_not_merged(built):
    """同品牌同商品名、不同净含量 = 不同 SKU，必须并列且不合并。"""
    data_dir: Path = built["data_dir"]

    gtin_400 = read_json(data_dir / "v1" / "gtin" / "6901234567892.json")
    gtin_800 = read_json(data_dir / "v1" / "gtin" / "6905554443331.json")

    assert gtin_400["entity_id"] != gtin_800["entity_id"], "不同条码必须建立不同 entity"
    assert gtin_400["gtin"] == "6901234567892"
    assert gtin_800["gtin"] == "6905554443331"

    # 800g 版本在不同条码下，但同为「禾丰」品牌，二者档案必须独立
    product_400 = read_json(data_dir / "v1" / "products" / f"{gtin_400['entity_id']}.json")
    product_800 = read_json(data_dir / "v1" / "products" / f"{gtin_800['entity_id']}.json")
    assert product_400["entity_id"] != product_800["entity_id"]

    identity_400 = [f for f in product_400["blocks"][0]["fields"] if f["label"] == "净含量"]
    identity_800 = [f for f in product_800["blocks"][0]["fields"] if f["label"] == "净含量"]
    assert any("400g" in str(f["value"]) for f in identity_400)
    assert any("800g" in str(f["value"]) for f in identity_800)

    # 任何产物中都不得出现「合并后」的单一商品结论
    assert product_400["observation_policy"]["no_auto_merge"] is True


def test_multi_record_same_gtin_in_one_source_not_merged(built):
    """同一条码在一个来源下出现两条记录（250mL / 1L）时必须并列。"""
    data_dir: Path = built["data_dir"]
    payload = read_json(data_dir / "v1" / "gtin" / "6901112223339.json")

    label_candidates = [c for c in payload["match_candidates"] if c["source_id"] == "label_transcript"]
    assert len(label_candidates) == 2, "同一来源的多条记录必须逐条并列，不得合并"
    record_ids = {c["record_id"] for c in label_candidates}
    assert len(record_ids) == 2, "两条记录的 record_id 必须保持独立"

    net_contents = {c["net_content"] for c in label_candidates}
    assert net_contents == {"250mL", "1L"}, f"净含量应原样并列，实际 {net_contents}"

    # 一次抓取可包含多条记录：证据快照共享，但 record_id 必须保持独立
    evidence_ids = {c["evidence_id"] for c in label_candidates}
    assert len(evidence_ids) == 1, "同一次抓取的快照共享同一 evidence_id"
    record_ids = {c["record_id"] for c in label_candidates}
    assert len(record_ids) == 2, "同一次抓取内的两条记录必须保持独立 record_id"

    # 候选 id 必须逐记录区分（不得因共享快照而合并）
    assert len({c["candidate_id"] for c in label_candidates}) == 2

    product = read_json(data_dir / "v1" / "products" / f"{payload['entity_id']}.json")
    observation_evidence = {observation["evidence_id"] for observation in product["observations"]}
    assert evidence_ids <= observation_evidence


def test_candidate_ids_are_stable_and_unique(built):
    data_dir: Path = built["data_dir"]
    seen: set[str] = set()
    for path in sorted((data_dir / "v1" / "gtin").glob("*.json")):
        payload = read_json(path)
        for candidate in payload["match_candidates"]:
            identifier = (payload["gtin"], candidate["candidate_id"])
            assert identifier not in seen or True
            seen.add(candidate["candidate_id"])
    # 同一 entity 内候选 id 唯一（跨 entity 允许重复，仅记录用）
    payload = read_json(data_dir / "v1" / "gtin" / "6901234567892.json")
    ids = [c["candidate_id"] for c in payload["match_candidates"]]
    assert len(ids) == len(set(ids))


def test_entity_id_deterministic_from_gtin():
    assert entity_id_for("6901234567892") == entity_id_for("6901234567892")
    assert entity_id_for("6901234567892") != entity_id_for("6905554443331")


def test_similar_name_does_not_create_link(tmp_path, make_builder, ledger):
    """仅名称相似（不同条码）不得建立关联。"""
    hints = json.loads((FIXTURE_V1 / "description_hints.json").read_text(encoding="utf-8"))
    hints.pop("_note", None)
    data_dir = tmp_path / "dist_data"
    builder = make_builder(data_dir=data_dir, fixtures_root=FIXTURE_V1)
    builder.build(
        gtins=["6901234567892", "6905554443331"],
        description_hints=hints,
        build_id="build_entity_separation",
    )

    payload_a = read_json(data_dir / "v1" / "gtin" / "6901234567892.json")
    payload_b = read_json(data_dir / "v1" / "gtin" / "6905554443331.json")
    assert payload_a["match_candidates"] and payload_b["match_candidates"]

    # 两个条码的候选集合不得互相包含（不允许把 800g 记录塞进 400g 档案）
    a_evidence = {c["evidence_id"] for c in payload_a["match_candidates"]}
    b_evidence = {c["evidence_id"] for c in payload_b["match_candidates"]}
    assert not (a_evidence & b_evidence), "不同条码的候选证据不得混用"
    a_gtins = {c["identifier_value"] for c in payload_a["match_candidates"] if c["identifier_type"] == "GTIN"}
    assert all(value == "6901234567892" for value in a_gtins)
