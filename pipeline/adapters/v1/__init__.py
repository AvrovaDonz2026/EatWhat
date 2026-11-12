"""V1 数据源适配器集合。

每个适配器只负责把特定源的原始响应原样搬运为「观测记录」，
不做任何字段推断、不合并候选、不判断真伪。
"""
from __future__ import annotations

from typing import Dict, Type

from pipeline.adapters.base import SourceAdapter


class _RecordsAdapter(SourceAdapter):
    """统一 records[] 信封的源适配器基类。"""

    def normalize_records(self, payload: dict) -> list[dict]:
        records = payload.get("records") if isinstance(payload, dict) else None
        if not isinstance(records, list):
            return []
        return [r for r in records if isinstance(r, dict)]


class UDIAdapter(_RecordsAdapter):
    source_id = "udi"
    identifier_type = "GTIN"
    adapter_version = "1.0.0"


class CnipaAdapter(_RecordsAdapter):
    source_id = "cnipa"
    identifier_type = "BRAND_NAME"
    adapter_version = "1.0.0"


class SamrStdAdapter(_RecordsAdapter):
    source_id = "samr_std"
    identifier_type = "STANDARD_NO"
    adapter_version = "1.0.0"


class SamrGovAdapter(_RecordsAdapter):
    source_id = "samr_gov"
    identifier_type = "GTIN"
    adapter_version = "1.0.0"


class CiferAdapter(_RecordsAdapter):
    source_id = "cifer"
    identifier_type = "CIFER_NO"
    adapter_version = "1.0.0"


class GS1Adapter(_RecordsAdapter):
    source_id = "gs1"
    identifier_type = "GTIN"
    adapter_version = "1.0.0"


class LabelTranscriptAdapter(_RecordsAdapter):
    source_id = "label_transcript"
    identifier_type = "GTIN"
    adapter_version = "1.0.0"


ADAPTERS: Dict[str, Type[SourceAdapter]] = {
    cls.source_id: cls
    for cls in (
        UDIAdapter,
        CnipaAdapter,
        SamrStdAdapter,
        SamrGovAdapter,
        CiferAdapter,
        GS1Adapter,
        LabelTranscriptAdapter,
    )
}


def adapter_classes_for_runnable(registry) -> Dict[str, Type[SourceAdapter]]:
    """只返回策略放行的适配器。未放行的源不会出现在构建序列里。"""
    return {
        source_id: cls
        for source_id, cls in ADAPTERS.items()
        if registry.get(source_id).runnable
    }


__all__ = [
    "ADAPTERS",
    "CiferAdapter",
    "CnipaAdapter",
    "GS1Adapter",
    "LabelTranscriptAdapter",
    "SamrGovAdapter",
    "SamrStdAdapter",
    "UDIAdapter",
    "adapter_classes_for_runnable",
]
