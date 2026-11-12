"""附录 B 禁用词语义门禁。

扫描范围：
- 静态 JSON 产物
- 网页源码（.ts/.tsx/.html/.css/.js）
- 打包后的 dist/index.html

豁免：`raw_value` 字段的字符串值可以原样保留来源原文（来源自己写的原文不得被改写）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

#: 附录 B 六类禁用词。每条附带类别与说明，便于给出可定位的报告。
BANNED_TERMS: Sequence[Dict[str, str]] = (
    # 1) 风险/安全评价
    {"term": "高风险", "category": "RISK_ASSESSMENT", "note": "风险/安全评价"},
    {"term": "低风险", "category": "RISK_ASSESSMENT", "note": "风险/安全评价"},
    {"term": "危险", "category": "RISK_ASSESSMENT", "note": "风险/安全评价"},
    {"term": "值得警惕", "category": "RISK_ASSESSMENT", "note": "风险/安全评价"},
    {"term": "可信度", "category": "RISK_ASSESSMENT", "note": "风险/安全评价"},
    {"term": "安全等级", "category": "RISK_ASSESSMENT", "note": "风险/安全评价"},
    # 2) 真伪/合法评价
    {"term": "假货", "category": "AUTHENTICITY", "note": "真伪评价"},
    {"term": "真货", "category": "AUTHENTICITY", "note": "真伪评价"},
    {"term": "非法", "category": "LEGALITY", "note": "合法/违法评价"},
    {"term": "违法", "category": "LEGALITY", "note": "合法/违法评价"},
    {"term": "未备案", "category": "LEGALITY", "note": "合法/违法评价"},
    # 3) 关系推断
    {"term": "同源", "category": "RELATION_INFERENCE", "note": "关系推断"},
    {"term": "换壳", "category": "RELATION_INFERENCE", "note": "关系推断"},
    {"term": "关联企业", "category": "RELATION_INFERENCE", "note": "关系推断"},
    {"term": "实际控制", "category": "RELATION_INFERENCE", "note": "关系推断"},
    {"term": "背后是同一批人", "category": "RELATION_INFERENCE", "note": "关系推断"},
    # 4) 购买建议
    {"term": "推荐", "category": "PURCHASE_ADVICE", "note": "购买建议"},
    {"term": "不推荐", "category": "PURCHASE_ADVICE", "note": "购买建议"},
    {"term": "可以买", "category": "PURCHASE_ADVICE", "note": "购买建议"},
    {"term": "不要买", "category": "PURCHASE_ADVICE", "note": "购买建议"},
    {"term": "谨慎购买", "category": "PURCHASE_ADVICE", "note": "购买建议"},
    {"term": "推荐指数", "category": "PURCHASE_ADVICE", "note": "购买建议"},
    # 5) 动机/主观评价
    {"term": "欺骗", "category": "MOTIVE_JUDGEMENT", "note": "动机/主观评价"},
    {"term": "擦边", "category": "MOTIVE_JUDGEMENT", "note": "动机/主观评价"},
    {"term": "故意隐瞒", "category": "MOTIVE_JUDGEMENT", "note": "动机/主观评价"},
    {"term": "恶意", "category": "MOTIVE_JUDGEMENT", "note": "动机/主观评价"},
    # 6) 学术功效评价
    {"term": "论文证明该商品有效", "category": "EFFICACY_CLAIM", "note": "学术功效评价"},
    {"term": "论文不支持该商品功效", "category": "EFFICACY_CLAIM", "note": "学术功效评价"},
)

#: 红绿灯风险配色（UI 层禁止以颜色编码风险等级）
FORBIDDEN_COLOR_PATTERNS: Sequence[Dict[str, str]] = (
    {"pattern": r"(风险|安全|评级)[^。\n]{0,12}(红灯|绿灯|黄灯)", "note": "红绿灯风险配色"},
    {"pattern": r"(红/黄/绿|红黄绿)[^。\n]{0,10}(风险|评级)", "note": "红绿灯风险配色"},
    {"pattern": r"risk[-_ ]?(light|level|score|grade|traffic)", "note": "风险评分/红绿灯", "flags": "i"},
    {"pattern": r"(score|rating)[-_ ]?(grade|badge)[^。\n]{0,10}(danger|safe)", "note": "风险评分徽标", "flags": "i"},
    {"pattern": r"bg-red-\d+[^\"']{0,40}(风险|不合格|fail)", "note": "红色风险徽标", "flags": "i"},
)

#: 这些键的值属于「来源原文」，允许原样保留来源自己的措辞
RAW_VALUE_KEYS = frozenset({"raw_value", "raw_text", "raw_note", "raw_name", "raw_nutrient"})

#: 这些路径片段属于测试/门禁用例或说明，不参与产物扫描
_SCAN_SKIP_PARTS = frozenset({"node_modules", ".git", "dist_data", "__pycache__", ".venv"})

SOURCE_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".html", ".css", ".json", ".md")


@dataclass(frozen=True)
class Finding:
    term: str
    category: str
    where: str
    line: Optional[int]
    excerpt: str
    node_path: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "term": self.term,
            "category": self.category,
            "where": self.where,
            "line": self.line,
            "excerpt": self.excerpt,
            "node_path": self.node_path,
        }


def _escaped(term: str) -> re.Pattern[str]:
    return re.compile(re.escape(term))


_TERM_PATTERNS: Dict[str, re.Pattern[str]] = {item["term"]: _escaped(item["term"]) for item in BANNED_TERMS}
_TERM_META: Dict[str, Dict[str, str]] = {item["term"]: item for item in BANNED_TERMS}
_COLOR_PATTERNS = [
    (re.compile(item["pattern"], re.IGNORECASE if item.get("flags") == "i" else 0), item["note"])
    for item in FORBIDDEN_COLOR_PATTERNS
]


def scan_text(text: str, where: str) -> List[Finding]:
    """扫描纯文本（源码 / 非 JSON 文本）。"""
    findings: List[Finding] = []
    for term, pattern in _TERM_PATTERNS.items():
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(
                Finding(
                    term=term,
                    category=_TERM_META[term]["category"],
                    where=where,
                    line=line,
                    excerpt=_excerpt(text, match.start(), match.end()),
                )
            )
    for pattern, note in _COLOR_PATTERNS:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(
                Finding(
                    term=match.group(0)[:40],
                    category="RISK_COLOR_CODE",
                    where=where,
                    line=line,
                    excerpt=_excerpt(text, match.start(), match.end()),
                )
            )
    return findings


def _excerpt(text: str, start: int, end: int, width: int = 60) -> str:
    left = max(0, start - width)
    right = min(len(text), end + width)
    return text[left:right].replace("\n", " ").strip()


def scan_json(payload: Any, where: str, *, allow_raw_value: bool = True) -> List[Finding]:
    """结构化扫描：raw_value 系字段豁免，其余字符串字段一律不得出现禁用词。"""
    findings: List[Finding] = []

    def walk(node: Any, path: str, key: str) -> None:
        if isinstance(node, dict):
            for k, value in node.items():
                walk(value, f"{path}.{k}", str(k))
            return
        if isinstance(node, (list, tuple)):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]", key)
            return
        if not isinstance(node, str):
            return
        exempt = allow_raw_value and key in RAW_VALUE_KEYS
        if exempt:
            return
        for term, pattern in _TERM_PATTERNS.items():
            if pattern.search(node):
                findings.append(
                    Finding(
                        term=term,
                        category=_TERM_META[term]["category"],
                        where=where,
                        line=None,
                        excerpt=_excerpt(node, 0, len(node), width=40),
                        node_path=path,
                    )
                )
        if key in RAW_VALUE_KEYS:
            return
        for pattern, note in _COLOR_PATTERNS:
            match = pattern.search(node)
            if match:
                findings.append(
                    Finding(
                        term=match.group(0)[:40],
                        category="RISK_COLOR_CODE",
                        where=where,
                        line=None,
                        excerpt=_excerpt(node, match.start(), match.end()),
                        node_path=path,
                    )
                )

    walk(payload, "$", "")
    return findings


def iter_source_files(root, *, extensions: Iterable[str] = SOURCE_EXTENSIONS) -> Iterator:
    from pathlib import Path

    root = Path(root)
    exts = tuple(extensions)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix not in exts:
            continue
        if _SCAN_SKIP_PARTS & set(path.parts):
            continue
        yield path


def scan_file(path) -> List[Finding]:
    from pathlib import Path

    path = Path(path)
    if path.suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return scan_text(path.read_text(encoding="utf-8"), str(path))
        return scan_json(payload, str(path))
    return scan_text(path.read_text(encoding="utf-8"), str(path))


#: 允许在源码中出现禁用词字面量的白名单文件（门禁自身、测试语料、文档）
ALLOWLIST_FILES = (
    "packages/semantic_gate/rules.py",
    "packages/semantic_gate/cli_scan.py",
    "packages/semantic_gate/adversarial_corpus.json",
    "tests/acceptance_gates/test_zero_inference_gate.py",
    "tests/acceptance_gates/conftest.py",
    "README.md",
)


def is_allowlisted(path, root) -> bool:
    from pathlib import Path

    try:
        rel = Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return False
    return any(rel == entry or rel.endswith("/" + entry) for entry in ALLOWLIST_FILES)


def scan_tree(root, *, include_allowlisted: bool = False) -> List[Finding]:
    findings: List[Finding] = []
    for path in iter_source_files(root):
        if not include_allowlisted and is_allowlisted(path, root):
            continue
        findings.extend(scan_file(path))
    return findings


__all__ = [
    "ALLOWLIST_FILES",
    "BANNED_TERMS",
    "FORBIDDEN_COLOR_PATTERNS",
    "Finding",
    "RAW_VALUE_KEYS",
    "is_allowlisted",
    "iter_source_files",
    "scan_file",
    "scan_json",
    "scan_text",
    "scan_tree",
]
