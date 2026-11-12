"""门禁 2：字段抽取准确率 >= 99.5%（黄金样本集比对原始抓取快照）。

做法：把静态 JSON 中所有可追溯到 evidence_id 的字段，与账本里的原始快照逐项比对。
"""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.builder.ledger import canonical_json
from tests.acceptance_gates.conftest import read_json

MIN_ACCURACY = 0.995


def _collect_pairs(data_dir: Path, snapshots):
    """收集 (静态产物值, 原始快照对应值) 对。"""
    by_id = {snap.evidence_id: snap for snap in snapshots}
    pairs: list[tuple[str, object, object]] = []

    for path in sorted((data_dir / "v1" / "products").glob("*.json")):
        payload = read_json(path)
        for block in payload["blocks"]:
            for field in block.get("fields", []):
                snapshot = by_id.get(field.get("evidence_id", ""))
                if snapshot is None:
                    continue
                pairs.append((f"{path.name}:{block['block_id']}:{field['label']}", field["raw_value"], snapshot.payload))
            for item in block.get("items", []):
                snapshot = by_id.get(item.get("evidence_id", ""))
                if snapshot is None:
                    continue
                label = item.get("label", "")
                if label in ("数据源覆盖状态", "原始证据哈希"):
                    continue
                raw = item.get("raw_value")
                if raw is None:
                    continue
                pairs.append((f"{path.name}:{block['block_id']}:{label}:{item.get('record_id', '')}", raw, snapshot.payload))

    for path in sorted((data_dir / "v1" / "ingredients").glob("*.json")):
        payload = read_json(path)
        for key in ("ingredients", "explicit_allergens", "explicit_unsuitable_groups"):
            for index, entry in enumerate(payload.get(key, [])):
                snapshot = by_id.get(entry.get("evidence_id", ""))
                if snapshot is None:
                    continue
                raw = entry.get("raw_value", entry.get("raw_text"))
                pairs.append((f"{path.name}:{key}[{index}]", raw, snapshot.payload))
        for index, entry in enumerate(payload.get("nutrition_facts", [])):
            snapshot = by_id.get(entry.get("evidence_id", ""))
            if snapshot is None:
                continue
            pairs.append((f"{path.name}:nutrition_facts[{index}]", entry["raw_value"], snapshot.payload))

    for path in sorted((data_dir / "v1" / "gtin").glob("*.json")):
        payload = read_json(path)
        gtin = payload["gtin"]
        for index, candidate in enumerate(payload.get("match_candidates", [])):
            snapshot = by_id.get(candidate.get("evidence_id", ""))
            if snapshot is None:
                continue
            pairs.append((f"{path.name}:candidate[{index}]:record_id", candidate["record_id"], snapshot.payload))
            if candidate.get("display_name"):
                pairs.append(
                    (f"{path.name}:candidate[{index}]:display_name", candidate["display_name"], snapshot.payload)
                )
        pairs.append((f"{path.name}:gtin", gtin, None))
    return pairs


def _value_in_snapshot(value, payload) -> bool:
    text = json.dumps(payload, ensure_ascii=False)
    if str(value) in text:
        return True
    # 允许规范化等价的数值/字符串表示
    try:
        normalized = json.loads(str(value))
    except (json.JSONDecodeError, TypeError):
        normalized = None
    if normalized is not None:
        return canonical_json(normalized) in canonical_json(payload)
    return False


def test_extraction_accuracy_at_least_99_5(built):
    ledger = built["ledger"]
    data_dir: Path = built["data_dir"]
    snapshots = ledger.snapshots()
    assert snapshots

    pairs = [pair for pair in _collect_pairs(data_dir, snapshots) if pair[2] is not None]
    assert pairs, "应存在可比对的字段对"
    assert len(pairs) > 30, f"黄金样本对过少（{len(pairs)}），不足以评估准确率"

    mismatches = [(name, value) for name, value, payload in pairs if not _value_in_snapshot(value, payload)]
    accuracy = (len(pairs) - len(mismatches)) / len(pairs)
    assert accuracy >= MIN_ACCURACY, (
        f"字段抽取准确率 {accuracy:.4%} 低于 {MIN_ACCURACY:.1%}；"
        f"共 {len(pairs)} 项，{len(mismatches)} 项不一致: {mismatches[:8]}"
    )


def test_golden_samples_cover_all_sources(built):
    """每个商品的观测必须覆盖该商品在账本中所有实际取到记录的源。"""
    ledger = built["ledger"]
    data_dir: Path = built["data_dir"]
    snapshots = ledger.snapshots()
    assert snapshots

    seen_sources: set[str] = set()
    for path in sorted((data_dir / "v1" / "products").glob("*.json")):
        product = read_json(path)
        observed_sources = {entry["source_id"] for entry in product["observations"]}
        assert observed_sources, f"{path.name}: observations 不得为空"
        seen_sources |= observed_sources

        evidence_sources = {
            snapshot.source_id
            for snapshot in snapshots
            if snapshot.evidence_id in {entry["evidence_id"] for entry in product["observations"]}
        }
        assert evidence_sources <= observed_sources, (
            f"{path.name}: 证据来源未被完整并列: 证据={sorted(evidence_sources)} 观测={sorted(observed_sources)}"
        )

    # 全部商品合计应覆盖本次取到记录的所有来源（仅排除标准文本这类按标准号查询的源）
    ledger_sources = {snapshot.source_id for snapshot in snapshots}
    assert ledger_sources - {"samr_std"} <= seen_sources, (
        f"账本中的来源未出现在任何商品的观测中: {sorted(ledger_sources - seen_sources)}"
    )


def test_ingredient_order_preserved(built):
    """配料表顺序必须与原始标签原文顺序一致（不得重排）。"""
    ledger = built["ledger"]
    data_dir: Path = built["data_dir"]
    known = {snap.evidence_id: snap for snap in ledger.snapshots()}

    for path in sorted((data_dir / "v1" / "ingredients").glob("*.json")):
        payload = read_json(path)
        for entry in payload["ingredients"]:
            snapshot = known[entry["evidence_id"]]
            records = snapshot.payload.get("records", [])
            raw_lists = [record.get("ingredients") for record in records if record.get("ingredients")]
            assert raw_lists, f"{path.name}: 证据中应含 ingredients"
            raw = next((lst for lst in raw_lists if entry["raw_name"] in lst), None)
            assert raw is not None, f"{path.name}: 配料 {entry['raw_name']} 不在原始快照中"
            assert raw.index(entry["raw_name"]) + 1 == entry["order"], (
                f"{path.name}: 配料顺序被重排（{entry['raw_name']} 应为第 {raw.index(entry['raw_name']) + 1} 项）"
            )
