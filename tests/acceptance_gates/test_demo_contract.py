"""门禁 3 补充：静态数据与端侧演示样例的一致性。

确保 /v1/demo/index.json 与正式产物契约同构，使离线兜底不会退化成另一套结构。
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.acceptance_gates.conftest import read_json

DEMO_KEYS = {"gtin", "entity_id", "query_status", "match_candidates", "coverage_manifest", "blocks", "ingredients"}
OFFICIAL_GTIN_KEYS = {"gtin", "entity_id", "query_status", "match_candidates", "coverage_manifest"}


def test_demo_index_exists_and_is_offline_ready(built):
    data_dir: Path = built["data_dir"]
    demo_path = data_dir / "v1" / "demo" / "index.json"
    assert demo_path.exists(), "必须提供离线兜底样例 /v1/demo/index.json"

    demo = read_json(demo_path)
    assert demo["entries"], "兜底样例不得为空"
    assert demo["note"], "兜底样例必须自我说明"
    for entry in demo["entries"]:
        assert DEMO_KEYS <= set(entry), f"兜底样例缺少字段: {DEMO_KEYS - set(entry)}"
        assert entry["query_status"] in ("QUERIED_MATCHED", "QUERIED_NO_MATCH")
        assert entry["match_candidates"], "兜底样例需包含候选"
        assert entry["ingredients"]["ingredients"], "兜底样例需包含配料"


def _shared_identity(payload: dict) -> dict:
    """比较共享字段的结构（忽略 build 元信息）。"""
    return {key: payload[key] for key in ("gtin", "entity_id", "query_status")}


def test_demo_entries_match_official_contract(built):
    data_dir: Path = built["data_dir"]
    demo = read_json(data_dir / "v1" / "demo" / "index.json")

    for entry in demo["entries"]:
        official = read_json(data_dir / "v1" / "gtin" / f"{entry['gtin']}.json")
        assert _shared_identity(entry) == _shared_identity(official)
        assert set(entry["ingredients"]) >= {"schema_version", "gtin", "ingredients", "nutrition_facts", "disclaimer"}
        demo_sources = {c["source_id"] for c in entry["match_candidates"]}
        official_sources = {c["source_id"] for c in official["match_candidates"]}
        assert demo_sources == official_sources, f"{entry['gtin']}: 样例候选来源与正式产物不一致"


def test_demo_covers_subset_of_official(built):
    data_dir: Path = built["data_dir"]
    demo = read_json(data_dir / "v1" / "demo" / "index.json")
    demo_gtins = {entry["gtin"] for entry in demo["entries"]}
    official_gtins = {read_json(path)["gtin"] for path in (data_dir / "v1" / "gtin").glob("*.json")}
    assert demo_gtins <= official_gtins, f"样例中出现正式产物没有的条码: {demo_gtins - official_gtins}"
    assert len(demo_gtins) >= 2, "至少提供两个离线样例"


def test_demo_blocks_shapes(built):
    data_dir: Path = built["data_dir"]
    demo = read_json(data_dir / "v1" / "demo" / "index.json")
    for entry in demo["entries"]:
        for block in entry["blocks"]:
            # 兜底样例允许携带与正式区块一致的候选计数附加字段
            allowed = {"block_id", "title", "state", "note", "fields", "items", "exact_match_count", "manual_confirm_count"}
            assert set(block) <= allowed, f"区块多余字段: {set(block) - allowed}"
            assert block["block_id"] and block["state"]


def test_ingredients_contract(built):
    data_dir: Path = built["data_dir"]
    for path in (data_dir / "v1" / "ingredients").glob("*.json"):
        payload = read_json(path)
        for key in ("ingredients", "nutrition_facts", "explicit_allergens", "explicit_unsuitable_groups", "label_standard_refs"):
            assert key in payload, f"{path.name}: 缺少 {key}"
        assert payload["disclaimer"], "配料文件必须附带免责声明"
        for ingredient in payload["ingredients"]:
            if "order" in ingredient:
                assert isinstance(ingredient["order"], int)
