"""设计复核：从真实渲染的页面读取配色、字号、WebGPU 状态与无障碍指标。

不属于验收门禁，只用于本轮「编辑部风格 + WebGPU」改版的自动核对。
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
    _find_chromium_executable,
    _free_port,
)

from playwright.sync_api import sync_playwright  # noqa: E402

FORBIDDEN_HUE_JS = """
() => {
  // 遍历所有元素的计算样式，找出接近红/绿/黄/琥珀的色值
  const bad = [];
  const seen = new Set();
  const hueOf = (r, g, b) => {
    const max = Math.max(r, g, b), min = Math.min(r, g, b);
    if (max === min) return { h: -1, s: 0 };
    const d = max - min;
    const l = (max + min) / 2 / 255;
    const s = l > 0.5 ? d / (510 - max - min) : d / (max + min);
    let h;
    if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) * 60;
    else if (max === g) h = ((b - r) / d + 2) * 60;
    else h = ((r - g) / d + 4) * 60;
    return { h, s };
  };
  const parse = (s) => {
    const m = s.match(/rgba?\\(([^)]+)\\)/);
    if (!m) return null;
    const parts = m[1].split(/[,/]/).map((x) => parseFloat(x.trim()));
    return parts.length >= 3 ? parts : null;
  };
  for (const el of document.querySelectorAll('*')) {
    const st = getComputedStyle(el);
    for (const prop of ['color', 'backgroundColor', 'borderTopColor', 'borderBottomColor']) {
      const v = st[prop];
      if (!v || v === 'rgba(0, 0, 0, 0)') continue;
      if (seen.has(prop + v)) continue;
      seen.add(prop + v);
      const c = parse(v);
      if (!c) continue;
      const alpha = c.length > 3 ? c[3] : 1;
      if (alpha < 0.02) continue;
      const { h, s } = hueOf(c[0], c[1], c[2]);
      if (s < 0.25) continue;
      // 红 350-15, 黄/琥珀 35-70, 绿 80-160
      const redish = h >= 345 || h <= 15;
      const yellowish = h >= 30 && h <= 70;
      const greenish = h >= 75 && h <= 165;
      if (redish || yellowish || greenish) {
        bad.push({ tag: el.tagName, cls: (el.className || '').toString().slice(0, 60), prop, value: v, hue: Math.round(h), sat: Math.round(s * 100) });
      }
    }
  }
  return bad.slice(0, 20);
}
"""

FONTS_JS = """
() => {
  const sizes = new Map();
  const fams = new Map();
  for (const el of document.querySelectorAll('*')) {
    if (!el.textContent || !el.textContent.trim()) continue;
    if (el.children.length > 3) continue;
    const st = getComputedStyle(el);
    const px = Math.round(parseFloat(st.fontSize));
    sizes.set(px, (sizes.get(px) || 0) + 1);
    const fam = st.fontFamily.split(',')[0].replace(/["']/g, '').trim();
    fams.set(fam, (fams.get(fam) || 0) + 1);
  }
  return {
    sizes: [...sizes.entries()].sort((a, b) => a[0] - b[0]),
    families: [...fams.entries()].sort((a, b) => b[1] - a[1]),
  };
}
"""


def main() -> int:
    if not (DIST / "index.html").exists():
        print("请先构建 apps/web/dist")
        return 1

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

        requests: list[str] = []
        with sync_playwright() as api:
            executable = _find_chromium_executable()
            browser = (
                api.chromium.launch(executable_path=executable, args=["--enable-unsafe-webgpu"])
                if executable
                else api.chromium.launch(args=["--enable-unsafe-webgpu"])
            )
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("request", lambda r: requests.append(f"{r.method} {r.url}"))

            page.goto(f"http://127.0.0.1:{port}", wait_until="load")
            page.wait_for_timeout(1200)

            print("=== WebGPU 探测 ===")
            print(
                page.evaluate(
                    """async () => {
                        const hasGpu = !!navigator.gpu;
                        let adapter = null;
                        try { adapter = hasGpu ? await navigator.gpu.requestAdapter() : null; } catch (e) { adapter = 'throw'; }
                        return {
                            navigator_gpu: hasGpu,
                            adapter: adapter === null ? 'null' : (adapter === 'throw' ? 'throw' : 'ok'),
                            weave_mode: document.querySelector('.weave-host')?.getAttribute('data-weave-mode') ?? 'missing',
                            canvas_active: document.querySelector('.weave-canvas')?.dataset.weaveActive ?? 'n/a',
                            canvas_px: [document.querySelector('.weave-canvas')?.width, document.querySelector('.weave-canvas')?.height],
                            dpr: window.devicePixelRatio,
                        };
                    }"""
                )
            )

            print("=== 禁用的红/绿/黄语义配色（应为空）===")
            bad = page.evaluate(FORBIDDEN_HUE_JS)
            print(bad if bad else "无")

            print("=== 字号阶梯（页面实测）===")
            fonts = page.evaluate(FONTS_JS)
            print("sizes:", fonts["sizes"])
            print("families:", fonts["families"])

            print("=== 请求清单（应仅同源静态资源）===")
            for entry in requests:
                print(" ", entry)

            print("=== Cookie / 存储 ===")
            print(
                page.evaluate(
                    """() => ({cookie: document.cookie, ls: localStorage.length, ss: sessionStorage.length})"""
                )
            )

            print("=== 关键元素尺寸 ===")
            print(
                page.evaluate(
                    """() => {
                        const q = (s) => document.querySelector(s);
                        const box = (el) => el ? {w: Math.round(el.getBoundingClientRect().width), h: Math.round(el.getBoundingClientRect().height)} : null;
                        return {
                          masthead: box(q('.masthead')),
                          weave_host: box(q('.weave-host')),
                          section_rule: box(q('.section-rule')),
                          margin_col: box(q('.marginalia')),
                          field_input: box(q('.field-input')),
                        };
                    }"""
                )
            )

            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
