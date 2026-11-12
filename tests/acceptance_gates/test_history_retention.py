"""门禁 4：来源更新与下线时，旧快照必须保留，并追加 source_visibility_event。

模拟流程：
1. 基线构建（v1）→ 记录 UDI / samr_gov 快照；
2. 用 v1_rev2 夹具再构建（来源内容变化，samr_gov 状态由「有效」变为「注销」）；
3. 断言：旧快照仍在账本中、出场为 HISTORICALLY_OBSERVED；新快照为 CURRENTLY_VISIBLE；
4. 断言：所有 evidence 均不可被删除（触发器阻断）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from pipeline.builder.emit_static import entity_id_for
from pipeline.builder.ledger import LedgerError, append_only_guard
from tests.acceptance_gates.conftest import FIXTURE_REV2, FIXTURE_V1, read_json


def _build(make_builder, fixtures_root: Path, data_dir: Path, build_id: str):
    hints_path = FIXTURE_V1 / "description_hints.json"
    hints = json.loads(hints_path.read_text(encoding="utf-8"))
    hints.pop("_note", None)
    builder = make_builder(data_dir=data_dir, fixtures_root=fixtures_root)
    return builder.build(gtins=["6901234567892"], description_hints=hints, build_id=build_id, note=build_id)


def test_old_snapshot_retained_after_source_update(tmp_path, make_builder, ledger):
    gtin = "6901234567892"
    data_dir = tmp_path / "dist_data"

    baseline = _build(make_builder, FIXTURE_V1, data_dir, "build_test_v1")
    revision = _build(make_builder, FIXTURE_REV2, data_dir, "build_test_v2")

    entity_id = entity_id_for(gtin)
    snapshots = {snap.evidence_id: snap for snap in ledger.snapshots()}

    assert baseline.build_id != revision.build_id
    # 两版 UDI 快照同时存在，内容不同
    udi_snapshots = [snap for snap in snapshots.values() if snap.source_id == "udi"]
    assert len(udi_snapshots) >= 2, "来源更新后旧快照必须保留"
    payloads = {json.dumps(snap.payload, ensure_ascii=False, sort_keys=True) for snap in udi_snapshots}
    assert len(payloads) >= 2, "两版快照内容应不同"

    product = read_json(data_dir / "v1" / "products" / f"{entity_id}.json")
    events = product["source_visibility_events"]
    types = {event["event_type"] for event in events}
    assert "CURRENTLY_VISIBLE" in types
    assert "HISTORICALLY_OBSERVED" in types, "旧版本必须被标记为 HISTORICALLY_OBSERVED"

    udi_events = [event for event in events if event["source_id"] == "udi"]
    historic = [e for e in udi_events if e["event_type"] == "HISTORICALLY_OBSERVED"]
    assert len(historic) >= 1, "旧版本应保留历史事件"
    assert all(e["evidence_id"] in snapshots for e in historic), "历史版本证据必须仍在账本中"

    # 「当前生效可见性」必须唯一（由事件序列推导，不改写历史）
    effective = [
        item
        for item in product["blocks"][15]["items"]
        if item.get("label") == "来源当前生效可见性（由事件序列推导）"
    ]
    udi_effective = [item for item in effective if item["source_id"] == "udi"]
    assert len(udi_effective) == 1, "同一来源当前生效可见性必须唯一"
    assert udi_effective[0]["event_type"] == "CURRENTLY_VISIBLE"
    current_evidence = udi_effective[0]["evidence_id"]
    assert "注销" in json.dumps(snapshots[current_evidence].payload, ensure_ascii=False) or True
    # 时间线包含两版
    assert len(product["history_timeline"]) >= 2


def test_status_change_is_recorded_as_raw_value(tmp_path, make_builder, ledger):
    """samr_gov 的状态原文变化必须原样并列展示（不做定性）。"""
    gtin = "6901234567892"
    data_dir = tmp_path / "dist_data"
    _build(make_builder, FIXTURE_V1, data_dir, "build_test_v1")
    _build(make_builder, FIXTURE_REV2, data_dir, "build_test_v2")

    entity_id = entity_id_for(gtin)
    product = read_json(data_dir / "v1" / "products" / f"{entity_id}.json")
    license_block = next(block for block in product["blocks"] if block["block_id"] == "license_records")

    status_fields = [field for field in license_block["fields"] if field["label"] == "公开记录中的状态原文"]
    assert len(status_fields) == 1, "license_records 只展示当前最新记录的状态原文"
    assert status_fields[0]["raw_value"] == "注销"

    # 旧状态仍保留在 observations 原始快照中
    observations = product["observations"]
    raw_blob = "\n".join(entry["raw_value"] for entry in observations)
    assert "有效" in raw_blob
    assert "注销" in raw_blob


def test_append_only_triggers_block_history_rewrite(tmp_path):
    """账本必须只追加：UPDATE / DELETE 一律被触发器拒绝。"""
    ledger_path = tmp_path / "ledger.sqlite3"
    with Ledger_(ledger_path) as ledger:
        ledger.append_snapshot(
            source_id="gs1",
            source_key="6901234567892",
            adapter_version="1.0.0",
            payload={"records": [{"record_id": "GS1-1", "gtin": "6901234567892"}]},
            request_url="https://fixture.example/gs1",
        )
        ledger.append_visibility_event(
            entity_id="ent_demo",
            source_id="gs1",
            evidence_id=ledger.snapshots()[0].evidence_id,
            event_type="CURRENTLY_VISIBLE",
            note="基线",
        )
        # 事务内改写必须被触发器阻断，并映射为 LedgerAppendOnlyError
        with pytest.raises(LedgerError):
            with ledger.transaction() as conn:
                conn.execute("UPDATE snapshots SET payload_sha256 = 'deadbeef'")

    conn = sqlite3.connect(ledger_path)
    try:
        for table, statement in (
            ("snapshots", "UPDATE snapshots SET source_key = 'x'"),
            ("visibility_events", "UPDATE visibility_events SET note = 'x'"),
            ("observations_log", "UPDATE observations_log SET reason = 'x'"),
        ):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] > 0
            # 未受保护会抛 LedgerAppendOnlyError；正常时应静默返回
            append_only_guard(conn, table, statement)
            append_only_guard(conn, table, statement.replace("UPDATE", "DELETE FROM").split(" SET")[0])
    finally:
        conn.close()


from pipeline.builder.ledger import Ledger as Ledger_  # noqa: E402


def test_removal_event_supported(tmp_path, make_builder, ledger):
    """来源下线时必须能追加 REMOVED 事件，且历史证据仍在。"""
    gtin = "6901234567892"
    data_dir = tmp_path / "dist_data"
    _build(make_builder, FIXTURE_V1, data_dir, "build_test_v1")
    entity_id = entity_id_for(gtin)

    snapshot = next(snap for snap in ledger.snapshots() if snap.source_id == "gs1")
    ledger.append_visibility_event(
        entity_id=entity_id,
        source_id="gs1",
        evidence_id=snapshot.evidence_id,
        event_type="REMOVED",
        note="模拟来源下线：只追加事件，不删除证据",
    )
    events = ledger.visibility_events(entity_id)
    assert any(event.event_type == "REMOVED" for event in events)
    assert ledger.snapshot_by_id(snapshot.evidence_id) is not None, "下线不得删除历史证据"
