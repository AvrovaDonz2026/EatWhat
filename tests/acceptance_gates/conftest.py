"""验收测试公共夹具：临时账本 + 独立静态产物目录 + 夹具数据。"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Callable, Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(ROOT))

from pipeline.adapters.base import LocalFixtureTransport  # noqa: E402
from pipeline.adapters.v1 import ADAPTERS  # noqa: E402
from pipeline.builder.emit_static import Builder, entity_id_for  # noqa: E402
from pipeline.builder.ledger import Ledger  # noqa: E402
from pipeline.config.policy import load_registry  # noqa: E402

FIXTURES = ROOT / "pipeline" / "fixtures"
FIXTURE_V1 = FIXTURES / "v1"
FIXTURE_REV2 = FIXTURES / "v1_rev2"


class SilentSleeper:
    """记录退避等待而不真实睡，保证测试快速。"""

    def __init__(self) -> None:
        self.calls: List[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture()
def registry():
    return load_registry()


@pytest.fixture()
def ledger(tmp_path: Path) -> Ledger:
    instance = Ledger(tmp_path / "ledger.sqlite3")
    yield instance
    instance.close()


@pytest.fixture()
def sleeper() -> SilentSleeper:
    return SilentSleeper()


@pytest.fixture()
def make_builder(tmp_path: Path, ledger: Ledger, registry, sleeper: SilentSleeper) -> Callable[..., Builder]:
    def factory(*, data_dir: Path | None = None, fixtures_root: Path = FIXTURE_V1, scenarios=None) -> Builder:
        builder = Builder(
            registry=registry,
            ledger=ledger,
            data_dir=data_dir or (tmp_path / "dist_data"),
            fixtures_root=fixtures_root,
            sleeper=sleeper,
            rng=__import__("random").Random(7),
        )
        if scenarios:
            builder.transport = LocalFixtureTransport(fixtures_root, scenarios=scenarios)
        return builder

    return factory


@pytest.fixture()
def built(tmp_path: Path, make_builder, ledger: Ledger) -> Dict[str, object]:
    """基线构建产物（三个夹具条码）。"""
    builder = make_builder(data_dir=tmp_path / "dist_data", fixtures_root=FIXTURE_V1)
    hints = json.loads((FIXTURE_V1 / "description_hints.json").read_text(encoding="utf-8"))
    hints.pop("_note", None)
    outcome = builder.build(
        gtins=["6901234567892", "6905554443331", "6901112223339"],
        description_hints=hints,
        build_id="build_test_baseline",
        note="baseline",
    )
    return {"outcome": outcome, "data_dir": tmp_path / "dist_data", "ledger": ledger}


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


__all__ = [
    "SilentSleeper",
    "ROOT",
    "FIXTURES",
    "FIXTURE_V1",
    "FIXTURE_REV2",
    "ADAPTERS",
    "Builder",
    "entity_id_for",
    "read_json",
]
