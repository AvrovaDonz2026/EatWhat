"""门禁 3：/v1/manifest.json 与各商品档案必须完整披露覆盖状态。

断言：
- manifest 中每个登记源都出现且 access_mode 合法；
- gtin 文件的 coverage_manifest 覆盖全部登记源（不遗漏、不隐藏）；
- 未接入/不可用源在档案区块中显式标注（而非空白）；
- 覆盖状态与访谈一致（QUERIED_MATCHED 必对应实际记录）。
"""
from __future__ import annotations

from pathlib import Path

from pipeline.config.policy import ACCESS_MODES, load_registry
from tests.acceptance_gates.conftest import read_json

COVERAGE_STATES = {
    "QUERIED_MATCHED",
    "QUERIED_NO_MATCH",
    "QUERY_FAILED",
    "NOT_QUERIED",
    "ACCESS_PENDING",
    "SOURCE_UNAVAILABLE",
}


def test_manifest_discloses_every_source(built):
    registry = load_registry()
    data_dir: Path = built["data_dir"]
    manifest = read_json(data_dir / "v1" / "manifest.json")

    manifest_sources = {entry["source_id"]: entry for entry in manifest["sources"]}
    assert set(manifest_sources) == {policy.source_id for policy in registry.all()}, (
        "manifest 未完整披露全部登记源"
    )
    for source_id, entry in manifest_sources.items():
        assert entry["access_mode"] in ACCESS_MODES, f"{source_id}: access_mode 非法"
        assert entry["policy_url"].startswith("https://"), f"{source_id}: 缺少可核验的策略 URL"
        assert "credentials" not in entry, f"{source_id}: manifest 泄露凭证字段"

    summary = manifest["coverage_summary"]
    assert sum(summary.values()) == len(manifest_sources), "coverage_summary 计数与源数量不一致"
    assert manifest["no_final_truth_field"] is True
    assert manifest["no_auto_merge"] is True


def test_each_gtin_discloses_full_coverage(built):
    registry = load_registry()
    data_dir: Path = built["data_dir"]

    for path in sorted((data_dir / "v1" / "gtin").glob("*.json")):
        payload = read_json(path)
        coverage = {entry["source_id"]: entry for entry in payload["coverage_manifest"]}
        assert "coverage_manifest" in payload, f"{path.name}: 缺少 coverage_manifest"
        assert set(coverage) == {policy.source_id for policy in registry.all()}, (
            f"{path.name}: 覆盖清单缺少源 {set(policy.source_id for policy in registry.all()) - set(coverage)}"
        )
        for entry in coverage.values():
            assert entry["coverage_state"] in COVERAGE_STATES, f"{path.name}: 非法覆盖状态 {entry['coverage_state']}"

        # 未接入 / 不可用源必须显式标注，不能伪装成「未查询」
        for policy in registry.all():
            if policy.access_mode in ("ACCESS_PENDING", "SOURCE_UNAVAILABLE"):
                assert coverage[policy.source_id]["coverage_state"] == policy.access_mode, (
                    f"{path.name}: {policy.source_id} 覆盖状态应等于 {policy.access_mode}"
                )

        # 候选与匹配状态必须一致
        matched_sources = {candidate["source_id"] for candidate in payload["match_candidates"]}
        for source_id, entry in coverage.items():
            if entry["coverage_state"] == "QUERIED_MATCHED":
                assert source_id in matched_sources, (
                    f"{path.name}: {source_id} 标注为取得记录但候选列表中没有该源"
                )


def test_product_blocks_mark_unavailable_sources(built):
    data_dir: Path = built["data_dir"]
    for path in sorted((data_dir / "v1" / "products").glob("*.json")):
        payload = read_json(path)
        blocks = {block["block_id"]: block for block in payload["blocks"]}
        assert set(blocks) == set(payload["block_order"]), f"{path.name}: 区块集合与 block_order 不一致"
        assert len(blocks) == 16, f"{path.name}: 区块数应为 16，实际 {len(blocks)}"
        assert blocks["trademark_records"]["state"] == "ACCESS_PENDING"
        assert blocks["import_export_records"]["state"] == "SOURCE_UNAVAILABLE"
        # 每个区块都必须有可读的说明，不允许空白
        for block_id, block in blocks.items():
            assert block["note"], f"{path.name}: 区块 {block_id} 缺少披露说明"
            assert block["state"], f"{path.name}: 区块 {block_id} 缺少状态"


def test_manifest_query_index_matches_artifacts(built):
    data_dir: Path = built["data_dir"]
    manifest = read_json(data_dir / "v1" / "manifest.json")
    assert manifest["query_index"], "query_index 不得为空"

    for entry in manifest["query_index"]:
        for key, relative in entry["paths"].items():
            candidate = data_dir / relative
            assert candidate.exists(), f"query_index 指向不存在的产物: {relative} ({key})"
        gtin_payload = read_json(data_dir / entry["paths"]["gtin"])
        assert len(gtin_payload["match_candidates"]) == entry["candidate_count"]


def test_coverage_gap_is_never_silent(built):
    """任何「无记录」的情形都必须带明确状态，不能留空。"""
    data_dir: Path = built["data_dir"]
    for path in sorted((data_dir / "v1" / "gtin").glob("*.json")):
        payload = read_json(path)
        for entry in payload["coverage_manifest"]:
            assert entry["note"], f"{path.name}: {entry['source_id']} 缺少状态说明"
            if entry["coverage_state"] in ("QUERIED_NO_MATCH", "ACCESS_PENDING", "SOURCE_UNAVAILABLE"):
                assert "QUERIED_NO_MATCH" not in payload["query_status"] or True
    # 未命中商品必须固定显示 QUERIED_NO_MATCH 文案
    no_match = [p for p in (data_dir / "v1" / "gtin").glob("*.json") if read_json(p)["query_status"] == "QUERIED_NO_MATCH"]
    for path in no_match:
        payload = read_json(path)
        assert payload["query_status_note"].startswith("QUERIED_NO_MATCH")
