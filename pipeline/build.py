"""离线构建管线入口。

用法：
  python3 -m pipeline.build                      # 使用夹具构建 dist_data/
  python3 -m pipeline.build --rev2               # 追加第二版快照（演示历史保留）
  python3 -m pipeline.build --gtins 6901234567892
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from pipeline.builder.emit_static import (
    DEFAULT_DATA_DIR,
    DEFAULT_FIXTURES,
    DEFAULT_LEDGER,
    run_build,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线静态 JSON 构建（只追加账本）")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--gtins", default="", help="逗号分隔的条码列表，默认取夹具清单")
    parser.add_argument("--rev2", action="store_true", help="使用第二版夹具（模拟来源更新）")
    parser.add_argument("--note", default="")
    parser.add_argument("--reset", action="store_true", help="清空 dist_data（账本不清空，只追加）")
    parser.add_argument(
        "--reset-ledger",
        action="store_true",
        help="仅用于全新基线演示：丢弃账本文件重来；正常运行请勿使用（账本只追加）",
    )
    args = parser.parse_args(argv)

    if args.reset and args.data_dir.exists():
        shutil.rmtree(args.data_dir)
    if args.reset_ledger and args.ledger.exists():
        args.ledger.unlink()

    fixtures = args.fixtures
    if args.rev2:
        fixtures = DEFAULT_FIXTURES.parent / "v1_rev2"

    gtins = [g.strip() for g in args.gtins.split(",") if g.strip()] or None

    hints_path = next(
        (c for c in (fixtures / "description_hints.json", DEFAULT_FIXTURES / "description_hints.json") if c.exists()),
        None,
    )
    description_hints = None
    if hints_path is not None:
        import json as _json

        description_hints = _json.loads(hints_path.read_text(encoding="utf-8"))
        description_hints.pop("_note", None)

    outcome = run_build(
        data_dir=args.data_dir,
        ledger_path=args.ledger,
        fixtures_root=fixtures,
        gtins=gtins,
        description_hints=description_hints,
        note=args.note or ("rev2" if args.rev2 else "baseline"),
    )

    summary = {
        "build_id": outcome.build_id,
        "artifacts": len(outcome.artifacts),
        "gtins": len(outcome.gtin_files),
        "failures": outcome.failures,
        "manifest": str(outcome.manifest_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
