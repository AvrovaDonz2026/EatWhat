"""语义门禁 CLI。

用法：
  python3 -m packages.semantic_gate.cli_scan --all
  python3 -m packages.semantic_gate.cli_scan --data dist_data --web apps/web/src apps/web/dist
退出码：0 = 通过；1 = 命中禁用词；2 = 扫描目标缺失。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Sequence

from packages.semantic_gate.rules import Finding, is_allowlisted, scan_file, scan_tree

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGETS = (
    "dist_data",
    "apps/web/src",
    "apps/web/index.html",
    "apps/web/dist",
)


def _scan_target(path: Path) -> List[Finding]:
    """扫描单个目标；门禁自身的规则定义与测试语料按其白名单豁免。"""
    if is_allowlisted(path, ROOT) and path.is_file():
        return []
    if path.is_dir():
        return scan_tree(path)
    if path.is_file():
        return scan_file(path)
    return []


def collect(targets: Sequence[str]) -> tuple[List[Finding], List[str]]:
    findings: List[Finding] = []
    missing: List[str] = []
    for raw in targets:
        path = Path(raw)
        if not path.is_absolute():
            path = ROOT / raw
        if not path.exists():
            missing.append(str(path))
            continue
        findings.extend(_scan_target(path))
    return findings, missing


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="附录 B 禁用词与风险配色静态扫描")
    parser.add_argument("--all", action="store_true", help="扫描默认目标（静态 JSON + 网页源码 + 打包产物）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    parser.add_argument("targets", nargs="*", help="待扫描的目录或文件")
    args = parser.parse_args(argv)

    targets = list(args.targets) or (list(DEFAULT_TARGETS) if args.all else [])
    if not targets:
        parser.error("请提供扫描目标，或使用 --all")

    findings, missing = collect(targets)

    if args.json:
        print(
            json.dumps(
                {
                    "targets": targets,
                    "missing": missing,
                    "finding_count": len(findings),
                    "findings": [f.as_dict() for f in findings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        if missing:
            for entry in missing:
                print(f"[skip] 目标不存在: {entry}", file=sys.stderr)
        if findings:
            for finding in findings:
                location = f"{finding.where}:{finding.line}" if finding.line else finding.where
                detail = f"{finding.node_path} " if finding.node_path else ""
                print(f"[FAIL] {location} {detail}「{finding.term}」({finding.category})")
                print(f"        …{finding.excerpt}…")
        else:
            print(f"[PASS] 语义门禁通过：扫描 {len(targets)} 个目标，未命中附录 B 禁用词")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
