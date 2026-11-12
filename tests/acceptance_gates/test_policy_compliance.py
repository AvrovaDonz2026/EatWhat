"""门禁 6：未通过策略注册表门禁的适配器无法运行，且产物无密钥泄露。"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pipeline.adapters.base import (
    AdapterError,
    CaptchaOrLoginRequired,
    LocalFixtureTransport,
    PolicyBlocked,
    RateLimitedError,
    StaticHttpTransport,
    SourceFormatChanged,
)
from pipeline.adapters.v1 import ADAPTERS, adapter_classes_for_runnable
from pipeline.builder.ledger import LedgerError, assert_no_credentials, redact
from pipeline.config.policy import PolicyError, PolicyGateError, load_registry
from tests.acceptance_gates.conftest import FIXTURE_V1

SECRET = "sk_live_9f8e7d6c5b4a3210"
APP_ID = "appid_7f6e5d4c3b2a1908"


def test_unregistered_source_cannot_construct(registry, sleeper):
    class GhostAdapter(ADAPTERS["gs1"]):  # type: ignore[misc]
        source_id = "ghost_source"

    with pytest.raises(PolicyGateError):
        GhostAdapter(registry, LocalFixtureTransport(FIXTURE_V1), sleeper=sleeper)


def test_access_pending_source_is_blocked(registry, sleeper):
    """ACCESS_PENDING / SOURCE_UNAVAILABLE 的源必须被门禁直接阻断。"""
    for source_id in ("cnipa", "cifer"):
        assert registry.get(source_id).access_mode in ("ACCESS_PENDING", "SOURCE_UNAVAILABLE")
        with pytest.raises(PolicyBlocked):
            ADAPTERS[source_id](registry, LocalFixtureTransport(FIXTURE_V1), sleeper=sleeper)


def test_runnable_set_excludes_blocked_sources(registry):
    runnable = adapter_classes_for_runnable(registry)
    assert "cnipa" not in runnable
    assert "cifer" not in runnable
    assert {"udi", "gs1", "samr_gov", "samr_std", "label_transcript"} <= set(runnable)
    for source_id in runnable:
        assert registry.get(source_id).runnable


def test_rate_limit_cannot_be_bypassed_by_retries(registry, sleeper):
    """禁止提升重试次数超出注册表上限。"""
    policy = registry.get("udi")
    with pytest.raises(PolicyGateError):
        ADAPTERS["udi"](
            registry,
            LocalFixtureTransport(FIXTURE_V1),
            sleeper=sleeper,
            max_retries_override=policy.backoff.max_retries + 3,
        )


def test_online_transport_disabled_by_default(registry, sleeper):
    """默认不允许线上采集：静态 HTTP 通道必须拒绝。"""
    transport = StaticHttpTransport(allowed_hosts=["udi.example.gov.cn"])
    adapter = ADAPTERS["udi"](registry, transport, sleeper=sleeper)
    with pytest.raises(AdapterError):
        adapter.fetch("6901234567892")


def test_captcha_triggers_stop_not_bypass(registry, sleeper):
    """命中验证码/登录墙必须立即终止，绝不尝试绕过。"""
    transport = LocalFixtureTransport(FIXTURE_V1, scenarios={"udi:6901234567892": "captcha"})
    adapter = ADAPTERS["udi"](registry, transport, sleeper=sleeper)
    with pytest.raises(CaptchaOrLoginRequired):
        adapter.fetch("6901234567892")


def test_429_exhaustion_raises_without_breaking_limit(registry, sleeper):
    transport = LocalFixtureTransport(FIXTURE_V1, scenarios={"udi:6901234567892": "rate_limited"})
    adapter = ADAPTERS["udi"](registry, transport, sleeper=sleeper)
    with pytest.raises(RateLimitedError):
        adapter.fetch("6901234567892")


def test_schema_change_circuit_breaker(registry, sleeper):
    """结构变更必须熔断并抛 SOURCE_FORMAT_CHANGED，而不是猜测解析。"""
    transport = LocalFixtureTransport(FIXTURE_V1, scenarios={"udi:6901234567892": "format_changed"})
    adapter = ADAPTERS["udi"](registry, transport, sleeper=sleeper)
    with pytest.raises(SourceFormatChanged) as excinfo:
        adapter.fetch("6901234567892")
    assert excinfo.value.code == "SOURCE_FORMAT_CHANGED"
    assert adapter.broken is True
    # 熔断后不再发起请求
    with pytest.raises(SourceFormatChanged):
        adapter.fetch("6901234567892")
    assert adapter.requests_made == 1


def test_registry_rejects_secret_literal(tmp_path):
    """注册表里写入真实凭据字面值必须被拒收。"""
    from pipeline.config import policy as policy_mod

    path = Path(policy_mod.__file__).with_name("source_policy_registry.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["sources"][0]["credentials"]["token_env"] = SECRET
    bad = tmp_path / "bad_registry.json"
    bad.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PolicyError):
        load_registry(bad)


def test_artifacts_contain_no_credentials(tmp_path, make_builder, ledger):
    """构建产物与账本不得出现任何凭据。"""
    os.environ["SAMR_GOV_TOKEN"] = SECRET
    os.environ["CNIPA_APP_ID"] = APP_ID
    try:
        hints = json.loads((FIXTURE_V1 / "description_hints.json").read_text(encoding="utf-8"))
        hints.pop("_note", None)
        data_dir = tmp_path / "dist_data"
        builder = make_builder(data_dir=data_dir, fixtures_root=FIXTURE_V1)
        builder.build(gtins=["6901234567892"], description_hints=hints, build_id="build_policy_probe")

        blob_checks = []
        for path in data_dir.rglob("*.json"):
            text = path.read_text(encoding="utf-8")
            assert_no_credentials(text, str(path))
            for needle in (SECRET, APP_ID, "SAMR_GOV_TOKEN", "CNIPA_APP_ID", "Authorization"):
                assert needle not in text, f"{path.name}: 产物泄露 {needle}"
            blob_checks.append(path)
        assert blob_checks

        for snapshot in ledger.snapshots():
            payload_text = json.dumps(snapshot.payload, ensure_ascii=False)
            assert SECRET not in payload_text
            assert APP_ID not in payload_text
            assert "Authorization" not in payload_text
    finally:
        os.environ.pop("SAMR_GOV_TOKEN", None)
        os.environ.pop("CNIPA_APP_ID", None)


def test_redaction_removes_credentials():
    node = {
        "token": "abc123",
        "Authorization": "Bearer abcdefghijklmnop",
        "credentials": {"appId": "x", "token_env": "SAMR_GOV_TOKEN"},
        "safe": "全脂乳粉",
        "payload_sha256": "a" * 64,
    }
    redacted = redact(node)
    assert redacted["token"] == "<redacted>"
    assert redacted["Authorization"] == "<redacted>"
    assert redacted["safe"] == "全脂乳粉"
    assert redacted["payload_sha256"] == "a" * 64
    with pytest.raises(LedgerError):
        assert_no_credentials({"nested": {"token": "value"}}, "test")


def test_forbidden_actions_declared(registry):
    """注册表必须声明禁止动作（验证码、限流、凭据落盘）。"""
    for action in ("captcha_bypass", "rate_limit_override", "credential_in_artifact"):
        assert action in registry.forbidden_actions
