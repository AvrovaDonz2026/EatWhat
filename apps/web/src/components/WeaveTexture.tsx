/**
 * 织纹纸面背景层（React 封装）。
 *
 * - 无 GPU、省电、减弱动效、渲染失败 → 自动落到 `.weave-fallback` 的纯 CSS 细网格；
 * - 不发起任何网络请求；WGSL 内联在 `weave_texture.ts` 的模板字符串里。
 */
import { useEffect, useRef, useState } from "react";
import { mountWeaveTexture, prefersReducedMotion, type WeaveMode } from "../webgpu/weave_texture";

interface WeaveTextureProps {
  /** 顶部强调带的像素高度，纹理在此范围内更密。 */
  band?: number;
  className?: string;
}

export function WeaveTexture({ band = 176, className = "" }: WeaveTextureProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [mode, setMode] = useState<WeaveMode>("css");

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    // 减弱动效时连尝试都不做，直接静态 CSS 纹理
    if (prefersReducedMotion()) {
      canvas.classList.add("weave-fallback");
      setMode("css");
      return;
    }

    let handle: { mode: WeaveMode; dispose(): void } | null = null;
    let cancelled = false;

    void mountWeaveTexture(canvas, { band })
      .then((result) => {
        if (cancelled) {
          result.dispose();
          return;
        }
        handle = result;
        setMode(result.mode);
        if (result.mode === "css") canvas.classList.add("weave-fallback");
        else canvas.classList.remove("weave-fallback");
      })
      .catch(() => {
        canvas.classList.add("weave-fallback");
        setMode("css");
      });

    return () => {
      cancelled = true;
      handle?.dispose();
    };
  }, [band]);

  return (
    <div className={`weave-host ${className}`} aria-hidden="true" data-weave-mode={mode}>
      <canvas ref={canvasRef} className="weave-canvas" />
    </div>
  );
}
