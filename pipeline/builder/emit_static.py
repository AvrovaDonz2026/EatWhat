"""离线静态 JSON 编译器。

输出：
  dist_data/v1/manifest.json
  dist_data/v1/gtin/{gtin}.json
  dist_data/v1/products/{entity_id}.json
  dist_data/v1/ingredients/{entity_id}.json
  dist_data/v1/demo/index.json        （端侧离线兜底样例索引）

并且把每个产物的 SHA-256 记入账本 artifacts 表。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from pipeline.adapters.base import (
    AdapterError,
    LocalFixtureTransport,
    PolicyBlocked,
    SourceFormatChanged,
    adapter_registry_map,
)
from pipeline.adapters.v1 import ADAPTERS
from pipeline.builder.dossier import (
    BLOCK_IDS,
    LINK_EXACT,
    LINK_MANUAL,
    assemble_blocks,
    build_candidates,
    coverage_manifest,
)
from pipeline.builder.ledger import (
    Ledger,
    SnapshotRecord,
    assert_no_credentials,
    canonical_json,
    sha256_of,
    utc_now,
)
from pipeline.config.policy import SourceRegistry, load_registry

def _norm_digits(value: str) -> str:
    return "".join(ch for ch in str(value) if ch.isdigit())


TOOL_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = ROOT / "dist_data"
DEFAULT_LEDGER = ROOT / ".state" / "ledger.sqlite3"
DEFAULT_FIXTURES = ROOT / "pipeline" / "fixtures" / "v1"

IDENTIFIER_TYPE_BY_SOURCE = {
    "udi": "GTIN",
    "gs1": "GTIN",
    "label_transcript": "GTIN",
    "samr_gov": "GTIN",
    "samr_std": "STANDARD_NO",
    "cnipa": "BRAND_NAME",
    "cifer": "CIFER_NO",
}

_GTIN_RE = re.compile(r"[0-9]{8,14}")
_LEGAL_STATUS_FIELD_HINTS = {
    "registration_status",
    "license_status",
    "status",
}


def entity_id_for(gtin: str) -> str:
    return "ent_" + sha256_of({"gtin": gtin})[:16]


def _write_json(path: Path, payload: Any) -> str:
    assert_no_credentials(payload, str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    path.write_text(text, encoding="utf-8")
    return sha256_of(text.encode("utf-8"))


@dataclass
class BuildOutcome:
    build_id: str
    manifest_path: Path
    artifacts: List[str]
    gtin_files: Dict[str, Path] = field(default_factory=dict)
    product_files: Dict[str, Path] = field(default_factory=dict)
    ingredient_files: Dict[str, Path] = field(default_factory=dict)
    failures: Dict[str, str] = field(default_factory=dict)


class Builder:
    def __init__(
        self,
        *,
        registry: SourceRegistry,
        ledger: Ledger,
        data_dir: Path,
        fixtures_root: Path,
        sleeper=None,
        rng=None,
        probe_mode: bool = False,
    ):
        self.registry = registry
        self.ledger = ledger
        self.data_dir = Path(data_dir)
        self.fixtures_root = Path(fixtures_root)
        self.sleeper = sleeper
        self.rng = rng
        self.probe_mode = probe_mode
        self.transport = LocalFixtureTransport(self.fixtures_root)

    # ------------------------------------------------------------------ helpers
    def _adapter(self, source_id: str):
        cls = ADAPTERS[source_id]
        kwargs: Dict[str, Any] = {}
        if self.sleeper is not None:
            kwargs["sleeper"] = self.sleeper
        if self.rng is not None:
            kwargs["rng"] = self.rng
        return cls(self.registry, self.transport, **kwargs)

    def _identifier_for(self, source_id: str, gtin: str, hints: Dict[str, str]) -> Optional[str]:
        id_type = IDENTIFIER_TYPE_BY_SOURCE.get(source_id, "GTIN")
        if id_type == "GTIN":
            return gtin
        return hints.get(source_id)

    # -------------------------------------------------------------------- build
    def build(
        self,
        *,
        gtins: Sequence[str],
        description_hints: Optional[Dict[str, Dict[str, str]]] = None,
        build_id: Optional[str] = None,
        note: str = "",
    ) -> BuildOutcome:
        hints = description_hints or {}
        build_id = build_id or "build_" + utc_now().replace("-", "").replace(":", "").replace("T", "_").replace("Z", "Z")
        adapter_versions = {sid: cls.adapter_version for sid, cls in ADAPTERS.items()}

        self.ledger.begin_build(
            build_id=build_id,
            registry_version=self.registry.registry_version,
            adapter_versions=adapter_versions,
            tool_version=TOOL_VERSION,
        )

        artifacts: List[str] = []
        gtin_files: Dict[str, Path] = {}
        product_files: Dict[str, Path] = {}
        ingredient_files: Dict[str, Path] = {}
        failures: Dict[str, str] = {}

        runnable = [p.source_id for p in self.registry.all() if p.runnable]
        entries_by_gtin: Dict[str, List[SnapshotRecord]] = {}

        for gtin in gtins:
            observed: List[SnapshotRecord] = []
            gtin_hints = hints.get(gtin, {})
            for source_id in runnable:
                identifier = self._identifier_for(source_id, gtin, gtin_hints)
                if not identifier:
                    continue
                adapter = self._adapter(source_id)
                try:
                    result = adapter.fetch(identifier)
                except (PolicyBlocked, SourceFormatChanged, AdapterError) as exc:
                    failures[f"{gtin}:{source_id}"] = f"{exc.code}: {exc}"
                    continue

                # 零记录 = 本次查询未取得记录，不产生证据快照（避免用空记录覆盖历史）
                payload_records = result["payload"].get("records") if isinstance(result["payload"], dict) else None
                if isinstance(payload_records, list) and len(payload_records) == 0:
                    continue

                snap = self.ledger.append_snapshot(
                    source_id=source_id,
                    source_key=identifier,
                    adapter_version=result["adapter_version"],
                    payload=result["payload"],
                    request_url=result["request_url"],
                    request_params=result["request_params"],
                )
                observed.append(snap)

            entries_by_gtin[gtin] = observed

        # ---- 先把所有 GTIN 聚合起来（用于同条码多候选并列与历史时间线）
        all_snapshots = self.ledger.snapshots()
        snapshot_index = {s.evidence_id: s for s in all_snapshots}

        for gtin, observed in entries_by_gtin.items():
            ent_id = entity_id_for(gtin)
            candidates = build_candidates(gtin, observed)
            coverage = coverage_manifest(
                registry=self.registry,
                queried_sources=runnable,
                matched_sources={c.source_id for c in candidates},
                failures={
                    key.split(":", 1)[1]: value
                    for key, value in failures.items()
                    if key.startswith(f"{gtin}:")
                },
            )

            # 历史快照：同 entity 的所有来源快照按 source_key 归属，全部保留
            historic = self._historic_snapshots(gtin, ent_id, snapshot_index)
            visibility = self._visibility_events(ent_id, observed, historic)
            current = self._latest_by_source(historic)

            # ---- /v1/gtin/{gtin}.json
            gtin_payload = {
                "schema_version": SCHEMA_VERSION,
                "build_id": build_id,
                "built_at": utc_now(),
                "gtin": gtin,
                "entity_id": ent_id,
                "query_status": "QUERIED_MATCHED" if candidates else "QUERIED_NO_MATCH",
                "query_status_note": (
                    "当前已接入静态档案库已匹配到记录"
                    if candidates
                    else "QUERIED_NO_MATCH：当前已接入静态档案库未匹配到记录"
                ),
                "match_candidates": [c.as_json() for c in candidates],
                "coverage_manifest": coverage,
                "exact_match_count": sum(1 for c in candidates if c.link_mode == LINK_EXACT),
                "manual_confirm_count": sum(1 for c in candidates if c.link_mode == LINK_MANUAL),
                "no_auto_merge_notice": "候选仅并列展示；仅 EXACT_IDENTIFIER_MATCH 建立关联，其余需 MANUAL_CONFIRM",
            }
            path = self.data_dir / "v1" / "gtin" / f"{gtin}.json"
            digest = _write_json(path, gtin_payload)
            self.ledger.record_artifact(build_id=build_id, path=f"v1/gtin/{gtin}.json", sha256=digest, bytes_=path.stat().st_size)
            artifacts.append(f"v1/gtin/{gtin}.json")
            gtin_files[gtin] = path

            # ---- /v1/products/{entity_id}.json
            # 原始证据哈希取自每条快照的 payload_sha256（内容寻址，稳定可复现）；
            # 不嵌入产物自身哈希，避免产物内容依赖自身书写时间。
            artifact_hashes = {
                f"{snap.source_id}:{snap.evidence_id}": snap.payload_sha256 for snap in historic
            }
            blocks = assemble_blocks(
                gtin=gtin,
                candidates=candidates,
                snapshots=current,
                visibility=visibility,
                coverage=coverage,
                artifact_hashes=artifact_hashes,
                history=historic,
            )
            product_payload = {
                "schema_version": SCHEMA_VERSION,
                "build_id": build_id,
                "built_at": utc_now(),
                "entity_id": ent_id,
                "gtin": gtin,
                "observation_policy": {
                    "no_final_truth_field": True,
                    "no_auto_merge": True,
                    "observations_bound_to": ["source_id", "evidence_id", "retrieved_at", "raw_value"],
                },
                "coverage_manifest": coverage,
                "blocks": blocks.as_json(),
                "block_order": [bid for bid, _ in BLOCK_IDS],
                "observations": [_obs(s) for s in historic],
                "history_timeline": [
                    {
                        "evidence_id": s.evidence_id,
                        "source_id": s.source_id,
                        "retrieved_at": s.retrieved_at,
                        "payload_sha256": s.payload_sha256,
                        "adapter_version": s.adapter_version,
                    }
                    for s in sorted(historic, key=lambda s: s.seq)
                ],
                "source_visibility_events": visibility,
            }
            product_path = self.data_dir / "v1" / "products" / f"{ent_id}.json"
            digest = _write_json(product_path, product_payload)
            self.ledger.record_artifact(build_id=build_id, path=f"v1/products/{ent_id}.json", sha256=digest, bytes_=product_path.stat().st_size)
            artifacts.append(f"v1/products/{ent_id}.json")
            product_files[gtin] = product_path

            # ---- /v1/ingredients/{entity_id}.json
            ingredient_payload = self._ingredients_payload(
                ent_id=ent_id, gtin=gtin, build_id=build_id, snapshots=current
            )
            ing_path = self.data_dir / "v1" / "ingredients" / f"{ent_id}.json"
            digest = _write_json(ing_path, ingredient_payload)
            self.ledger.record_artifact(build_id=build_id, path=f"v1/ingredients/{ent_id}.json", sha256=digest, bytes_=ing_path.stat().st_size)
            artifacts.append(f"v1/ingredients/{ent_id}.json")
            ingredient_files[gtin] = ing_path

        # ---- /v1/manifest.json
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "build_id": build_id,
            "built_at": utc_now(),
            "registry_version": self.registry.registry_version,
            "tool_version": TOOL_VERSION,
            "adapter_versions": adapter_versions,
            "sources": [p.as_manifest_entry() for p in self.registry.all()],
            "coverage_summary": self._coverage_summary(),
            "query_index": [
                {
                    "gtin": gtin,
                    "entity_id": entity_id_for(gtin),
                    "paths": {
                        "gtin": f"v1/gtin/{gtin}.json",
                        "product": f"v1/products/{entity_id_for(gtin)}.json",
                        "ingredients": f"v1/ingredients/{entity_id_for(gtin)}.json",
                    },
                    "candidate_count": len(build_candidates(gtin, entries_by_gtin[gtin])),
                }
                for gtin in sorted(entries_by_gtin)
            ],
            "no_final_truth_field": True,
            "no_auto_merge": True,
            "zero_inference": True,
        }
        manifest_path = self.data_dir / "v1" / "manifest.json"
        digest = _write_json(manifest_path, manifest)
        self.ledger.record_artifact(build_id=build_id, path="v1/manifest.json", sha256=digest, bytes_=manifest_path.stat().st_size)
        artifacts.append("v1/manifest.json")

        demo_index_path = self._emit_demo_index(manifest, entries_by_gtin, build_id)
        artifacts.append("v1/demo/index.json")

        self.ledger.finish_build(build_id=build_id, note=note)

        return BuildOutcome(
            build_id=build_id,
            manifest_path=manifest_path,
            artifacts=artifacts,
            gtin_files=gtin_files,
            product_files=product_files,
            ingredient_files=ingredient_files,
            failures=failures,
        )

    # ------------------------------------------------------------------ internals
    def _historic_snapshots(
        self, gtin: str, ent_id: str, index: Dict[str, SnapshotRecord]
    ) -> List[SnapshotRecord]:
        """本 entity 的全部快照（含历史版本）。

        归属规则（纯机械，无相似度推断）：
        1. 账本中 source_key 与本条码相等；
        2. UDI 源的去前导零 DI 与本条码相等；
        3. 已通过 visibility 事件登记到本 entity 的证据。
        同一来源的多个版本全部保留，不覆盖。
        """
        out: List[SnapshotRecord] = []
        for ev in self.ledger.visibility_events(ent_id):
            snap = index.get(ev.evidence_id)
            if snap is not None and snap not in out:
                out.append(snap)
        for snap in index.values():
            if snap in out:
                continue
            key = _norm_digits(snap.source_key)
            if key == _norm_digits(gtin) or key.lstrip("0") == _norm_digits(gtin).lstrip("0"):
                out.append(snap)
        out.sort(key=lambda s: (s.source_id, s.seq))
        return out

    @staticmethod
    def _latest_by_source(historic: Sequence[SnapshotRecord]) -> List[SnapshotRecord]:
        """每源仅取最新版本（按账本序号，保证同一秒内也能正确判定）。"""
        latest: Dict[str, SnapshotRecord] = {}
        for snap in sorted(historic, key=lambda s: s.seq):
            latest[snap.source_id] = snap
        return [latest[sid] for sid in sorted(latest)]

    def _visibility_events(self, ent_id: str, observed: Sequence[SnapshotRecord], historic: Sequence[SnapshotRecord]) -> List[Dict[str, Any]]:
        """为每个快照追加可见性事件。

        规则（确定性、幂等、只追加）：
        - 每个来源的当前最新快照在该来源内只有一条 CURRENTLY_VISIBLE 事件；
          「当前最新」由账本新增序号决定，不看时间戳（避免同秒并列歧义）。
        - 若某快照从「当前最新」变为历史版本，则追加一条 HISTORICALLY_OBSERVED。
        - 绝不删除或改写历史事件。
        """
        latest_by_source: Dict[str, SnapshotRecord] = {}
        for snap in sorted(historic, key=lambda s: s.seq):
            latest_by_source[snap.source_id] = snap

        existing = self.ledger.visibility_events(ent_id)
        existing_keys = {(e.source_id, e.evidence_id, e.event_type) for e in existing}

        for snap in historic:
            latest = latest_by_source.get(snap.source_id)
            is_current = latest is not None and latest.evidence_id == snap.evidence_id
            if is_current:
                if (snap.source_id, snap.evidence_id, "CURRENTLY_VISIBLE") not in existing_keys:
                    self.ledger.append_visibility_event(
                        entity_id=ent_id,
                        source_id=snap.source_id,
                        evidence_id=snap.evidence_id,
                        event_type="CURRENTLY_VISIBLE",
                        note="本来源当前最新记录",
                    )
            else:
                if (snap.source_id, snap.evidence_id, "HISTORICALLY_OBSERVED") not in existing_keys:
                    self.ledger.append_visibility_event(
                        entity_id=ent_id,
                        source_id=snap.source_id,
                        evidence_id=snap.evidence_id,
                        event_type="HISTORICALLY_OBSERVED",
                        note="该来源已产生更新的快照，本快照按历史保留，不覆盖",
                    )

        # 若某来源旧快照已被更新的快照取代，则把旧快照的 CURRENTLY_VISIBLE 追加一条 HISTORICALLY_OBSERVED
        for event in existing:
            if event.event_type != "CURRENTLY_VISIBLE":
                continue
            current = latest_by_source.get(event.source_id)
            if current is not None and current.evidence_id != event.evidence_id:
                if (event.source_id, event.evidence_id, "HISTORICALLY_OBSERVED") not in existing_keys:
                    self.ledger.append_visibility_event(
                        entity_id=ent_id,
                        source_id=event.source_id,
                        evidence_id=event.evidence_id,
                        event_type="HISTORICALLY_OBSERVED",
                        note="该快照已被同一来源的更新版本取代，按历史保留",
                    )
                    existing_keys.add((event.source_id, event.evidence_id, "HISTORICALLY_OBSERVED"))

        out = []
        for ev in self.ledger.visibility_events(ent_id):
            out.append(
                {
                    "seq": ev.seq,
                    "event_id": ev.event_id,
                    "entity_id": ev.entity_id,
                    "source_id": ev.source_id,
                    "evidence_id": ev.evidence_id,
                    "event_type": ev.event_type,
                    "recorded_at": ev.recorded_at,
                    "note": ev.note,
                }
            )
        return out

    def _ingredients_payload(self, *, ent_id: str, gtin: str, build_id: str, snapshots: Sequence[SnapshotRecord]) -> Dict[str, Any]:
        from pipeline.builder.dossier import assemble_blocks
        from pipeline.builder.lexicon_terms import (
            canonical_nutrient,
            search_keys_for_allergen,
            search_keys_for_ingredient,
        )

        ingredients: List[Dict[str, Any]] = []
        nutrition: List[Dict[str, Any]] = []
        allergens: List[Dict[str, Any]] = []
        unsuitable: List[Dict[str, Any]] = []
        label_refs: List[Dict[str, Any]] = []

        for snap in snapshots:
            payload = snap.payload if isinstance(snap.payload, dict) else {}
            for record in payload.get("records", []) or []:
                if not isinstance(record, dict):
                    continue
                raw_ings = record.get("ingredients")
                if isinstance(raw_ings, list):
                    for idx, raw_name in enumerate(raw_ings, start=1):
                        ingredients.append(
                            {
                                "order": idx,
                                "raw_name": raw_name,
                                "search_keys": search_keys_for_ingredient(str(raw_name)),
                                "source_id": snap.source_id,
                                "evidence_id": snap.evidence_id,
                                "retrieved_at": snap.retrieved_at,
                                "raw_value": raw_name,
                                "record_id": record.get("record_id"),
                            }
                        )
                raw_nut = record.get("nutrition_facts")
                if isinstance(raw_nut, list):
                    for entry in raw_nut:
                        if isinstance(entry, dict):
                            nutrition.append(
                                {
                                    "nutrient": canonical_nutrient(str(entry.get("nutrient", ""))),
                                    "raw_nutrient": entry.get("nutrient", ""),
                                    "raw_value": entry.get("raw_value", ""),
                                    "evidence_id": snap.evidence_id,
                                    "source_id": snap.source_id,
                                    "retrieved_at": snap.retrieved_at,
                                }
                            )
                raw_allergens = record.get("allergens")
                if isinstance(raw_allergens, list):
                    for entry in raw_allergens:
                        if isinstance(entry, dict):
                            allergens.append(
                                {
                                    "raw_text": entry.get("raw_text", ""),
                                    "standard_ref": entry.get("standard_ref", ""),
                                    "search_keys": search_keys_for_allergen(str(entry.get("raw_text", ""))),
                                    "source_id": snap.source_id,
                                    "evidence_id": snap.evidence_id,
                                    "retrieved_at": snap.retrieved_at,
                                }
                            )
                raw_groups = record.get("unsuitable_groups")
                if isinstance(raw_groups, list):
                    for entry in raw_groups:
                        if isinstance(entry, dict):
                            unsuitable.append(
                                {
                                    "raw_text": entry.get("raw_text", ""),
                                    "source_id": snap.source_id,
                                    "evidence_id": snap.evidence_id,
                                    "retrieved_at": snap.retrieved_at,
                                }
                            )
                if record.get("label_standard_ref"):
                    label_refs.append(
                        {
                            "standard_no": record["label_standard_ref"],
                            "source_id": snap.source_id,
                            "evidence_id": snap.evidence_id,
                            "retrieved_at": snap.retrieved_at,
                        }
                    )

        return {
            "schema_version": SCHEMA_VERSION,
            "build_id": build_id,
            "built_at": utc_now(),
            "entity_id": ent_id,
            "gtin": gtin,
            "ingredients": ingredients,
            "nutrition_facts": nutrition,
            "explicit_allergens": allergens,
            "explicit_unsuitable_groups": unsuitable,
            "label_standard_refs": label_refs,
            "disclaimer": "本文件为纯客观配料与营养原文数据，供端侧机械词条对照；不含任何评价或建议",
        }

    def _coverage_summary(self) -> Dict[str, int]:
        summary = {mode: 0 for mode in ("OPEN_BULK", "OPEN_API", "ACCESS_PENDING", "SOURCE_UNAVAILABLE")}
        for policy in self.registry.all():
            summary[policy.access_mode] = summary.get(policy.access_mode, 0) + 1
        return summary

    def _emit_demo_index(
        self, manifest: Dict[str, Any], entries_by_gtin: Dict[str, List[SnapshotRecord]], build_id: str
    ) -> Path:
        """端侧无静态库时的本地兜底样例索引（含完整内联数据，便于单文件离线演示）。"""
        from pipeline.builder.dossier import assemble_blocks, build_candidates, coverage_manifest

        demo_entries: List[Dict[str, Any]] = []
        for gtin, observed in entries_by_gtin.items():
            if not observed:
                continue
            ent_id = entity_id_for(gtin)
            candidates = build_candidates(gtin, observed)
            coverage = coverage_manifest(
                registry=self.registry,
                queried_sources=[p.source_id for p in self.registry.all() if p.runnable],
                matched_sources={c.source_id for c in candidates},
            )
            blocks = assemble_blocks(
                gtin=gtin,
                candidates=candidates,
                snapshots=observed,
                visibility=self._visibility_events(ent_id, observed, observed),
                coverage=coverage,
                artifact_hashes={},
                history=observed,
            )
            demo_entries.append(
                {
                    "gtin": gtin,
                    "entity_id": ent_id,
                    "query_status": "QUERIED_MATCHED",
                    "match_candidates": [c.as_json() for c in candidates],
                    "coverage_manifest": coverage,
                    "blocks": blocks.as_json(),
                    "ingredients": self._ingredients_payload(
                        ent_id=ent_id, gtin=gtin, build_id=build_id, snapshots=observed
                    ),
                }
            )

        payload = {
            "schema_version": SCHEMA_VERSION,
            "build_id": build_id,
            "built_at": utc_now(),
            "note": "端侧离线兜底样例：仅用于无静态库时的数据结构演示，全部字段与 /v1/ 契约一致",
            "manifest_sources": [p.as_manifest_entry() for p in self.registry.all()],
            "entries": demo_entries,
        }
        path = self.data_dir / "v1" / "demo" / "index.json"
        _write_json(path, payload)
        return path


def _obs(snap: SnapshotRecord) -> Dict[str, Any]:
    return {
        "source_id": snap.source_id,
        "evidence_id": snap.evidence_id,
        "retrieved_at": snap.retrieved_at,
        "raw_value": canonical_json(snap.payload),
        "payload_sha256": snap.payload_sha256,
        "adapter_version": snap.adapter_version,
    }


def default_gtins() -> List[str]:
    env = os.environ.get("PPD_GTINS")
    if env:
        return [g.strip() for g in env.split(",") if g.strip()]
    from pipeline.fixtures_index import FIXTURE_GTINS

    return list(FIXTURE_GTINS)


def run_build(
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    ledger_path: Path = DEFAULT_LEDGER,
    fixtures_root: Path = DEFAULT_FIXTURES,
    gtins: Optional[Sequence[str]] = None,
    build_id: Optional[str] = None,
    description_hints: Optional[Dict[str, Dict[str, str]]] = None,
    note: str = "",
) -> BuildOutcome:
    registry = load_registry()
    ledger = Ledger(ledger_path)
    try:
        builder = Builder(
            registry=registry,
            ledger=ledger,
            data_dir=data_dir,
            fixtures_root=fixtures_root,
        )
        return builder.build(
            gtins=list(gtins or default_gtins()),
            build_id=build_id,
            description_hints=description_hints,
            note=note,
        )
    finally:
        ledger.close()


__all__ = [
    "Builder",
    "BuildOutcome",
    "DEFAULT_DATA_DIR",
    "DEFAULT_LEDGER",
    "DEFAULT_FIXTURES",
    "entity_id_for",
    "run_build",
]
