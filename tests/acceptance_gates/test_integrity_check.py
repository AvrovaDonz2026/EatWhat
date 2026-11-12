"""门禁 9：完整性校验（账本只追加 + SHA-256 内容寻址 + 产物校验）。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from pipeline.builder.ledger import (
    Ledger,
    LedgerAppendOnlyError,
    LedgerError,
    canonical_json,
    sha256_of,
)
from tests.acceptance_gates.conftest import read_json


def test_snapshot_hash_is_content_addressed(built):
    ledger = built["ledger"]
    snapshots = ledger.snapshots()
    assert snapshots
    for snapshot in snapshots:
        expected = sha256_of(canonical_json(snapshot.payload))
        assert snapshot.payload_sha256 == expected, f"{snapshot.evidence_id}: 内容哈希不匹配原始 payload"
        assert len(snapshot.payload_sha256) == 64
        assert all(ch in "0123456789abcdef" for ch in snapshot.payload_sha256)


def test_same_content_reuses_evidence_id(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    try:
        payload = {"records": [{"record_id": "X-1", "gtin": "6901234567892"}]}
        first = ledger.append_snapshot(
            source_id="gs1", source_key="6901234567892", adapter_version="1.0.0",
            payload=payload, request_url="https://fixture.example/gs1",
        )
        second = ledger.append_snapshot(
            source_id="gs1", source_key="6901234567892", adapter_version="1.0.0",
            payload=payload, request_url="https://fixture.example/gs1",
        )
        assert first.evidence_id == second.evidence_id, "相同内容必须复用同一 evidence_id（内容寻址）"
        assert second.is_new is False
        assert len(ledger.snapshots()) == 1, "重复内容不得产生重复快照"
    finally:
        ledger.close()


def test_different_content_creates_new_evidence(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    try:
        a = ledger.append_snapshot(
            source_id="gs1", source_key="6901234567892", adapter_version="1.0.0",
            payload={"records": [{"record_id": "A"}]}, request_url="https://x/1",
        )
        b = ledger.append_snapshot(
            source_id="gs1", source_key="6901234567892", adapter_version="1.0.0",
            payload={"records": [{"record_id": "B"}]}, request_url="https://x/2",
        )
        assert a.evidence_id != b.evidence_id
        assert b.seq > a.seq, "新快照必须获得更大的账本序号"
        assert len(ledger.snapshots()) == 2
    finally:
        ledger.close()


def test_ledger_triggers_survive_reopen(tmp_path):
    """触发器必须持久化在数据库内，重新打开后仍然有效。"""
    path = tmp_path / "ledger.sqlite3"
    ledger = Ledger(path)
    ledger.append_snapshot(
        source_id="gs1", source_key="k", adapter_version="1.0.0",
        payload={"records": [{"record_id": "A"}]}, request_url="https://x/1",
    )
    ledger.close()

    conn = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM snapshots")
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE snapshots SET payload_sha256='x'")
        conn.rollback()
        assert conn.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 1
    finally:
        conn.close()


def test_tampered_snapshot_detected(tmp_path):
    """篡改 payload_json 后，哈希校验必须能识别。"""
    path = tmp_path / "ledger.sqlite3"
    ledger = Ledger(path)
    snapshot = ledger.append_snapshot(
        source_id="gs1", source_key="k", adapter_version="1.0.0",
        payload={"records": [{"record_id": "A"}]}, request_url="https://x/1",
    )
    ledger.close()

    # 直接绕过触发器（模拟外部篡改数据库文件），再检测
    conn = sqlite3.connect(path)
    conn.execute("DROP TRIGGER trg_snapshots_no_update")
    conn.execute(
        "UPDATE snapshots SET payload_json = ? WHERE evidence_id = ?",
        (json.dumps({"records": [{"record_id": "TAMPERED"}]}), snapshot.evidence_id),
    )
    conn.commit()
    conn.close()

    reopened = Ledger(path)
    try:
        with pytest.raises(LedgerError):
            reopened.verify_integrity()
    finally:
        reopened.close()


def test_builder_verifies_artifacts_on_write(built):
    ledger = built["ledger"]
    artifacts = ledger.artifacts()
    assert artifacts
    data_dir: Path = built["data_dir"]
    for row in artifacts:
        path = data_dir / row["path"]
        assert path.exists()
        assert sha256_of(path.read_text(encoding="utf-8").encode("utf-8")) == row["sha256"]


def test_manifest_declares_integrity_fields(built):
    data_dir: Path = built["data_dir"]
    manifest = read_json(data_dir / "v1" / "manifest.json")
    assert manifest["build_id"]
    assert manifest["registry_version"]
    assert manifest["adapter_versions"]
    for entry in manifest["query_index"]:
        for key, relative in entry["paths"].items():
            assert (data_dir / relative).exists(), f"{key} 产物缺失"

    for path in (data_dir / "v1" / "products").glob("*.json"):
        payload = read_json(path)
        for observation in payload["observations"]:
            assert len(observation["payload_sha256"]) == 64


def test_evidence_id_is_deterministic():
    """evidence_id 必须由内容决定，保证跨机器复现一致。"""
    payload = {"records": [{"record_id": "A", "gtin": "6901234567892"}]}
    digest = sha256_of(canonical_json(payload))
    assert len(digest) == 64
    assert digest == sha256_of(canonical_json(payload))


def test_append_only_error_type(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    try:
        ledger.append_snapshot(
            source_id="gs1", source_key="k", adapter_version="1.0.0",
            payload={"records": [{"record_id": "A"}]}, request_url="https://x/1",
        )
        with pytest.raises(LedgerAppendOnlyError):
            with ledger.transaction() as conn:
                conn.execute("UPDATE snapshots SET source_key='y'")
    finally:
        ledger.close()
