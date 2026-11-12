"""只追加账本（SQLite）+ SHA-256 content_hash。

红线：
- 账本只允许 INSERT；任何 UPDATE / DELETE 都会被触发器拒绝（禁止覆盖历史）。
- 每条证据忠实地保存原始抓取快照（payload_json 原文），并绑定 SHA-256。
- 写入前做凭据脱敏检查；授权头、token、appId 一律不得落盘。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

SCHEMA_VERSION = "1.0.0"

#: 允许出现在账本里的键名（其余一律视为敏感键）
_SAFE_KEY_ALLOWLIST = {
    "source_id",
    "source_key",
    "adapter_version",
    "request_url",
    "request_headers",
    "payload",
    "records",
    "note",
    "gtin",
    "udi_di",
    "standard_no",
    "brand",
    "q",
}

_SENSITIVE_KEY = re.compile(
    r"(token|secret|password|passwd|authorization|api[_-]?key|app[_-]?id|appid|bearer|credential|cookie|session)",
    re.IGNORECASE,
)
#: 强凭据特征：无论出现在哪个字段都必须拦截
_HARD_SECRET = re.compile(
    r"(sk_live|AKIA[0-9A-Z]{4,}|ghp_[A-Za-z0-9]{8,}|eyJ[A-Za-z0-9_\-]{10,}|Bearer\s+[A-Za-z0-9\._\-]{8,})"
)
#: 形似高熵串；仅在非哈希字段出现时视为凭据
_HEX_LIKE_SECRET = re.compile(r"[A-Fa-f0-9]{32,}")
_URL_SECRET = re.compile(r"(token|key|secret|appid|app_id|signature|sig|credential)=([^&]+)", re.IGNORECASE)
#: 允许承载 SHA-256 摘要的字段名（内容哈希不是凭据）
_HASH_KEY_ALLOWLIST = {
    "sha256",
    "payload_sha256",
    "content_hash",
    "artifact_sha256",
    "digest",
    "hash",
    "checksum",
    "zxing_wasm_sha256",
    "dataset_sha256",
}


class LedgerError(RuntimeError):
    """账本完整性或脱敏校验失败。"""


class LedgerAppendOnlyError(LedgerError):
    """试图改写历史记录。"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    """用于哈希计算的稳定序列化。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_of(value: Any) -> str:
    raw = value if isinstance(value, (bytes, str)) else canonical_json(value)
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def mask_url(url: str) -> str:
    return _URL_SECRET.sub(lambda m: f"{m.group(1)}=***", url)


def _contains_secret_text(text: str) -> bool:
    """文本层面的凭据探测（不含哈希白名单判断）。"""
    if _HARD_SECRET.search(text):
        return True
    if _URL_SECRET.search(text):
        return True
    return bool(_HEX_LIKE_SECRET.search(text))


def _mask_value(value: str) -> str:
    masked = mask_url(value)
    masked = _HARD_SECRET.sub("***", masked)
    if masked != value and not _URL_SECRET.search(masked):
        pass
    return masked


def redact(value: Any, path: str = "$", *, key: str = "") -> Any:
    """递归脱敏：敏感键整项替换为 <redacted>，敏感值替换为 ***。

    哈希字段（见 _HASH_KEY_ALLOWLIST）允许承载 SHA-256 摘要。
    """
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for child_key, item in value.items():
            child_path = f"{path}.{child_key}"
            if _SENSITIVE_KEY.search(str(child_key)) and str(child_key).lower() not in _SAFE_KEY_ALLOWLIST:
                out[child_key] = "<redacted>"
            else:
                out[child_key] = redact(item, child_path, key=str(child_key))
        return out
    if isinstance(value, (list, tuple)):
        return [redact(item, f"{path}[{i}]", key=key) for i, item in enumerate(value)]
    if isinstance(value, str):
        if key.lower() in _HASH_KEY_ALLOWLIST and _HEX_LIKE_SECRET.fullmatch(value.strip()):
            return value
        return _mask_value(value)
    return value


def assert_no_credentials(blob: Any, where: str) -> None:
    """在写盘前做最后一道凭据检查（按字段名区分哈希与凭据）。"""
    if isinstance(blob, str):
        # 若为 JSON 文本，先解析后按结构化规则校验，避免把 64 位 SHA-256 误判为凭据
        stripped = blob.lstrip()
        if stripped.startswith(("{", "[")):
            try:
                blob = json.loads(blob)
            except json.JSONDecodeError:
                blob = blob
        if isinstance(blob, str):
            if _contains_secret_text(blob):
                raise LedgerError(f"{where}: 检测到疑似凭据字面值，拒绝落盘")
            return

    def walk(node: Any, key: str) -> None:
        if isinstance(node, dict):
            for k, item in node.items():
                if _SENSITIVE_KEY.search(str(k)) and str(k).lower() not in _SAFE_KEY_ALLOWLIST:
                    raise LedgerError(f"{where}: 敏感字段 {k} 禁止落盘")
                walk(item, str(k))
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item, key)
            return
        if isinstance(node, str):
            if key.lower() in _HASH_KEY_ALLOWLIST and _HEX_LIKE_SECRET.fullmatch(node.strip()):
                return
            if _contains_secret_text(node):
                raise LedgerError(f"{where}: 字段 {key} 含疑似凭据字面值，拒绝落盘")
        return

    walk(blob, "")


@dataclass(frozen=True)
class SnapshotRecord:
    """账本快照。seq 为账本内单调递增序号，用于稳定判定「最新」。"""
    seq: int
    evidence_id: str
    source_id: str
    source_key: str
    adapter_version: str
    payload: Dict[str, Any]
    payload_sha256: str
    retrieved_at: str
    request_url: str
    request_params: Dict[str, Any]
    is_new: bool


@dataclass(frozen=True)
class VisibilityEvent:
    """可见性事件。seq 为账本内单调递增序号（不可变、只追加）。"""
    seq: int
    event_id: str
    entity_id: str
    source_id: str
    evidence_id: str
    event_type: str
    recorded_at: str
    note: str


VISIBILITY_EVENT_TYPES = ("CURRENTLY_VISIBLE", "HISTORICALLY_OBSERVED", "REMOVED")


ledger_schema_sql = """
CREATE TABLE IF NOT EXISTS ledger_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS snapshots (
    seq            INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_id    TEXT NOT NULL,
    source_id      TEXT NOT NULL,
    source_key     TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    retrieved_at   TEXT NOT NULL,
    request_url    TEXT NOT NULL,
    request_params TEXT NOT NULL,
    payload_json   TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS ux_snapshots_evidence ON snapshots(evidence_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_snapshots_identity
    ON snapshots(source_id, source_key, adapter_version, payload_sha256);

CREATE TABLE IF NOT EXISTS observations_log (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    reason      TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS visibility_events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    note        TEXT NOT NULL
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS ux_visibility_event ON visibility_events(event_id);
CREATE INDEX IF NOT EXISTS ix_visibility_entity ON visibility_events(entity_id, seq);

CREATE TABLE IF NOT EXISTS build_runs (
    build_id         TEXT PRIMARY KEY,
    registry_version TEXT NOT NULL,
    adapter_versions TEXT NOT NULL,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    tool_version     TEXT NOT NULL,
    note             TEXT NOT NULL DEFAULT ''
) STRICT;

CREATE TABLE IF NOT EXISTS artifacts (
    seq      INTEGER PRIMARY KEY AUTOINCREMENT,
    build_id TEXT NOT NULL,
    path     TEXT NOT NULL,
    sha256   TEXT NOT NULL,
    bytes    INTEGER NOT NULL,
    written_at TEXT NOT NULL
) STRICT;

-- 只追加：任何改写历史的行为都直接失败
CREATE TRIGGER IF NOT EXISTS trg_snapshots_no_update BEFORE UPDATE ON snapshots
BEGIN SELECT RAISE(ABORT, 'snapshots is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_snapshots_no_delete BEFORE DELETE ON snapshots
BEGIN SELECT RAISE(ABORT, 'snapshots is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_visibility_no_update BEFORE UPDATE ON visibility_events
BEGIN SELECT RAISE(ABORT, 'visibility_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_visibility_no_delete BEFORE DELETE ON visibility_events
BEGIN SELECT RAISE(ABORT, 'visibility_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_artifacts_no_update BEFORE UPDATE ON artifacts
BEGIN SELECT RAISE(ABORT, 'artifacts is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_artifacts_no_delete BEFORE DELETE ON artifacts
BEGIN SELECT RAISE(ABORT, 'artifacts is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_observations_no_update BEFORE UPDATE ON observations_log
BEGIN SELECT RAISE(ABORT, 'observations_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_observations_no_delete BEFORE DELETE ON observations_log
BEGIN SELECT RAISE(ABORT, 'observations_log is append-only'); END;
"""


class Ledger:
    """追加写账本。所有写入都带 SHA-256；历史不可改写。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(ledger_schema_sql)
        self._conn.execute(
            "INSERT OR IGNORE INTO ledger_meta(key, value) VALUES('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        self._conn.commit()

    # ---------------------------------------------------------------- lifecycle
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            if "append-only" in str(exc):
                raise LedgerAppendOnlyError(str(exc)) from exc
            raise
        except Exception:
            self._conn.rollback()
            raise

    # ------------------------------------------------------------------ writers
    def _seq_for(self, evidence_id: str) -> int:
        row = self._conn.execute(
            "SELECT seq FROM snapshots WHERE evidence_id = ?", (evidence_id,)
        ).fetchone()
        return int(row["seq"]) if row else 0

    def append_snapshot(
        self,
        *,
        source_id: str,
        source_key: str,
        adapter_version: str,
        payload: Dict[str, Any],
        request_url: str,
        request_params: Dict[str, Any] | None = None,
        retrieved_at: str | None = None,
    ) -> SnapshotRecord:
        """写入一份原始快照。

        完全等价的快照（同源、同键、同适配器版本、同 content_hash）不会重复落盘，
        而是追加一条 observations_log 观测记录，并返回既有 evidence_id。
        """
        if not source_id or not source_key or not adapter_version:
            raise LedgerError("source_id / source_key / adapter_version 均不得为空")
        memory = payload if isinstance(payload, (dict, list)) else {"value": payload}
        assert_no_credentials(memory, f"snapshot:{source_id}/{source_key}")

        payload_json = canonical_json(memory)
        payload_sha256 = sha256_of(payload_json)
        retrieved = retrieved_at or utc_now()
        request_url_masked = mask_url(request_url)
        params_json = canonical_json(redact(request_params or {}))
        evidence_id = "ev_" + sha256_of(
            {"s": source_id, "k": source_key, "v": adapter_version, "h": payload_sha256}
        )[:20]

        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT evidence_id FROM snapshots WHERE evidence_id = ?", (evidence_id,)
            ).fetchone()
            is_new = existing is None
            if is_new:
                conn.execute(
                    """
                    INSERT INTO snapshots
                        (evidence_id, source_id, source_key, adapter_version,
                         retrieved_at, request_url, request_params, payload_json, payload_sha256)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evidence_id,
                        source_id,
                        source_key,
                        adapter_version,
                        retrieved,
                        request_url_masked,
                        params_json,
                        payload_json,
                        payload_sha256,
                    ),
                )
            conn.execute(
                "INSERT INTO observations_log(evidence_id, observed_at, reason) VALUES (?, ?, ?)",
                (
                    evidence_id,
                    retrieved,
                    "first_observation" if is_new else "re_observation_same_content",
                ),
            )

        seq = self._seq_for(evidence_id)
        return SnapshotRecord(
            seq=seq,
            evidence_id=evidence_id,
            source_id=source_id,
            source_key=source_key,
            adapter_version=adapter_version,
            payload=memory,
            payload_sha256=payload_sha256,
            retrieved_at=retrieved,
            request_url=request_url_masked,
            request_params=json.loads(params_json),
            is_new=is_new,
        )

    def append_visibility_event(
        self,
        *,
        entity_id: str,
        source_id: str,
        evidence_id: str,
        event_type: str,
        note: str = "",
        recorded_at: str | None = None,
    ) -> VisibilityEvent:
        if event_type not in VISIBILITY_EVENT_TYPES:
            raise LedgerError(f"未登记的 source_visibility_event: {event_type}")
        recorded = recorded_at or utc_now()
        # event_id 必须纯内容寻址：不掺入采集时间，否则同一账本状态下重跑构建会得到
        # 不同的 id（跨秒即失败），破坏 test_query_reproducibility 的可复现语义。
        # 重复的同内容事件由 INSERT OR IGNORE 去重，无需靠时间戳区分。
        event_id = "vis_" + sha256_of(
            {"e": entity_id, "s": source_id, "v": evidence_id, "t": event_type}
        )[:20]
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO visibility_events
                    (event_id, entity_id, source_id, evidence_id, event_type, recorded_at, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, entity_id, source_id, evidence_id, event_type, recorded, note),
            )
            row = conn.execute(
                "SELECT seq FROM visibility_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return VisibilityEvent(
            seq=int(row["seq"]) if row else 0,
            event_id=event_id,
            entity_id=entity_id,
            source_id=source_id,
            evidence_id=evidence_id,
            event_type=event_type,
            recorded_at=recorded,
            note=note,
        )

    def begin_build(self, *, build_id: str, registry_version: str, adapter_versions: Dict[str, str], tool_version: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO build_runs(build_id, registry_version, adapter_versions, started_at, tool_version)
                VALUES (?, ?, ?, ?, ?)
                """,
                (build_id, registry_version, canonical_json(adapter_versions), utc_now(), tool_version),
            )

    def finish_build(self, *, build_id: str, note: str = "") -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE build_runs SET finished_at = ?, note = ? WHERE build_id = ? AND finished_at IS NULL",
                (utc_now(), note, build_id),
            )

    def record_artifact(self, *, build_id: str, path: str, sha256: str, bytes_: int) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO artifacts(build_id, path, sha256, bytes, written_at) VALUES (?, ?, ?, ?, ?)",
                (build_id, path, sha256, bytes_, utc_now()),
            )

    # ------------------------------------------------------------------ readers
    def snapshots(self) -> List[SnapshotRecord]:
        rows = self._conn.execute(
            "SELECT * FROM snapshots ORDER BY seq"
        ).fetchall()
        return [
            SnapshotRecord(
                seq=int(row["seq"]),
                evidence_id=row["evidence_id"],
                source_id=row["source_id"],
                source_key=row["source_key"],
                adapter_version=row["adapter_version"],
                payload=json.loads(row["payload_json"]),
                payload_sha256=row["payload_sha256"],
                retrieved_at=row["retrieved_at"],
                request_url=row["request_url"],
                request_params=json.loads(row["request_params"]),
                is_new=False,
            )
            for row in rows
        ]

    def snapshot_by_id(self, evidence_id: str) -> Optional[SnapshotRecord]:
        row = self._conn.execute(
            "SELECT * FROM snapshots WHERE evidence_id = ?", (evidence_id,)
        ).fetchone()
        if row is None:
            return None
        return SnapshotRecord(
            seq=int(row["seq"]),
            evidence_id=row["evidence_id"],
            source_id=row["source_id"],
            source_key=row["source_key"],
            adapter_version=row["adapter_version"],
            payload=json.loads(row["payload_json"]),
            payload_sha256=row["payload_sha256"],
            retrieved_at=row["retrieved_at"],
            request_url=row["request_url"],
            request_params=json.loads(row["request_params"]),
            is_new=False,
        )

    def visibility_events(self, entity_id: str | None = None) -> List[VisibilityEvent]:
        if entity_id is None:
            rows = self._conn.execute("SELECT * FROM visibility_events ORDER BY seq").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM visibility_events WHERE entity_id = ? ORDER BY seq", (entity_id,)
            ).fetchall()
        return [
            VisibilityEvent(
                seq=int(r["seq"]),
                event_id=r["event_id"],
                entity_id=r["entity_id"],
                source_id=r["source_id"],
                evidence_id=r["evidence_id"],
                event_type=r["event_type"],
                recorded_at=r["recorded_at"],
                note=r["note"],
            )
            for r in rows
        ]

    def build_runs(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM build_runs ORDER BY started_at").fetchall()
        return [dict(row) for row in rows]

    def artifacts(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM artifacts ORDER BY seq").fetchall()
        return [dict(row) for row in rows]

    def verify_integrity(self) -> Dict[str, Any]:
        """校验账本完整性；发现被篡改的快照直接抛错。"""
        report = self.integrity_report()
        if report["hash_mismatches"]:
            raise LedgerError(
                "账本完整性校验失败：以下证据的 payload 与记录的 SHA-256 不一致 "
                f"{report['hash_mismatches']}"
            )
        return report

    def integrity_report(self) -> Dict[str, Any]:
        """独立重算 content_hash，验证账本未被篡改。"""
        snapshots = self.snapshots()
        mismatches = [
            s.evidence_id
            for s in snapshots
            if sha256_of(canonical_json(s.payload)) != s.payload_sha256
        ]
        return {
            "schema_version": self._conn.execute(
                "SELECT value FROM ledger_meta WHERE key='schema_version'"
            ).fetchone()[0],
            "snapshots": len(snapshots),
            "visibility_events": len(self.visibility_events()),
            "artifacts": len(self.artifacts()),
            "hash_mismatches": mismatches,
            "ok": not mismatches,
        }


def append_only_guard(conn: sqlite3.Connection, table: str, sql: str) -> None:
    """测试辅助：执行一条会改写历史的语句并确认被触发器阻断。"""
    try:
        conn.execute(sql)
        conn.commit()
    except sqlite3.IntegrityError as exc:  # RAISE(ABORT) 以 IntegrityError 形式冒泡
        if "append-only" not in str(exc):
            raise LedgerAppendOnlyError(f"{table} 未被只追加触发器保护: {exc}") from exc
        return
    raise LedgerAppendOnlyError(f"{table} 允许改写历史（未受保护）")


def iter_sorted(items: Iterable[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    return sorted(items, key=lambda item: str(item.get(key, "")))


def gtin_checksum_ok(gtin: str) -> bool:
    """GTIN-8/12/13/14 校验位（Mod-10）。"""
    digits = [c for c in gtin if c.isdigit()]
    if len(digits) not in (8, 12, 13, 14):
        return False
    body = digits[:-1]
    check = int(digits[-1])
    total = 0
    for index, ch in enumerate(reversed(body)):
        weight = 3 if index % 2 == 0 else 1
        total += int(ch) * weight
    return (10 - (total % 10)) % 10 == check


def gtin_check_digit(body: str) -> int:
    total = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(body)))
    return (10 - (total % 10)) % 10


__all__ = [
    "Ledger",
    "LedgerError",
    "LedgerAppendOnlyError",
    "SnapshotRecord",
    "VisibilityEvent",
    "VISIBILITY_EVENT_TYPES",
    "append_only_guard",
    "assert_no_credentials",
    "canonical_json",
    "gtin_checksum_ok",
    "gtin_check_digit",
    "mask_url",
    "redact",
    "sha256_of",
    "utc_now",
]
