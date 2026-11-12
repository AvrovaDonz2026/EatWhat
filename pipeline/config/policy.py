"""策略注册表加载与门禁校验。

红线：适配器只有在策略注册表登记且 access_mode 放行时才允许运行。
凭证只允许以环境变量名的形式登记；本模块拒绝任何形似密钥的字面值。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

ACCESS_MODES = ("OPEN_BULK", "OPEN_API", "ACCESS_PENDING", "SOURCE_UNAVAILABLE")
RUNNABLE_MODES = ("OPEN_BULK", "OPEN_API")
SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,31}$")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")

# 看起来像真实凭据的字面量（拒收）
_SECRET_LIKE = re.compile(
    r"(sk_live|AKIA|ghp_|Bearer\s+[A-Za-z0-9]|eyJ[A-Za-z0-9_\-]{10,}|[A-Fa-f0-9]{32,})"
)

REQUIRED_SOURCE_KEYS = {
    "source_id",
    "display_name",
    "authority",
    "jurisdiction",
    "access_mode",
    "policy_url",
    "legal_basis",
    "query_identifier_types",
    "endpoint_template",
    "rate_limit",
    "backoff",
    "schema_keys",
    "expected_fields",
    "credentials",
}
OPTIONAL_SOURCE_KEYS = {"notes"}
REQUIRED_RATE_KEYS = {"requests_per_minute", "min_interval_ms", "max_concurrency"}
REQUIRED_BACKOFF_KEYS = {"base_ms", "factor", "jitter_ms", "max_retries"}
REQUIRED_CRED_KEYS = {"token_env", "app_id_env"}


class PolicyError(ValueError):
    """注册表本身不合规。"""


class PolicyGateError(PermissionError):
    """某个源未放行，适配器不得运行。"""


@dataclass(frozen=True)
class RateLimit:
    requests_per_minute: int
    min_interval_ms: int
    max_concurrency: int


@dataclass(frozen=True)
class Backoff:
    base_ms: int
    factor: float
    jitter_ms: int
    max_retries: int


@dataclass(frozen=True)
class SourcePolicy:
    source_id: str
    display_name: str
    authority: str
    jurisdiction: str
    access_mode: str
    policy_url: str
    legal_basis: str
    query_identifier_types: List[str]
    endpoint_template: str
    rate_limit: RateLimit
    backoff: Backoff
    schema_keys: List[str]
    expected_fields: List[str]
    credentials: Dict[str, Optional[str]]
    notes: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def runnable(self) -> bool:
        return self.access_mode in RUNNABLE_MODES

    def as_manifest_entry(self) -> Dict[str, Any]:
        """manifest 中只披露公开信息，绝不包含凭证字段名以外的任何内容。"""
        return {
            "source_id": self.source_id,
            "display_name": self.display_name,
            "authority": self.authority,
            "jurisdiction": self.jurisdiction,
            "access_mode": self.access_mode,
            "policy_url": self.policy_url,
            "legal_basis": self.legal_basis,
            "query_identifier_types": list(self.query_identifier_types),
            "rate_limit": {
                "requests_per_minute": self.rate_limit.requests_per_minute,
                "min_interval_ms": self.rate_limit.min_interval_ms,
                "max_concurrency": self.rate_limit.max_concurrency,
            },
            "notes": self.notes,
        }


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise PolicyError(message)


def _validate_source(raw: Dict[str, Any], index: int) -> SourcePolicy:
    _require(isinstance(raw, dict), f"sources[{index}] 必须是对象")
    missing = REQUIRED_SOURCE_KEYS - set(raw)
    extra = set(raw) - REQUIRED_SOURCE_KEYS - OPTIONAL_SOURCE_KEYS
    _require(not missing, f"sources[{index}] 缺少字段: {sorted(missing)}")
    _require(not extra, f"sources[{index}] 存在未登记字段: {sorted(extra)}")

    sid = raw["source_id"]
    _require(isinstance(sid, str) and bool(SOURCE_ID_RE.match(sid)), f"非法 source_id: {sid!r}")
    _require(
        raw["access_mode"] in ACCESS_MODES,
        f"{sid}: access_mode 必须是 {ACCESS_MODES} 之一",
    )
    _require(isinstance(raw["schema_keys"], list) and raw["schema_keys"], f"{sid}: schema_keys 不得为空")
    _require(
        isinstance(raw["expected_fields"], list) and raw["expected_fields"],
        f"{sid}: expected_fields 不得为空",
    )
    _require(
        isinstance(raw["query_identifier_types"], list) and raw["query_identifier_types"],
        f"{sid}: query_identifier_types 不得为空",
    )

    rl = raw["rate_limit"]
    _require(isinstance(rl, dict) and REQUIRED_RATE_KEYS <= set(rl), f"{sid}: rate_limit 字段不全")
    _require(
        isinstance(rl["requests_per_minute"], int) and rl["requests_per_minute"] > 0,
        f"{sid}: requests_per_minute 必须为正整数",
    )
    _require(
        isinstance(rl["min_interval_ms"], int) and rl["min_interval_ms"] >= 1000,
        f"{sid}: min_interval_ms 必须 >= 1000，禁止突破限流",
    )
    _require(
        isinstance(rl["max_concurrency"], int) and rl["max_concurrency"] >= 1,
        f"{sid}: max_concurrency 必须 >= 1",
    )

    bo = raw["backoff"]
    _require(isinstance(bo, dict) and REQUIRED_BACKOFF_KEYS <= set(bo), f"{sid}: backoff 字段不全")
    _require(
        isinstance(bo["base_ms"], int) and bo["base_ms"] >= 100,
        f"{sid}: backoff.base_ms 必须 >= 100",
    )
    _require(float(bo["factor"]) >= 1.0, f"{sid}: backoff.factor 必须 >= 1.0")
    _require(
        isinstance(bo["jitter_ms"], int) and 0 <= bo["jitter_ms"] <= 2000,
        f"{sid}: backoff.jitter_ms 必须位于 [0, 2000]",
    )
    _require(
        isinstance(bo["max_retries"], int) and 0 <= bo["max_retries"] <= 5,
        f"{sid}: backoff.max_retries 必须位于 [0, 5]",
    )

    cred = raw["credentials"]
    _require(isinstance(cred, dict) and REQUIRED_CRED_KEYS <= set(cred), f"{sid}: credentials 字段不全")
    for key in ("token_env", "app_id_env"):
        value = cred[key]
        _require(
            value is None or (isinstance(value, str) and bool(ENV_NAME_RE.match(value))),
            f"{sid}: credentials.{key} 只能是环境变量名或 null（禁止写入凭据字面值）",
        )
        if isinstance(value, str):
            _require(
                not _SECRET_LIKE.search(value),
                f"{sid}: credentials.{key} 形似真实凭据，禁止登记",
            )

    _require(raw["endpoint_template"].startswith(("https://", "local://")), f"{sid}: endpoint_template 协议不受支持")

    return SourcePolicy(
        source_id=sid,
        display_name=raw["display_name"],
        authority=raw["authority"],
        jurisdiction=raw["jurisdiction"],
        access_mode=raw["access_mode"],
        policy_url=raw["policy_url"],
        legal_basis=raw["legal_basis"],
        query_identifier_types=list(raw["query_identifier_types"]),
        endpoint_template=raw["endpoint_template"],
        rate_limit=RateLimit(**{k: rl[k] for k in REQUIRED_RATE_KEYS}),
        backoff=Backoff(**{k: bo[k] for k in REQUIRED_BACKOFF_KEYS}),
        schema_keys=list(raw["schema_keys"]),
        expected_fields=list(raw["expected_fields"]),
        credentials={k: cred[k] for k in REQUIRED_CRED_KEYS},
        notes=raw.get("notes", ""),
        raw=raw,
    )


class SourceRegistry:
    """只读策略注册表。"""

    def __init__(self, sources: Dict[str, SourcePolicy], forbidden_actions: List[str], registry_version: str):
        self._sources = dict(sources)
        self.forbidden_actions = list(forbidden_actions)
        self.registry_version = registry_version

    def __contains__(self, source_id: str) -> bool:
        return source_id in self._sources

    def __len__(self) -> int:
        return len(self._sources)

    def all(self) -> List[SourcePolicy]:
        return sorted(self._sources.values(), key=lambda p: p.source_id)

    def get(self, source_id: str) -> SourcePolicy:
        try:
            return self._sources[source_id]
        except KeyError:
            raise PolicyGateError(f"数据源未在策略注册表登记: {source_id}") from None

    def require_runnable(self, source_id: str) -> SourcePolicy:
        policy = self.get(source_id)
        if policy.access_mode not in RUNNABLE_MODES:
            raise PolicyGateError(
                f"数据源 {source_id} 当前状态为 {policy.access_mode}，适配器禁止运行；"
                "仅允许在 manifest / coverage_manifest 中披露覆盖状态"
            )
        return policy


def load_registry(path: Optional[Path] = None) -> SourceRegistry:
    path = path or Path(__file__).with_name("source_policy_registry.json")
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    _require(isinstance(raw, dict), "注册表必须是 JSON 对象")
    _require(raw.get("allowed_access_modes") == list(ACCESS_MODES), "allowed_access_modes 与代码枚举不一致")

    first = set(raw["sources"][0]) if raw.get("sources") else set()
    extra_top = set(raw) - {
        "registry_version",
        "generated_at",
        "description",
        "allowed_access_modes",
        "global_forbidden_actions",
        "sources",
    }
    _require(not extra_top, f"注册表存在未登记顶层字段: {sorted(extra_top)}")

    forbidden = raw.get("global_forbidden_actions", [])
    _require(isinstance(forbidden, list), "global_forbidden_actions 必须是数组")
    required_forbidden = {"captcha_bypass", "rate_limit_override", "credential_in_artifact"}
    _require(
        required_forbidden <= set(forbidden),
        f"global_forbidden_actions 必须包含 {sorted(required_forbidden)}",
    )

    sources: Dict[str, SourcePolicy] = {}
    for index, entry in enumerate(raw.get("sources", [])):
        policy = _validate_source(entry, index)
        _require(policy.source_id not in sources, f"重复的 source_id: {policy.source_id}")
        sources[policy.source_id] = policy
    _require(bool(sources), "注册表至少需要一个数据源")
    _ = first  # 仅为保持显式读取结构

    return SourceRegistry(
        sources=sources,
        forbidden_actions=forbidden,
        registry_version=raw["registry_version"],
    )
