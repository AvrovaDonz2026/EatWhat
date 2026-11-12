"""门禁 5：依据构建记录中的源、键、时间、适配器版本可完整复现产物。

做法：同一夹具、同一构建序列在全新账本上重跑，产物的「内容指纹」除 build_id/built_at
与流水时间戳外必须逐字节一致（对结构做规范化后再比较）。
"""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.adapters.base import adapter_registry_map
from pipeline.builder.emit_static import TOOL_VERSION
from tests.acceptance_gates.conftest import FIXTURE_V1, read_json

#: 允许随构建批次变化的字段（版本标识与采集时间）
VOLATILE_KEYS = {"build_id", "built_at", "retrieved_at", "recorded_at", "written_at"}


def normalize(node):
    if isinstance(node, dict):
        return {
            key: normalize(value)
            for key, value in node.items()
            if key not in VOLATILE_KEYS
        }
    if isinstance(node, list):
        return [normalize(value) for value in node]
    return node


def _build_all(tmp_path: Path, registry, sleeper, build_id: str):
    """每次复现都从空账本开始：复现语义是「相同账本状态 + 相同输入 → 相同产物」。"""
    from pipeline.builder.emit_static import Builder
    from pipeline.builder.ledger import Ledger

    hints = json.loads((FIXTURE_V1 / "description_hints.json").read_text(encoding="utf-8"))
    hints.pop("_note", None)
    data_dir = tmp_path / build_id
    ledger = Ledger(tmp_path / f"{build_id}.sqlite3")
    try:
        builder = Builder(
            registry=registry,
            ledger=ledger,
            data_dir=data_dir,
            fixtures_root=FIXTURE_V1,
            sleeper=sleeper,
        )
        outcome = builder.build(
            gtins=["6901234567892", "6905554443331", "6901112223339"],
            description_hints=hints,
            build_id=build_id,
            note="reproducibility",
        )
    finally:
        ledger.close()
    return outcome, data_dir


def test_build_record_is_complete(tmp_path, registry, sleeper):
    """账本必须记录复现所需全部要素：源、键、时间、适配器版本。"""
    out = tmp_path / "build_repro_probe"
    _build_all(tmp_path, registry, sleeper, "build_repro_probe")
    ledger = __import__("pipeline.builder.ledger", fromlist=["Ledger"]).Ledger(tmp_path / "build_repro_probe.sqlite3")

    runs = ledger.build_runs()
    assert len(runs) >= 1
    run = runs[0]
    assert run["registry_version"]
    assert run["tool_version"] == TOOL_VERSION
    versions = json.loads(run["adapter_versions"])
    assert versions == adapter_registry_map()
    assert run["finished_at"], "构建必须标记完成时间"

    snapshots = ledger.snapshots()
    assert snapshots
    for snapshot in snapshots:
        assert snapshot.source_id, "缺少源"
        assert snapshot.source_key, "缺少查询键"
        assert snapshot.retrieved_at, "缺少采集时间"
        assert snapshot.adapter_version, "缺少适配器版本"
        assert snapshot.payload_sha256, "缺少内容哈希"
        assert snapshot.request_url, "缺少请求现场"
    ledger.close()


def test_products_are_byte_reproducible(tmp_path, registry, sleeper):
    """两次独立构建的产物内容必须一致（去掉版本/时间等易变字段后）。"""
    outcome_a, dir_a = _build_all(tmp_path / "run_a", registry, sleeper, "build_repro_a")
    outcome_b, dir_b = _build_all(tmp_path / "run_b", registry, sleeper, "build_repro_b")

    files_a = sorted(p.relative_to(dir_a) for p in dir_a.rglob("*.json"))
    files_b = sorted(p.relative_to(dir_b) for p in dir_b.rglob("*.json"))
    assert files_a == files_b, "两次构建产物文件集合不同"

    mismatches = []
    for relative in files_a:
        if relative.name == "manifest.json":
            continue
        left = normalize(read_json(dir_a / relative))
        right = normalize(read_json(dir_b / relative))
        if left != right:
            mismatches.append(str(relative))
    assert not mismatches, f"以下产物不可复现: {mismatches[:10]}"


def test_manifest_records_reproducibility_inputs(tmp_path, registry, sleeper):
    """manifest 必须给出复现所需的源清单与适配器版本。"""
    outcome, data_dir = _build_all(tmp_path, registry, sleeper, "build_repro_manifest")
    manifest = read_json(data_dir / "v1" / "manifest.json")
    assert manifest["build_id"] == outcome.build_id
    assert manifest["tool_version"] == TOOL_VERSION
    assert manifest["registry_version"], "manifest 缺少策略注册表版本"
    assert manifest["adapter_versions"], "manifest 缺少适配器版本表"
    assert manifest["query_index"], "manifest 缺少查询索引"
    # 查询索引必须可直接复现请求键
    for entry in manifest["query_index"]:
        assert entry["gtin"].isdigit()


def test_artifact_hashes_match_files(tmp_path, registry, sleeper):
    """账本记录的产物哈希必须与实际文件一致（可离线校验复现结果）。"""
    from pipeline.builder.ledger import Ledger, sha256_of

    outcome, data_dir = _build_all(tmp_path, registry, sleeper, "build_repro_hash")
    ledger = Ledger(tmp_path / "build_repro_hash.sqlite3")
    artifacts = {row["path"]: row for row in ledger.artifacts()}
    ledger.close()
    assert artifacts, "账本应记录产物哈希"

    for relative, row in artifacts.items():
        path = data_dir / relative
        assert path.exists(), f"账本记录的产品不存在: {relative}"
        digest = sha256_of(path.read_text(encoding="utf-8").encode("utf-8"))
        assert digest == row["sha256"], f"{relative}: 哈希与账本不一致"
        assert row["bytes"] == path.stat().st_size
