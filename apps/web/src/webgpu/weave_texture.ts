/**
 * 织纹纸面：单个全屏渲染 pass 的 WebGPU 背景层。
 *
 * 设计意图（只为版面服务，绝不作为装饰凑数）：
 * - 在页眉带与版心留白（gutter）后方绘制极低对比的织纹细网格；
 * - 让纸面有织物/纤维的触感，避免大面积纯色显得像通用后台模板。
 *
 * 硬约束：
 * - 只有 1 个 pass、1 个全屏三角形、1 个 uniform buffer；无 compute、无后处理；
 * - WGSL 以模板字符串内联，不新增 .wgsl 文件 / 资源 / wasm，单文件打包后仍是自包含 HTML；
 * - 不产生任何网络请求；
 * - devicePixelRatio 上限 1.5；
 * - 标签页隐藏、元素滚出视口、`prefers-reduced-motion: reduce`、
 *   电池放电且低于 20% → 一律停帧（或完全不启动）；
 * - 整个模块被 try/catch 包裹，任何 GPU 失败都静默回退到纯 CSS。
 */

const WGSL = /* wgsl */ `
struct Uniforms {
  resolution : vec2f,
  dpi        : f32,
  time       : f32,
  band       : f32,
  _pad       : f32,
};

@group(0) @binding(0) var<uniform> u : Uniforms;

struct VSOut {
  @builtin(position) position : vec4f,
  @location(0) css : vec2f,
};

@vertex
fn vs(@builtin(vertex_index) vertexIndex : u32) -> VSOut {
  // 单个全屏三角形：比两个三角形的 quad 少一次光栅化接缝
  var corners = array<vec2f, 3>(
    vec2f(-1.0, -1.0),
    vec2f( 3.0, -1.0),
    vec2f(-1.0,  3.0),
  );
  let p = corners[vertexIndex];
  var out : VSOut;
  out.position = vec4f(p, 0.0, 1.0);
  // 设备像素 → CSS 像素，保证纹理粗细与缩放无关
  out.css = vec2f(
    (p.x + 1.0) * 0.5 * u.resolution.x,
    (1.0 - p.y) * 0.5 * u.resolution.y
  ) / u.dpi;
  return out;
}

// 到最近一条线的距离，用于画发丝线
fn hairline(coord : f32, pitch : f32, thickness : f32) -> f32 {
  let d = abs(fract(coord / pitch + 0.5) - 0.5) * pitch;
  return 1.0 - smoothstep(0.0, thickness, d);
}

fn hash21(p : vec2f) -> f32 {
  var q = fract(p * vec2f(0.1031, 0.1030));
  q = q + vec2f(dot(q, q.yx + 19.19));
  return fract((q.x + q.y) * q.x);
}

// 织物纤维：两向交错的细微波纹，几乎不可察觉但能消除纯色死板
fn fiber(cssPos : vec2f, t : f32) -> f32 {
  var acc = 0.0;
  var p = cssPos;
  for (var i = 0; i < 2; i = i + 1) {
    let cell = floor(p / 3.0);
    acc = acc + hash21(cell + vec2f(f32(i) * 7.0, t * 0.06));
    p = p * 1.73 + vec2f(11.0, 5.0);
  }
  return acc * 0.5;
}

@fragment
fn fs(in : VSOut) -> @location(0) vec4f {
  let cssPos = in.css;
  let t = u.time;

  // 正交细网格（4px 栅格）+ 更粗的交织线（16px 栅格）
  let fineP = 4.0;
  let coarseP = 16.0;

  let vFine = hairline(cssPos.x, fineP, 0.55);
  let hFine = hairline(cssPos.y, fineP, 0.55);
  let vWide = hairline(cssPos.x, coarseP, 1.1);
  let hWide = hairline(cssPos.y, coarseP, 1.1);

  let f = fiber(cssPos, t);

  // 顶部强调带更密一点，向下渐渐稀疏：只做空间上的轻重，不表达任何判断
  let w = mix(1.0, 0.34, smoothstep(u.band, u.band + 300.0, cssPos.y));

  var a = 0.0;
  a = a + vFine * 0.030 + hFine * 0.036;
  a = a + vWide * 0.038 + hWide * 0.046;
  a = a + (f - 0.5) * 0.038;
  a = max(a, 0.0) * w;
  a = clamp(a * 3.6, 0.0, 0.16);

  // 画布使用 premultiplied alpha：颜色必须预先乘以 alpha，否则会被合成器裁掉。
  let ink = vec3f(0.078, 0.129, 0.157);
  return vec4f(ink * a, a);
}
`;

/** 本模块对外的最小信息，便于界面在无 GPU 时给出 CSS 兜底。 */
export type WeaveMode = "gpu" | "css";

export interface WeaveHandle {
  mode: WeaveMode;
  /** 主动停止帧循环（例如组件卸载）。 */
  dispose(): void;
}

const MAX_DPR = 1.5;

interface BatteryLike {
  level: number;
  charging: boolean;
}

interface GpuNavigator extends Navigator {
  gpu?: {
    requestAdapter(options?: { powerPreference?: string }): Promise<GpuAdapter | null>;
    getPreferredCanvasFormat(): string;
  };
  getBattery?: () => Promise<BatteryLike>;
}

/** 极简 WebGPU 结构声明：避免为几个类型新增依赖，同时保持类型可读。 */
interface GpuAdapter {
  requestDevice(): Promise<GpuDevice>;
}
interface GpuDevice {
  lost: Promise<{ message?: string }>;
  destroy(): void;
  createShaderModule(descriptor: { code: string; label?: string }): unknown;
  createRenderPipeline(descriptor: unknown): GpuRenderPipeline;
  createBindGroup(descriptor: unknown): unknown;
  createBuffer(descriptor: { size: number; usage: number; label?: string }): GpuBuffer;
  queue: { writeBuffer(buffer: GpuBuffer, offset: number, data: BufferSource): void; submit(buffers: unknown[]): void };
  createCommandEncoder(): GpuCommandEncoder;
  pushErrorScope(filter: string): void;
  popErrorScope(): Promise<{ message?: string } | null>;
  onuncapturederror: ((event: unknown) => void) | null;
}
interface GpuBuffer {
  destroy(): void;
}
interface GpuRenderPipeline {
  getBindGroupLayout(index: number): unknown;
}
interface GpuCommandEncoder {
  beginRenderPass(descriptor: unknown): GpuRenderPassEncoder;
  finish(): unknown;
}
interface GpuRenderPassEncoder {
  setPipeline(pipeline: GpuRenderPipeline): void;
  setBindGroup(index: number, bindGroup: unknown): void;
  draw(vertexCount: number, instanceCount?: number): void;
  end(): void;
}
interface GpuCanvasContext {
  configure(configuration: unknown): void;
  getCurrentTexture(): { createView(): unknown };
}

const GPUBufferUsage = { UNIFORM: 0x0040, COPY_DST: 0x0008 };

export function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

/** 电池放电且低于 20% 时不启动 GPU 层（省电优先）。 */
async function batteryIsLow(): Promise<boolean> {
  try {
    const nav = navigator as GpuNavigator;
    if (typeof nav.getBattery !== "function") return false;
    const battery = await nav.getBattery();
    return battery.charging === false && battery.level < 0.2;
  } catch {
    return false;
  }
}

/**
 * 在给定 canvas 上挂载织纹纸面。
 * 任何一步失败都返回 `{ mode: "css" }`，调用方据此启用 CSS 兜底。
 */
export async function mountWeaveTexture(
  canvas: HTMLCanvasElement,
  options: { band?: number } = {},
): Promise<WeaveHandle> {
  const cssFallback: WeaveHandle = { mode: "css", dispose() {} };

  try {
    if (prefersReducedMotion()) return cssFallback;
    if (await batteryIsLow()) return cssFallback;

    const nav = navigator as GpuNavigator;
    if (!nav.gpu || typeof nav.gpu.requestAdapter !== "function") return cssFallback;

    const adapter = await nav.gpu.requestAdapter({ powerPreference: "low-power" });
    if (!adapter) return cssFallback;

    const device = await adapter.requestDevice();
    let disposed = false;
    let rafId = 0;
    let running = false;

    const context = canvas.getContext("webgpu") as unknown as GpuCanvasContext | null;
    if (!context) {
      device.destroy();
      return cssFallback;
    }

    const format = nav.gpu.getPreferredCanvasFormat();

    // 4 个 f32（resolution.xy, dpi, time）+ 2 个 f32（band, pad）→ 32 字节，按 16 对齐
    const UNIFORM_FLOATS = 8;
    const uniformBuffer = device.createBuffer({
      size: UNIFORM_FLOATS * 4,
      usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST,
      label: "weave-uniforms",
    });
    const uniformData = new Float32Array(UNIFORM_FLOATS);

    const shader = device.createShaderModule({ code: WGSL, label: "weave-shader" });

    const pipeline = device.createRenderPipeline({
      label: "weave-pipeline",
      layout: "auto",
      vertex: { module: shader, entryPoint: "vs" },
      fragment: { module: shader, entryPoint: "fs", targets: [{ format }] },
      primitive: { topology: "triangle-list" },
    });

    const bindGroup = device.createBindGroup({
      layout: pipeline.getBindGroupLayout(0),
      entries: [{ binding: 0, resource: { buffer: uniformBuffer } }],
    });

    const band = Math.max(0, options.band ?? 180);
    let pixelRatio = Math.min(window.devicePixelRatio || 1, MAX_DPR);

    // configure 只在尺寸变化时执行：每帧重复 configure 会让上一帧的纹理失效
    let configured = false;

    const configure = () => {
      const rect = canvas.getBoundingClientRect();
      const cssWidth = Math.max(1, Math.round(rect.width));
      const cssHeight = Math.max(1, Math.round(rect.height));
      pixelRatio = Math.min(window.devicePixelRatio || 1, MAX_DPR);
      const width = Math.max(1, Math.round(cssWidth * pixelRatio));
      const height = Math.max(1, Math.round(cssHeight * pixelRatio));
      const sizeChanged = canvas.width !== width || canvas.height !== height;
      if (sizeChanged) {
        canvas.width = width;
        canvas.height = height;
      }
      if (!configured || sizeChanged) {
        context.configure({
          device,
          format,
          alphaMode: "premultiplied",
        });
        configured = true;
      }
    };

    configure();

    // 试渲染一帧：某些 headless / 无合成器的环境里 configure 成功但画布纹理不可用。
    // 这时必须整体降级到 CSS 纹理，而不是留下一个永远空白的画布。
    const drawFrame = (nowMs: number) => {
      uniformData[0] = canvas.width;
      uniformData[1] = canvas.height;
      uniformData[2] = pixelRatio;
      uniformData[3] = nowMs / 1000;
      uniformData[4] = band;
      uniformData[5] = 0;
      device.queue.writeBuffer(uniformBuffer, 0, uniformData);

      const encoder = device.createCommandEncoder();
      const pass = encoder.beginRenderPass({
        colorAttachments: [
          {
            view: context.getCurrentTexture().createView(),
            clearValue: { r: 0, g: 0, b: 0, a: 0 },
            loadOp: "clear",
            storeOp: "store",
          },
        ],
      });
      pass.setPipeline(pipeline);
      pass.setBindGroup(0, bindGroup);
      pass.draw(3, 1);
      pass.end();
      device.queue.submit([encoder.finish()]);
    };

    try {
      device.pushErrorScope("validation");
      drawFrame(0);
      const trialError = await device.popErrorScope();
      if (trialError) {
        device.destroy();
        canvas.dataset.weaveActive = "0";
        return cssFallback;
      }
    } catch {
      device.destroy();
      canvas.dataset.weaveActive = "0";
      return cssFallback;
    }

    const render = (nowMs: number) => {
      if (disposed || !running) return;
      try {
        drawFrame(nowMs);
      } catch {
        // 单帧失败即停帧，并按 CSS 纹理兜底；页面其余部分不受影响
        stop();
        canvas.dataset.weaveActive = "0";
        canvas.classList.add("weave-fallback");
        return;
      }

      rafId = window.requestAnimationFrame(render);
    };

    const start = () => {
      if (disposed || running) return;
      running = true;
      canvas.dataset.weaveActive = "1";
      rafId = window.requestAnimationFrame((t) => render(t));
    };

    const stop = () => {
      running = false;
      canvas.dataset.weaveActive = "0";
      if (rafId) window.cancelAnimationFrame(rafId);
      rafId = 0;
    };

    configure();

    // 视口内才跑；滚出视口 / 切到后台 / 窗口过小 一律停帧
    let visible = true;
    const intersectionObserver =
      typeof IntersectionObserver === "function"
        ? new IntersectionObserver(
            (entries) => {
              visible = entries.some((entry) => entry.isIntersecting);
              if (visible && !document.hidden) start();
              else stop();
            },
            { threshold: 0.01 },
          )
        : null;
    intersectionObserver?.observe(canvas);

    const onVisibility = () => {
      if (document.hidden) stop();
      else if (visible) start();
    };
    const onResize = () => {
      configure();
      if (visible && !document.hidden) start();
    };

    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("resize", onResize);
    window.addEventListener("orientationchange", onResize);

    // 纹理是静态图案：先画一帧，之后只在需要时继续跑，避免无谓耗电
    if (visible && !document.hidden) start();

    // GPU 丢失（驱动重置 / 设备休眠）→ 静默降级，页面照常可用
    device.lost.then(() => {
      stop();
      canvas.dataset.weaveActive = "0";
      canvas.classList.add("weave-fallback");
    });
    device.onuncapturederror = () => {
      stop();
      canvas.dataset.weaveActive = "0";
    };

    if (visible && !document.hidden) start();

    return {
      mode: "gpu",
      dispose() {
        if (disposed) return;
        disposed = true;
        stop();
        intersectionObserver?.disconnect();
        document.removeEventListener("visibilitychange", onVisibility);
        window.removeEventListener("resize", onResize);
        window.removeEventListener("orientationchange", onResize);
        try {
          device.destroy();
        } catch {
          /* 忽略销毁异常 */
        }
      },
    };
  } catch {
    // 任何 GPU 初始化失败都保持页面完全可用
    canvas.dataset.weaveActive = "0";
    return cssFallback;
  }
}
