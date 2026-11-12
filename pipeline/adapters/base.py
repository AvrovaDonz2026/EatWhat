"""SourceAdapter 基类：策略门禁 + 限流 + 指数退避 + 结构变更熔断。

红线：
- 构造时必须传入策略注册表；未登记或未放行的源在 __init__ 阶段即拒绝运行。
- 每次请求前必须通过本地令牌桶（requests_per_minute + min_interval_ms）。
- 响应结构不符合 schema_keys 时立即熔断，抛 SOURCE_FORMAT_CHANGED，禁止猜测解析。
- 捕获验证码/登录墙 → 抛 CAPTCHA_OR_LOGIN_REQUIRED，绝不尝试绕过。
"""
from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol
from urllib.parse import quote, urlparse

from pipeline.builder import ledger as ledger_mod
from pipeline.config.policy import PolicyGateError, SourcePolicy, SourceRegistry

ADAPTER_VERSION = "1.0.0"

ERROR_SOURCE_FORMAT_CHANGED = "SOURCE_FORMAT_CHANGED"
ERROR_RATE_LIMITED = "RATE_LIMITED_EXHAUSTED"
ERROR_CAPTCHA_OR_LOGIN = "CAPTCHA_OR_LOGIN_REQUIRED"
ERROR_TRANSPORT = "TRANSPORT_ERROR"
ERROR_POLICY_BLOCKED = "POLICY_BLOCKED"


class AdapterError(RuntimeError):
    code = ERROR_TRANSPORT

    def __init__(self, message: str, *, source_id: str = "", detail: Dict[str, Any] | None = None):
        super().__init__(message)
        self.source_id = source_id
        self.detail = detail or {}


class SourceFormatChanged(AdapterError):
    """结构变更熔断：宁可空结果，也不猜测解析。"""

    code = ERROR_SOURCE_FORMAT_CHANGED


class RateLimitedError(AdapterError):
    code = ERROR_RATE_LIMITED


class CaptchaOrLoginRequired(AdapterError):
    code = ERROR_CAPTCHA_OR_LOGIN


class PolicyBlocked(AdapterError):
    code = ERROR_POLICY_BLOCKED


class Transport(Protocol):
    """可替换的取数通道。真实接入时替换为受限 HTTP 客户端。"""

    def fetch_json(
        self,
        url: str,
        params: Dict[str, Any],
        headers: Dict[str, str],
        *,
        source_id: str = "",
        identifier: str = "",
    ) -> Any: ...


class LocalFixtureTransport:
    """离线夹具通道：读取本地 JSON，模拟真实响应（用于构建与验收）。"""

    CAPTCHA_HINT = re.compile(r"(captcha|验证码|请先登录|login required|signin)", re.IGNORECASE)

    def __init__(self, root: Path | str, *, scenarios: Optional[Dict[str, str]] = None):
        self.root = Path(root)
        self.scenarios = scenarios or {}

    def _path_for(self, source_id: str, identifier: str) -> Optional[Path]:
        if not source_id or not identifier:
            return None
        return self.root / source_id / f"{identifier}.json"

    def fetch_json(
        self,
        url: str,
        params: Dict[str, Any],
        headers: Dict[str, str],
        *,
        source_id: str = "",
        identifier: str = "",
    ) -> Any:
        scenario = self.scenarios.get(f"{source_id}:{identifier}") or self.scenarios.get(url)
        if scenario == "captcha":
            raise CaptchaOrLoginRequired("命中验证码/登录墙夹具场景；按红线终止，不尝试绕过", detail={"url": url})
        if scenario == "transport_error":
            raise AdapterError(f"模拟传输失败: {url}", detail={"url": url})
        if scenario == "rate_limited":
            raise AdapterError("模拟 429", detail={"status": 429, "url": url})
        if scenario == "format_changed":
            return {"unexpected_envelope": [], "note": "schema 变更夹具"}

        path = self._path_for(source_id, identifier)
        if path is None:
            raise AdapterError("夹具通道缺少 source_id/identifier 定位信息", detail={"url": url})
        if not path.exists():
            return {"records": []}
        text = path.read_text(encoding="utf-8")
        if self.CAPTCHA_HINT.search(text):
            raise CaptchaOrLoginRequired("夹具内容提示验证码/登录，终止采集", detail={"url": url})
        return json.loads(text)


class StaticHttpTransport:
    """受限 HTTP 通道占位：只允许 GET，且断言注册表中的 host。

    真实采集必须由人工在策略审批后启用；默认禁用，避免误触发线上站点。
    """

    def __init__(self, allowed_hosts: List[str], *, enabled: bool = False):
        self.allowed_hosts = set(allowed_hosts)
        self.enabled = enabled

    def fetch_json(
        self,
        url: str,
        params: Dict[str, Any],
        headers: Dict[str, str],
        *,
        source_id: str = "",
        identifier: str = "",
    ) -> Any:
        raise AdapterError(
            "线上采集通道未启用：离线构建默认只使用夹具/本地快照。"
            "启用需经策略审批并在构建机显式传入 enabled=True。"
        )


@dataclass
class ThrottleState:
    """进程内限流状态。"""

    min_interval_ms: int
    requests_per_minute: int
    last_call_at: float = -1e9
    window_start: float = -1e9
    window_count: int = 0
    total_calls: int = 0
    sleeper: Callable[[float], None] = field(default=time.sleep)

    def acquire(self) -> None:
        now = time.monotonic()
        # 1) 最小间隔
        gap = now - self.last_call_at
        need = self.min_interval_ms / 1000.0
        if gap < need:
            self.sleeper(need - gap)
            now = time.monotonic()
        # 2) 每分钟配额
        if now - self.window_start >= 60.0:
            self.window_start = now
            self.window_count = 0
        if self.window_count >= self.requests_per_minute:
            wait = 60.0 - (now - self.window_start)
            if wait > 0:
                self.sleeper(wait)
                self.window_start = time.monotonic()
                self.window_count = 0
            now = time.monotonic()
        self.last_call_at = now
        self.window_count += 1
        self.total_calls += 1


class Backoff:
    def __init__(self, *, base_ms: int, factor: float, jitter_ms: int, max_retries: int, sleeper=time.sleep, rng=None):
        self.base_ms = base_ms
        self.factor = factor
        self.jitter_ms = jitter_ms
        self.max_retries = max_retries
        self.sleeper = sleeper
        self.rng = rng or random.Random(0)

    def delay_for(self, attempt: int) -> float:
        base = self.base_ms * (self.factor ** attempt)
        jitter = self.rng.uniform(0, self.jitter_ms) if self.jitter_ms else 0.0
        return (base + jitter) / 1000.0

    def wait(self, attempt: int) -> None:
        self.sleeper(self.delay_for(attempt))


def _structural_error(payload: Any, policy: SourcePolicy, url: str) -> Optional[str]:
    """返回结构问题描述；None 表示结构可接受。"""
    if payload is None:
        return "响应为空（null）"
    if not isinstance(payload, (dict, list)):
        return f"响应顶层类型异常: {type(payload).__name__}"
    if isinstance(payload, dict):
        missing = [key for key in policy.schema_keys if key not in payload]
        if missing:
            return f"缺少 schema_keys: {missing}"
    return None


class SourceAdapter:
    """受策略注册表门禁控制的单源适配器。"""

    source_id: str = ""
    adapter_version: str = ADAPTER_VERSION
    identifier_type: str = "GTIN"

    def __init__(
        self,
        registry: SourceRegistry,
        transport: Transport,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        rng: Optional[random.Random] = None,
        max_retries_override: Optional[int] = None,
    ):
        if not self.source_id:
            raise ValueError("adapter 必须声明 source_id")
        if self.source_id not in registry:
            raise PolicyGateError(
                f"数据源 {self.source_id} 未在 source_policy_registry.json 登记，适配器拒绝构造"
            )
        try:
            self.policy = registry.require_runnable(self.source_id)
        except PolicyGateError as exc:
            raise PolicyBlocked(str(exc), source_id=self.source_id, detail={"access_mode": registry.get(self.source_id).access_mode}) from exc

        self.registry = registry
        self.transport = transport
        self.throttle = ThrottleState(
            min_interval_ms=self.policy.rate_limit.min_interval_ms,
            requests_per_minute=self.policy.rate_limit.requests_per_minute,
            sleeper=sleeper,
        )
        retries = self.policy.backoff.max_retries if max_retries_override is None else max_retries_override
        if retries > self.policy.backoff.max_retries:
            raise PolicyGateError("禁止提升重试次数超出策略注册表上限")
        self.backoff = Backoff(
            base_ms=self.policy.backoff.base_ms,
            factor=self.policy.backoff.factor,
            jitter_ms=self.policy.backoff.jitter_ms,
            max_retries=retries,
            sleeper=sleeper,
            rng=rng,
        )
        self._broken = False
        self.requests_made = 0

    # ------------------------------------------------------------------ helpers
    def build_url(self, identifier: str) -> str:
        encoded = quote(str(identifier), safe="")
        return self.policy.endpoint_template.replace("{q}", encoded).replace(
            "{identifier}", encoded
        ).replace("{gtin}", encoded).replace("{standard_no}", encoded).replace("{di}", encoded)

    def _headers(self) -> Dict[str, str]:
        """凭证只从构建机环境变量读取，绝不进入请求参数或产物。"""
        import os

        headers = {"Accept": "application/json", "User-Agent": "product-public-dossier/offline-builder"}
        token_env = (self.policy.credentials or {}).get("token_env")
        app_env = (self.policy.credentials or {}).get("app_id_env")
        if token_env and os.environ.get(token_env):
            headers["Authorization"] = f"Bearer {os.environ[token_env]}"
        if app_env and os.environ.get(app_env):
            headers["X-App-Id"] = os.environ[app_env]
        return headers

    # ------------------------------------------------------------------- public
    def fetch(self, identifier: str) -> Dict[str, Any]:
        """取数并落账本。返回带 evidence_id 的观测单元。"""
        if self._broken:
            raise SourceFormatChanged(
                f"{self.source_id}: 已因结构变更熔断，禁止继续请求（需人工复核 schema）",
                source_id=self.source_id,
            )
        if not identifier:
            raise AdapterError("identifier 不得为空", source_id=self.source_id)

        url = self.build_url(identifier)
        params = {"q": identifier}
        last_error: Optional[Exception] = None

        for attempt in range(self.backoff.max_retries + 1):
            self.throttle.acquire()
            self.requests_made += 1
            try:
                payload = self.transport.fetch_json(
                    url,
                    params,
                    self._headers(),
                    source_id=self.source_id,
                    identifier=str(identifier),
                )
            except CaptchaOrLoginRequired as exc:
                raise CaptchaOrLoginRequired(str(exc), source_id=self.source_id, detail=exc.detail) from exc
            except AdapterError as exc:
                last_error = exc
                if exc.detail.get("status") == 429:
                    if attempt >= self.backoff.max_retries:
                        raise RateLimitedError(
                            f"{self.source_id}: 限流重试已耗尽，禁止突破限流",
                            source_id=self.source_id,
                        ) from exc
                self.backoff.wait(attempt)
                continue

            problem = _structural_error(payload, self.policy, url)
            if problem:
                self._broken = True
                raise SourceFormatChanged(
                    f"{self.source_id}: {problem} → 熔断，不猜测解析",
                    source_id=self.source_id,
                    detail={"url": url, "problem": problem, "schema_keys": self.policy.schema_keys},
                )

            record = ledger_mod.Ledger  # 仅为类型提示占位，实际落账由 builder 注入
            _ = record
            return {                "source_id": self.source_id,
                "source_key": identifier,
                "adapter_version": self.adapter_version,
                "identifier_type": self.identifier_type,
                "request_url": url,
                "request_params": params,
                "payload": payload,
                "requests_made": self.requests_made,
            }

        raise AdapterError(
            f"{self.source_id}: 取数失败（重试已耗尽）: {last_error}",
            source_id=self.source_id,
            detail={"url": url},
        )

    @property
    def broken(self) -> bool:
        return self._broken


def adapter_registry_map() -> Dict[str, str]:
    """各源适配器版本表，供构建记录与可复现性验收使用。"""
    from pipeline.adapters.v1 import ADAPTERS

    return {source_id: cls.adapter_version for source_id, cls in ADAPTERS.items()}




__all__ = [
    "ADAPTER_VERSION",
    "AdapterError",
    "Backoff",
    "CaptchaOrLoginRequired",
    "LocalFixtureTransport",
    "PolicyBlocked",
    "RateLimitedError",
    "SourceAdapter",
    "SourceFormatChanged",
    "StaticHttpTransport",
    "ThrottleState",
    "adapter_registry_map",
    "ERROR_CAPTCHA_OR_LOGIN",
    "ERROR_POLICY_BLOCKED",
    "ERROR_RATE_LIMITED",
    "ERROR_SOURCE_FORMAT_CHANGED",
    "ERROR_TRANSPORT",
]
