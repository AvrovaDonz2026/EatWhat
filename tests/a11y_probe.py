"""无障碍复核：实测文字与背景的对比度、focus-visible、reduced-motion。

不属于验收门禁，只用于本轮改版的自查。
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.acceptance_gates.test_privacy_and_zero_cookie import (  # noqa: E402
    DIST,
    _find_full_chromium,
    _free_port,
)

from playwright.sync_api import sync_playwright  # noqa: E402

FULL = _find_full_chromium()

# 计算每个「有文字」的叶子元素在其实际背景色上的 WCAG 对比度
CONTRAST_JS = """
() => {
  const parse = (s) => {
    const m = s.match(/rgba?\\(([^)]+)\\)/);
    if (!m) return null;
    const p = m[1].split(/[,/]/).map((x) => parseFloat(x.trim()));
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const blend = (fg, bg) => ({
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a),
    a: 1,
  });
  const effBg = (el) => {
    let node = el, stack = [];
    while (node && node !== document.documentElement) {
      const c = parse(getComputedStyle(node).backgroundColor);
      if (c && c.a > 0.9) stack.push(c);
      else if (c && c.a > 0.02) stack.push(c);
      node = node.parentElement;
    }
    let bg = parse(getComputedStyle(document.body).backgroundColor) || { r: 255, g: 255, b: 255, a: 1 };
    for (const c of stack.reverse()) bg = blend(c, bg);
    return bg;
  };
  const lum = (c) => {
    const f = (v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const ratio = (a, b) => {
    const la = lum(a), lb = lum(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  };
  const fails = [];
  for (const el of document.querySelectorAll('*')) {
    if (!el.textContent || !el.textContent.trim()) continue;
    if ([...el.children].some((c) => c.textContent && c.textContent.trim())) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || parseFloat(st.opacity) < 0.1) continue;
    const fg = parse(st.color);
    if (!fg) continue;
    const bg = effBg(el);
    const r = ratio(blend(fg, bg), bg);
    const size = parseFloat(st.fontSize);
    const bold = parseInt(st.fontWeight, 10) >= 600;
    // 大字号（>=20px 粗体）门槛 3:1，其余 4.5:1
    const need = size >= 24 || (size >= 18.66 && bold) ? 3.0 : 4.5;
    if (r < need) {
      fails.push({
        text: el.textContent.trim().slice(0, 34),
        ratio: Math.round(r * 100) / 100,
        need,
        size,
        color: st.color,
        cls: (el.className || '').toString().slice(0, 40),
      });
    }
  }
  return { fails: fails.slice(0, 25), total: fails.length };
}
"""


def main() -> int:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(DIST)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                    break
            except OSError:
                time.sleep(0.1)

        with sync_playwright() as api:
            browser = api.chromium.launch(
                executable_path=FULL, args=["--enable-unsafe-webgpu", "--use-angle=metal"]
            )

            for tab in ("扫码看成分", "我的忌口与饮食计划", "原始记录与出处"):
                page = browser.new_page(viewport={"width": 1440, "height": 980})
                page.goto(f"http://127.0.0.1:{port}", wait_until="load")
                page.wait_for_timeout(900)
                if tab != "扫码看成分":
                    page.get_by_role("button", name=tab).click()
                    page.wait_for_timeout(700)
                result = page.evaluate(CONTRAST_JS)
                print(f"=== {tab} === 低于门槛的元素: {result['total']}")
                for item in result["fails"]:
                    print("  ", item)
                page.close()

            # focus-visible 与 reduced-motion
            page = browser.new_page(viewport={"width": 1440, "height": 980})
            page.goto(f"http://127.0.0.1:{port}", wait_until="load")
            page.wait_for_timeout(800)
            page.keyboard.press("Tab")
            print(
                "focus ring:",
                page.evaluate(
                    """() => {
                        const el = document.activeElement;
                        const st = getComputedStyle(el);
                        return { tag: el.tagName, outline: st.outlineWidth + ' ' + st.outlineStyle + ' ' + st.outlineColor, offset: st.outlineOffset };
                    }"""
                ),
            )
            page.close()

            reduced = browser.new_page(
                viewport={"width": 1440, "height": 980},
                reduced_motion="reduce",
            )
            reduced.goto(f"http://127.0.0.1:{port}", wait_until="load")
            reduced.wait_for_timeout(1200)
            print(
                "reduced-motion → weave mode:",
                reduced.evaluate("document.querySelector('.weave-host').dataset.weaveMode"),
                "| fallback:",
                reduced.evaluate("document.querySelector('.weave-canvas').classList.contains('weave-fallback')"),
            )
            reduced.close()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
