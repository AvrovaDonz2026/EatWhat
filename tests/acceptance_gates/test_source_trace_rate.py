"""门禁 1：展示字段 100% 绑定有效 evidence_id 与 source_id。

覆盖：
- /v1/products/*.json 的每个区块字段与条目；
- /v1/ingredients/*.json 的每条配料/营养/致敏原/不适宜人群；
- /v1/gtin/*.json 的每个候选；
并且证据必须在账本中存在，且其 content_hash 与账本记录一致。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from tests.acceptance_gates.conftest import FIXTURE_V1, read_json

REQUIRED_KEYS = ("source_id", "evidence_id")


def _walk(node, path="$"):
    """遍历 JSON，产出 (路径, 字典节点)。"""
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}[{index}]")


def _collect_display_records(data_dir: Path):
    records = []
    for path in sorted((data_dir / "v1" / "products").glob("*.json")):
        payload = read_json(path)
        for block in payload["blocks"]:
            for field in block.get("fields", []):
                records.append((path.name, f"blocks[{block['block_id']}].fields", field))
            for item in block.get("items", []):
                label = item.get("label", "")
                if label in ("数据源覆盖状态", "原始证据哈希"):
                    continue
                if label == "标准文本元数据" or label == "标签所标注的标准号":
                    records.append((path.name, f"blocks[{block['block_id']}].items", item))
                    continue
                records.append((path.name, f"blocks[{block['block_id']}].items", item))
        for observation in payload.get("observations", []):
            records.append((path.name, "observations", observation))
        for event in payload.get("source_visibility_events", []):
            records.append((path.name, "source_visibility_events", event))

    for path in sorted((data_dir / "v1" / "ingredients").glob("*.json")):
        payload = read_json(path)
        for key in ("ingredients", "nutrition_facts", "explicit_allergens", "explicit_unsuitable_groups", "label_standard_refs"):
            for index, entry in enumerate(payload.get(key, [])):
                records.append((path.name, f"{key}[{index}]", entry))

    for path in sorted((data_dir / "v1" / "gtin").glob("*.json")):
        payload = read_json(path)
        for index, candidate in enumerate(payload.get("match_candidates", [])):
            records.append((path.name, f"match_candidates[{index}]", candidate))
    return records


def test_all_display_records_bound_to_evidence(built):
    ledger = built["ledger"]
    data_dir: Path = built["data_dir"]
    known = {snap.evidence_id: snap for snap in ledger.snapshots()}
    assert known, "账本中应至少有一条快照"

    records = _collect_display_records(data_dir)
    assert records, "应当存在展示记录"

    failures = []
    for file_name, location, node in records:
        missing = [key for key in REQUIRED_KEYS if not node.get(key)]
        if missing:
            failures.append(f"{file_name} {location}: 缺少 {missing} -> {json.dumps(node, ensure_ascii=False)[:120]}")
            continue
        evidence_id = node["evidence_id"]
        if evidence_id not in known:
            failures.append(f"{file_name} {location}: evidence_id={evidence_id} 不在账本中")
            continue
        snapshot = known[evidence_id]
        if snapshot.source_id != node["source_id"]:
            failures.append(
                f"{file_name} {location}: source_id={node['source_id']} 与账本 {snapshot.source_id} 不一致"
            )
        raw = node.get("raw_value")
        if raw is not None and not isinstance(raw, (dict, list)):
            # observations 使用规范化 JSON 序列化；比较前统一解析回结构
            candidate = None
            try:
                candidate = json.loads(raw) if str(raw).startswith(("{", "[")) else None
            except json.JSONDecodeError:
                candidate = None
            if candidate is not None:
                if candidate != snapshot.payload:
                    failures.append(
                        f"{file_name} {location}: raw_value 与账本快照内容不一致"
                    )
            elif str(raw) not in json.dumps(snapshot.payload, ensure_ascii=False):
                failures.append(
                    f"{file_name} {location}: raw_value 未出现在原始快照中"
                )

    assert not failures, "存在未绑定有效证据的展示记录:\n" + "\n".join(failures[:20])


def test_field_trace_rate_is_full(built):
    """展示字段追溯率必须为 100%。"""
    ledger = built["ledger"]
    data_dir: Path = built["data_dir"]
    known = {snap.evidence_id for snap in ledger.snapshots()}

    total = 0
    traced = 0
    for file_name, _location, node in _collect_display_records(data_dir):
        total += 1
        if node.get("evidence_id") in known and node.get("source_id"):
            traced += 1

    assert total > 0
    rate = traced / total
    assert rate == 1.0, f"字段追溯率 {rate:.4f} 未达到 100%（{traced}/{total}）"


def test_no_final_truth_field(built):
    """不得存在最终真相字段（例如结论/最终值/单一裁定）。"""
    data_dir: Path = built["data_dir"]
    forbidden = {"final_truth", "final_value", "verdict", "conclusion", "final_conclusion", "verdict_value"}
    offenders = []
    for path in data_dir.rglob("*.json"):
        for node_path, node in _walk(read_json(path)):
            keys = {str(key).lower() for key in node}
            hit = keys & forbidden
            if hit:
                offenders.append(f"{path.name} {node_path}: {sorted(hit)}")
    assert not offenders, "出现最终真相字段:\n" + "\n".join(offenders[:10])

    product = read_json(next((data_dir / "v1" / "products").glob("*.json")))
    assert product["observation_policy"]["no_final_truth_field"] is True


def test_retrieved_at_is_single_valued_per_evidence(built, make_builder, tmp_path):
    """同一 evidence_id 在所有分片中的 retrieved_at 必须唯一。

    早期实现中，内容未变的快照会返回本次复核时间，而历史读取走首次落盘时间，
    导致 gtin 分片与 products 分片对同一条证据给出两个不同的 retrieved_at。
    """
    data_dir: Path = built["data_dir"]
    builder = make_builder(data_dir=tmp_path / "rebuild", fixtures_root=FIXTURE_V1)
    hints = json.loads((FIXTURE_V1 / "description_hints.json").read_text(encoding="utf-8"))
    hints.pop("_note", None)
    # 重复构建：只应追加重建观测，不应改变既有证据的时间戳
    builder.build(
        gtins=["6901234567892", "6905554443331", "6901112223339"],
        description_hints=hints,
        build_id="build_test_reobserve",
        note="reobserve",
    )

    seen: Dict[str, set] = {}
    for path in list((data_dir / "v1" / "gtin").glob("*.json")) + list(
        (tmp_path / "rebuild" / "v1" / "gtin").glob("*.json")
    ) + list((data_dir / "v1" / "products").glob("*.json")) + list(
        (tmp_path / "rebuild" / "v1" / "products").glob("*.json")
    ):
        payload = read_json(path)

        def collect(node):
            if isinstance(node, dict):
                ev = node.get("evidence_id")
                ts = node.get("retrieved_at")
                if isinstance(ev, str) and isinstance(ts, str):
                    seen.setdefault(ev, set()).add(ts)
                for value in node.values():
                    collect(value)
            elif isinstance(node, list):
                for value in node:
                    collect(value)

        collect(payload)

    assert seen, "未收集到任何 evidence_id/retrieved_at 组合"
    conflicts = {ev: sorted(ts) for ev, ts in seen.items() if len(ts) > 1}
    assert not conflicts, f"同一证据出现多个 retrieved_at: {list(conflicts.items())[:3]}"
