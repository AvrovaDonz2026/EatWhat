"""16 区块档案装配：把账本快照映射为客观字段，并强制绑定来源与证据。

红线：
- 每个展示字段都必须携带 source_id / evidence_id / retrieved_at / raw_value。
- 不同来源对同一字段的记录一律并列保存在 observations[]，不做取舍、不做「最终值」。
- 候选只在权威标识符精确相等时建立关联，否则并列展示。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from pipeline.builder.ledger import SnapshotRecord
from pipeline.builder.lexicon_terms import (
    canonical_nutrient,
    search_keys_for_allergen,
    search_keys_for_ingredient,
)

BLOCK_IDS: Sequence[tuple[str, str]] = (
    ("identity_basic", "基本信息（条码/品牌/商品名/净含量/包装）"),
    ("match_candidates", "标识符候选与关联状态"),
    ("label_ingredients", "配料表原文"),
    ("nutrition_facts", "营养成分表原文"),
    ("explicit_allergens", "致敏原原文提示"),
    ("explicit_unsuitable_groups", "标签标注的不适宜人群原文"),
    ("storage_and_shelf_life", "贮存条件与保质期"),
    ("registration_records", "注册/备案公开记录"),
    ("license_records", "生产许可公开记录"),
    ("company_prefix", "条码厂商前缀登记"),
    ("trademark_records", "商标公开记录"),
    ("referenced_standards", "引用的标准文本元数据"),
    ("import_export_records", "进出口商备案记录"),
    ("observations", "多源观测并列"),
    ("history_timeline", "历史快照时间线"),
    ("source_visibility", "来源可见性与证据哈希"),
)

LINK_EXACT = "EXACT_IDENTIFIER_MATCH"
LINK_MANUAL = "MANUAL_CONFIRM"
LINK_NONE = "NO_LINK"

STATUS_TIER = {
    "label_transcript": 1,
    "gs1": 2,
    "udi": 3,
    "samr_gov": 4,
    "samr_std": 5,
    "cnipa": 6,
    "cifer": 7,
}


@dataclass
class Field:
    label: str
    value: Any
    source_id: str
    evidence_id: str
    retrieved_at: str
    raw_value: Any

    def as_json(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "value": self.value,
            "raw_value": self.raw_value,
            "source_id": self.source_id,
            "evidence_id": self.evidence_id,
            "retrieved_at": self.retrieved_at,
        }


def _field(label: str, snap: SnapshotRecord, record: Dict[str, Any], key: str, *, value: Any = None) -> Field:
    raw = record.get(key)
    return Field(
        label=label,
        value=raw if value is None else value,
        source_id=snap.source_id,
        evidence_id=snap.evidence_id,
        retrieved_at=snap.retrieved_at,
        raw_value=raw,
    )


def _records(snap: SnapshotRecord) -> List[Dict[str, Any]]:
    payload = snap.payload
    if not isinstance(payload, dict):
        return []
    records = payload.get("records")
    if not isinstance(records, list):
        return []
    return [r for r in records if isinstance(r, dict)]


def _normalize_standard_no(value: str) -> str:
    return "".join(ch for ch in str(value).upper() if ch.isalnum())


@dataclass
class Candidate:
    candidate_id: str
    source_id: str
    evidence_id: str
    retrieved_at: str
    identifier_type: str
    identifier_value: str
    record_id: str
    display_name: str
    net_content: str
    pack_type: str
    link_mode: str
    link_reason: str

    def as_json(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_id": self.source_id,
            "evidence_id": self.evidence_id,
            "retrieved_at": self.retrieved_at,
            "identifier_type": self.identifier_type,
            "identifier_value": self.identifier_value,
            "record_id": self.record_id,
            "display_name": self.display_name,
            "net_content": self.net_content,
            "pack_type": self.pack_type,
            "link_mode": self.link_mode,
            "link_reason": self.link_reason,
        }


def build_candidates(gtin: str, snapshots: Sequence[SnapshotRecord]) -> List[Candidate]:
    """从快照中抽取候选。只有标识符精确相等才标记 EXACT_IDENTIFIER_MATCH。"""
    candidates: List[Candidate] = []
    for snap in snapshots:
        for record in _records(snap):
            record_id = str(record.get("record_id", ""))
            if not record_id:
                continue
            if snap.source_id == "gs1":
                identifier_type, identifier_value = "GTIN", str(record.get("gtin", ""))
                exact = _norm_digits(identifier_value) == _norm_digits(gtin)
            elif snap.source_id == "udi":
                identifier_type = "UDI_DI"
                di = str(record.get("udi_di", ""))
                # UDI-DI 通常以 0 补齐到 14 位；去除前导 0 后与 GTIN 精确比较
                identifier_value = di
                exact = _norm_digits(di).lstrip("0") == _norm_digits(gtin).lstrip("0")
            elif snap.source_id == "label_transcript":
                identifier_type, identifier_value = "GTIN", str(record.get("gtin", ""))
                exact = _norm_digits(identifier_value) == _norm_digits(gtin)
            elif snap.source_id == "samr_gov":
                identifier_type, identifier_value = "SC_NO", str(record.get("license_no", ""))
                exact = False
            elif snap.source_id == "samr_std":
                identifier_type = "STANDARD_NO"
                identifier_value = str(record.get("standard_no", ""))
                exact = False
            elif snap.source_id == "cnipa":
                identifier_type = "MARK_NAME"
                identifier_value = str(record.get("mark_name", ""))
                exact = False
            else:
                identifier_type, identifier_value = "CIFER_NO", str(record.get("cifer_no", ""))
                exact = False

            candidate_id = "cand_" + _stable_slug(
                f"{snap.source_id}|{record_id}|{snap.payload_sha256}"
            )
            candidates.append(
                Candidate(
                    candidate_id=candidate_id,
                    source_id=snap.source_id,
                    evidence_id=snap.evidence_id,
                    retrieved_at=snap.retrieved_at,
                    identifier_type=identifier_type,
                    identifier_value=identifier_value,
                    record_id=record_id,
                    display_name=str(record.get("product_name") or record.get("mark_name") or ""),
                    net_content=str(record.get("net_content") or record.get("specification") or ""),
                    pack_type=str(record.get("pack_type") or ""),
                    link_mode=LINK_EXACT if exact else LINK_MANUAL,
                    link_reason=(
                        f"{identifier_type} 精确等于查询条码 {gtin}"
                        if exact
                        else f"{identifier_type} 与查询条码 {gtin} 不构成精确标识符相等，需用户手动确认"
                    ),
                )
            )
    # 稳定排序：源优先级 + record_id，保证可复现
    candidates.sort(key=lambda c: (STATUS_TIER.get(c.source_id, 99), c.record_id))
    return candidates


def _norm_digits(value: str) -> str:
    return "".join(ch for ch in str(value) if ch.isdigit())


def _stable_slug(text: str) -> str:
    from pipeline.builder.ledger import sha256_of

    return sha256_of(text)[:10]


@dataclass
class DossierBlocks:
    blocks: Dict[str, Dict[str, Any]]

    def as_json(self) -> List[Dict[str, Any]]:
        return [self.blocks[block_id] for block_id, _ in BLOCK_IDS]


def _empty_block(block_id: str, title: str, *, state: str, note: str) -> Dict[str, Any]:
    return {
        "block_id": block_id,
        "title": title,
        "state": state,
        "note": note,
        "fields": [],
        "items": [],
    }


def assemble_blocks(
    *,
    gtin: str,
    candidates: Sequence[Candidate],
    snapshots: Sequence[SnapshotRecord],
    visibility: Sequence[Dict[str, Any]],
    coverage: Sequence[Dict[str, Any]],
    artifact_hashes: Dict[str, str],
    history: Optional[Sequence[SnapshotRecord]] = None,
) -> DossierBlocks:
    """把快照装配为 16 区块。

    snapshots 为「各源当前最新记录」，用于字段区块；
    history 为全部历史快照（含被替代版本），用于观测并列与时间线。
    所有字段均来自 raw 记录，逐字段绑定证据。
    """
    history_all = list(history) if history is not None else list(snapshots)
    blocks: Dict[str, Dict[str, Any]] = {bid: _empty_block(bid, title, state="EMPTY", note="本区块无已采集记录") for bid, title in BLOCK_IDS}

    def fill(block_id: str, *, state: str, note: str, fields: List[Field], items: Optional[List[Dict[str, Any]]] = None) -> None:
        if not fields and not items and state == "EMPTY":
            return
        blocks[block_id] = {
            "block_id": block_id,
            "title": blocks[block_id]["title"],
            "state": state,
            "note": note,
            "fields": [f.as_json() for f in fields],
            "items": items or [],
        }

    # ---- 1 identity_basic
    identity_fields: List[Field] = []
    for snap in snapshots:
        for record in _records(snap):
            for key, label in (
                ("product_name", "商品名称"),
                ("brand", "品牌"),
                ("net_content", "净含量"),
                ("pack_type", "包装形式"),
                ("gtin", "条码（GTIN）"),
                ("company_prefix", "条码厂商前缀"),
            ):
                if record.get(key) not in (None, ""):
                    identity_fields.append(_field(label, snap, record, key))
    if identity_fields:
        fill(
            "identity_basic",
            state="FILLED",
            note="以下基本信息来自各数据源原始记录，按来源并列列出",
            fields=identity_fields,
        )

    # ---- 2 match_candidates
    exact_count = sum(1 for c in candidates if c.link_mode == LINK_EXACT)
    fill(
        "match_candidates",
        state="FILLED" if candidates else "EMPTY",
        note=(
            "候选列表仅做并列展示；只有权威标识符精确相等的记录标记为 EXACT_IDENTIFIER_MATCH，"
            "其余需用户手动确认，系统不自动合并"
        ),
        fields=[],
        items=[c.as_json() for c in candidates],
    )
    blocks["match_candidates"]["exact_match_count"] = exact_count
    blocks["match_candidates"]["manual_confirm_count"] = len(candidates) - exact_count

    # ---- 3 label_ingredients
    ingredient_items: List[Dict[str, Any]] = []
    for snap in snapshots:
        for record in _records(snap):
            raw_ingredients = record.get("ingredients")
            if not isinstance(raw_ingredients, list):
                continue
            for index, raw_name in enumerate(raw_ingredients, start=1):
                ingredient_items.append(
                    {
                        "label": "配料表原文",
                        "order": index,
                        "raw_name": raw_name,
                        "search_keys": search_keys_for_ingredient(str(raw_name)),
                        "source_id": snap.source_id,
                        "evidence_id": snap.evidence_id,
                        "retrieved_at": snap.retrieved_at,
                        "raw_value": raw_name,
                        "record_id": record.get("record_id"),
                    }
                )
    if ingredient_items:
        fill(
            "label_ingredients",
            state="FILLED",
            note="配料表按包装标签原文顺序逐项列出，未做改写",
            fields=[],
            items=ingredient_items,
        )

    # ---- 4 nutrition_facts
    nutrition_items: List[Dict[str, Any]] = []
    for snap in snapshots:
        for record in _records(snap):
            raw_nutrition = record.get("nutrition_facts")
            if not isinstance(raw_nutrition, list):
                continue
            for entry in raw_nutrition:
                if not isinstance(entry, dict):
                    continue
                nutrition_items.append(
                    {
                        "label": "营养成分表原文",
                        "nutrient": canonical_nutrient(str(entry.get("nutrient", ""))),
                        "raw_nutrient": entry.get("nutrient", ""),
                        "raw_value": entry.get("raw_value", ""),
                        "source_id": snap.source_id,
                        "evidence_id": snap.evidence_id,
                        "retrieved_at": snap.retrieved_at,
                        "record_id": record.get("record_id"),
                    }
                )
    if nutrition_items:
        fill(
            "nutrition_facts",
            state="FILLED",
            note="营养成分按标签原文逐条列出，单位与表述保持原样",
            fields=[],
            items=nutrition_items,
        )

    # ---- 5 explicit_allergens
    allergen_items: List[Dict[str, Any]] = []
    for snap in snapshots:
        for record in _records(snap):
            raw_allergens = record.get("allergens")
            if not isinstance(raw_allergens, list):
                continue
            for entry in raw_allergens:
                if not isinstance(entry, dict):
                    continue
                raw_text = entry.get("raw_text", "")
                allergen_items.append(
                    {
                        "label": "致敏原原文提示",
                        "raw_text": raw_text,
                        "standard_ref": entry.get("standard_ref", ""),
                        "search_keys": search_keys_for_allergen(str(raw_text)),
                        "source_id": snap.source_id,
                        "evidence_id": snap.evidence_id,
                        "retrieved_at": snap.retrieved_at,
                        "record_id": record.get("record_id"),
                    }
                )
    if allergen_items:
        fill(
            "explicit_allergens",
            state="FILLED",
            note="以下为标签上明确书写的致敏原提示原文，未做任何补充判断",
            fields=[],
            items=allergen_items,
        )

    # ---- 6 explicit_unsuitable_groups
    unsuitable_items: List[Dict[str, Any]] = []
    for snap in snapshots:
        for record in _records(snap):
            raw_groups = record.get("unsuitable_groups")
            if not isinstance(raw_groups, list):
                continue
            for entry in raw_groups:
                if not isinstance(entry, dict):
                    continue
                unsuitable_items.append(
                    {
                        "label": "标签标注的不适宜人群原文",
                        "raw_text": entry.get("raw_text", ""),
                        "source_id": snap.source_id,
                        "evidence_id": snap.evidence_id,
                        "retrieved_at": snap.retrieved_at,
                        "record_id": record.get("record_id"),
                    }
                )
    if unsuitable_items:
        fill(
            "explicit_unsuitable_groups",
            state="FILLED",
            note="仅复述标签原文，系统不扩充适宜性范围",
            fields=[],
            items=unsuitable_items,
        )

    # ---- 7 storage_and_shelf_life
    storage_fields: List[Field] = []
    for snap in snapshots:
        for record in _records(snap):
            for key, label in (("storage_condition", "贮存条件"), ("shelf_life", "保质期")):
                if record.get(key) not in (None, ""):
                    storage_fields.append(_field(label, snap, record, key))
    if storage_fields:
        fill(
            "storage_and_shelf_life",
            state="FILLED",
            note="贮存条件与保质期均为原文记录",
            fields=storage_fields,
        )

    # ---- 8 registration_records
    reg_fields: List[Field] = []
    for snap in snapshots:
        if snap.source_id != "udi":
            continue
        for record in _records(snap):
            for key, label in (
                ("registration_no", "注册/备案编号"),
                ("registration_status", "公开记录中的状态原文"),
                ("record_type", "记录类型"),
                ("registrant", "登载主体"),
                ("specification", "规格"),
                ("udi_di", "UDI-DI"),
            ):
                if record.get(key) not in (None, ""):
                    reg_fields.append(_field(label, snap, record, key))
    if reg_fields:
        fill(
            "registration_records",
            state="FILLED",
            note="来自 NMPA UDI 公开查询记录的原始字段",
            fields=reg_fields,
        )

    # ---- 9 license_records
    license_fields: List[Field] = []
    for snap in snapshots:
        if snap.source_id != "samr_gov":
            continue
        for record in _records(snap):
            for key, label in (
                ("license_no", "许可编号"),
                ("licensee", "被许可人"),
                ("license_status", "公开记录中的状态原文"),
                ("food_category", "食品类别"),
                ("issue_date", "发证日期"),
            ):
                if record.get(key) not in (None, ""):
                    license_fields.append(_field(label, snap, record, key))
    if license_fields:
        fill(
            "license_records",
            state="FILLED",
            note="来自市场监管公开许可查询的原始字段",
            fields=license_fields,
        )

    # ---- 10 company_prefix
    prefix_fields: List[Field] = []
    for snap in snapshots:
        if snap.source_id != "gs1":
            continue
        for record in _records(snap):
            if record.get("company_prefix") not in (None, ""):
                prefix_fields.append(_field("条码厂商前缀", snap, record, "company_prefix"))
    if prefix_fields:
        fill(
            "company_prefix",
            state="FILLED",
            note="GS1 公开登记中的厂商前缀字段原文",
            fields=prefix_fields,
        )

    # ---- 11 trademark_records
    tm_fields: List[Field] = []
    for snap in snapshots:
        if snap.source_id != "cnipa":
            continue
        for record in _records(snap):
            for key, label in (
                ("mark_name", "商标名称"),
                ("application_no", "申请号"),
                ("applicant", "申请人"),
                ("status", "公开记录中的状态原文"),
                ("class_no", "类别"),
            ):
                if record.get(key) not in (None, ""):
                    tm_fields.append(_field(label, snap, record, key))
    if tm_fields:
        fill(
            "trademark_records",
            state="FILLED",
            note="来自商标公开查询的原始字段",
            fields=tm_fields,
        )
    else:
        pending = next((c for c in coverage if c["source_id"] == "cnipa"), None)
        if pending:
            blocks["trademark_records"] = _empty_block(
                "trademark_records",
                blocks["trademark_records"]["title"],
                state=pending["coverage_state"],
                note="该数据源当前未接入，本区块不展示任何记录",
            )

    # ---- 12 referenced_standards
    std_items: List[Dict[str, Any]] = []
    for snap in snapshots:
        if snap.source_id != "samr_std":
            continue
        for record in _records(snap):
            std_items.append(
                {
                    "label": "标准文本元数据",
                    "standard_no": record.get("standard_no", ""),
                    "standard_name": record.get("standard_name", ""),
                    "status": record.get("status", ""),
                    "issuer": record.get("issuer", ""),
                    "effective_date": record.get("effective_date", ""),
                    "source_id": snap.source_id,
                    "evidence_id": snap.evidence_id,
                    "retrieved_at": snap.retrieved_at,
                    "raw_value": record.get("standard_no", ""),
                    "record_id": record.get("record_id"),
                }
            )
    label_standard_refs: List[Dict[str, Any]] = []
    for snap in snapshots:
        for record in _records(snap):
            ref = record.get("label_standard_ref")
            if ref:
                label_standard_refs.append(
                    {
                        "label": "标签所标注的标准号",
                        "standard_no": ref,
                        "source_id": snap.source_id,
                        "evidence_id": snap.evidence_id,
                        "retrieved_at": snap.retrieved_at,
                        "raw_value": ref,
                        "record_id": record.get("record_id"),
                    }
                )
    if std_items or label_standard_refs:
        fill(
            "referenced_standards",
            state="FILLED",
            note="标准号与标准名称均为公开系统原文；标准号相等仅用于并列展示，不判断适用性",
            fields=[],
            items=std_items + label_standard_refs,
        )

    # ---- 13 import_export_records
    cifer_items: List[Dict[str, Any]] = []
    for snap in snapshots:
        if snap.source_id != "cifer":
            continue
        for record in _records(snap):
            cifer_items.append(
                {
                    "label": "进出口商备案记录",
                    "cifer_no": record.get("cifer_no", ""),
                    "importer": record.get("importer", ""),
                    "exporter": record.get("exporter", ""),
                    "record_type": record.get("record_type", ""),
                    "source_id": snap.source_id,
                    "evidence_id": snap.evidence_id,
                    "retrieved_at": snap.retrieved_at,
                    "raw_value": record.get("cifer_no", ""),
                }
            )
    if cifer_items:
        fill(
            "import_export_records",
            state="FILLED",
            note="来自海关 CIFER 公开查询的原始字段",
            fields=[],
            items=cifer_items,
        )
    else:
        unavailable = next((c for c in coverage if c["source_id"] == "cifer"), None)
        if unavailable:
            blocks["import_export_records"] = _empty_block(
                "import_export_records",
                blocks["import_export_records"]["title"],
                state=unavailable["coverage_state"],
                note="该数据源当前不可访问，本区块不展示任何记录",
            )

    # ---- 14 observations
    observation_items = [_observation_entry(snap) for snap in history_all]
    if observation_items:
        fill(
            "observations",
            state="FILLED",
            note="各来源观测并列保存，每条记录绑定 source_id / evidence_id / retrieved_at / raw_value；不设最终真相字段",
            fields=[],
            items=observation_items,
        )

    # ---- 15 history_timeline
    timeline_items = [
        {
            "label": "快照时间线",
            "evidence_id": snap.evidence_id,
            "source_id": snap.source_id,
            "retrieved_at": snap.retrieved_at,
            "payload_sha256": snap.payload_sha256,
            "adapter_version": snap.adapter_version,
            "request_url": snap.request_url,
            "record_ids": sorted(
                str(r.get("record_id", "")) for r in _records(snap) if r.get("record_id")
            ),
        }
        for snap in sorted(history_all, key=lambda s: s.seq)
    ]
    if timeline_items:
        fill(
            "history_timeline",
            state="FILLED",
            note="历史快照按采集时间追加保留；来源更新或下线不会删除旧快照",
            fields=[],
            items=timeline_items,
        )

        # ---- 16 source_visibility
    vis_items = [
        {
            "label": "来源可见性事件",
            "seq": item.get("seq"),
            "event_id": item["event_id"],
            "entity_id": item["entity_id"],
            "source_id": item["source_id"],
            "evidence_id": item["evidence_id"],
            "event_type": item["event_type"],
            "recorded_at": item["recorded_at"],
            "note": item["note"],
        }
        for item in visibility
    ]
    effective_items = [
        {
            "label": "来源当前生效可见性（由事件序列推导）",
            "source_id": entry["source_id"],
            "evidence_id": entry["evidence_id"],
            "event_type": entry["event_type"],
            "since_event_id": entry["since_event_id"],
            "note": entry["note"],
        }
        for entry in effective_visibility(visibility)
    ]
    cover_items = [
        {
            "label": "数据源覆盖状态",
            "source_id": item["source_id"],
            "display_name": item["display_name"],
            "access_mode": item["access_mode"],
            "coverage_state": item["coverage_state"],
            "note": item["note"],
        }
        for item in coverage
    ]
    evidence_items = [
        {
            "label": "原始证据哈希",
            "path": path,
            "sha256": digest,
        }
        for path, digest in sorted(artifact_hashes.items())
    ]
    fill(
        "source_visibility",
        state="FILLED",
        note="来源可见性事件为只追加日志；「当前生效可见性」由事件序列按 seq 推导，历史事件一律保留",
        fields=[],
        items=vis_items + effective_items + cover_items + evidence_items,
    )

    return DossierBlocks(blocks=blocks)


def effective_visibility(events: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """由只追加的事件序列推导「每个来源当前生效的可见性」。

    事件历史本身不会被改写（append-only）；这里只是按 seq 取每个来源
    最后一条事件，得到当前生效状态。REMOVED 之后若又有新事件，则以新事件为准。
    """
    latest: Dict[str, Dict[str, Any]] = {}
    for event in sorted(events, key=lambda e: int(e.get("seq") or 0)):
        latest[event["source_id"]] = event
    out = []
    for source_id in sorted(latest):
        event = latest[source_id]
        out.append(
            {
                "source_id": source_id,
                "evidence_id": event["evidence_id"],
                "event_type": event["event_type"],
                "since_event_id": event["event_id"],
                "note": event.get("note", ""),
            }
        )
    return out


def _observation_entry(snap: SnapshotRecord) -> Dict[str, Any]:
    return {
        "label": "来源观测",
        "source_id": snap.source_id,
        "evidence_id": snap.evidence_id,
        "retrieved_at": snap.retrieved_at,
        "adapter_version": snap.adapter_version,
        "payload_sha256": snap.payload_sha256,
        "request_url": snap.request_url,
        "raw_value": json.dumps(snap.payload, ensure_ascii=False, sort_keys=True),
        "record_count": len(_records(snap)),
    }


def coverage_manifest(
    *,
    registry,
    queried_sources: Iterable[str],
    matched_sources: Iterable[str],
    failures: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """覆盖清单：对每个登记源披露接入状态，不隐藏未接入/不可用源。"""
    matched = set(matched_sources)
    queried = set(queried_sources)
    failures = failures or {}
    entries: List[Dict[str, Any]] = []
    for policy in registry.all():
        if policy.access_mode in ("ACCESS_PENDING", "SOURCE_UNAVAILABLE"):
            state = policy.access_mode
            note = {
                "ACCESS_PENDING": "该数据源接入待授权，本档案不含其记录",
                "SOURCE_UNAVAILABLE": "该数据源当前不可稳定访问，本档案不含其记录",
            }[policy.access_mode]
        elif policy.source_id in failures:
            state = "QUERY_FAILED"
            note = f"本次查询失败：{failures[policy.source_id]}"
        elif policy.source_id in matched:
            state = "QUERIED_MATCHED"
            note = "本次查询取得记录"
        elif policy.source_id in queried:
            state = "QUERIED_NO_MATCH"
            note = "本次查询未取得记录"
        else:
            state = "NOT_QUERIED"
            note = "该源不适用于本条码的查询标识符类型"
        entries.append(
            {
                "source_id": policy.source_id,
                "display_name": policy.display_name,
                "authority": policy.authority,
                "access_mode": policy.access_mode,
                "coverage_state": state,
                "policy_url": policy.policy_url,
                "note": note,
            }
        )
    return entries


__all__ = [
    "BLOCK_IDS",
    "Candidate",
    "DossierBlocks",
    "LINK_EXACT",
    "LINK_MANUAL",
    "assemble_blocks",
    "build_candidates",
    "effective_visibility",
    "coverage_manifest",
]
