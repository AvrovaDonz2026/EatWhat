/**
 * 端侧 OCR 提取配料表文字（tesseract.js）。
 *
 * 红线：
 * - worker 脚本、core（内嵌 wasm 的 .wasm.js）、语言数据全部随单文件打包内联，
 *   运行时不会发出任何网络请求；
 * - 图片只作为 Blob 在内存中交给 worker，不上传、不落盘；
 * - 语言数据按字节直接传入 worker（gzip 由 worker 内部解压）。
 *
 * 关键实现约束（曾导致 OCR 完全不可用，勿回退）：
 * tesseract.js 的 worker 内部只用 `importScripts()` 加载 core，而浏览器禁止
 * `importScripts` 读取 `blob:` URL（会抛 NetworkError）。因此不能把 core 做成
 * blob URL 传进去；必须把 core 源码拼进 worker 引导脚本，让 `global.TesseractCore`
 * 在 worker 启动时就已存在，worker 的 getCore() 便会跳过 importScripts。
 */
import { createWorker, type Worker } from "tesseract.js";
import coreSource from "tesseract.js-core/tesseract-core-simd-lstm.wasm.js?raw";
import workerSource from "tesseract.js/dist/worker.min.js?raw";
import chiSimUrl from "@tesseract.js-data/chi_sim/4.0.0_best_int/chi_sim.traineddata.gz?url";

export interface OcrProgress {
  status: string;
  progress: number;
}

export interface OcrResult {
  text: string;
  confidence: number;
  /** 从 OCR 文本中机械抽取的配料清单行（不做任何语义推断） */
  ingredientLines: string[];
}

let workerPromise: Promise<Worker> | null = null;
const blobCache: { bootstrap?: string; language?: Uint8Array } = {};

/**
 * 构造 worker 引导脚本：先定义 TesseractCore（内联 core），
 * 再加载 tesseract worker 主体。全部内容都在同一个 blob 里，
 * 因此 worker 内部不再需要 importScripts 任何外部资源。
 *
 * 另外补一个上游缺陷的补丁（tesseract.js 5.1.1）：
 * `initialize` 分支会把 `lang.data`（即 traineddata 原始字节）当成语言名拼接，
 * 得到 `Failed loading language '31,139,8,8,...'`，导致原本已通过 loadLanguage
 * 注入的语言数据在初始化阶段失效、OCR 整体不可用。
 * 这里在消息进入 dispatch 前，仅对 `initialize` 任务把 `{code,data}` 还原成
 * `code` 字符串；`loadLanguage` 仍保留字节对象，不回退到 CDN 下载。
 */
function buildBootstrap(core: string, worker: string): string {
  return [
    "/* 端侧 OCR 引导：内联 tesseract core 与 worker 主体，禁止任何外部加载 */",
    "(function () {",
    "  var global = self;",
    core,
    "  if (typeof global.TesseractCore === 'undefined' && typeof TesseractCore !== 'undefined') {",
    "    global.TesseractCore = TesseractCore;",
    "  }",
    "})();",
    "/* 修正 initialize 阶段误把语言字节当语言名的问题 */",
    "(function () {",
    "  self.addEventListener('message', function (event) {",
    "    var data = event.data;",
    "    if (!data || data.action !== 'initialize' || !data.payload || !data.payload.langs) return;",
    "    var langs = data.payload.langs;",
    "    if (typeof langs === 'string') return;",
    "    var codes = langs.map(function (lang) {",
    "      return typeof lang === 'string' ? lang : (lang && lang.code) || '';",
    "    }).filter(Boolean);",
    "    if (!codes.length) return;",
    "    data.payload.langs = codes.join('+');",
    "  }, true);",
    "})();",
    worker,
  ].join("\n");
}

function bootstrapUrl(): string {
  if (!blobCache.bootstrap) {
    blobCache.bootstrap = URL.createObjectURL(
      new Blob([buildBootstrap(coreSource, workerSource)], { type: "application/javascript" }),
    );
  }
  return blobCache.bootstrap;
}

async function languageBytes(): Promise<Uint8Array> {
  if (!blobCache.language) {
    // chiSimUrl 为内联 data: URL 或同一站点相对路径，读取不会产生跨站请求
    const response = await fetch(chiSimUrl, { credentials: "omit" });
    blobCache.language = new Uint8Array(await response.arrayBuffer());
  }
  return blobCache.language;
}

async function ensureWorker(onProgress?: (state: OcrProgress) => void): Promise<Worker> {
  if (!workerPromise) {
    workerPromise = (async () => {
      const data = await languageBytes();

      const worker = await createWorker(
        // 语言以「字节 + code」形式传入，避免 worker 内部再发起 fetch
        [{ code: "chi_sim", data } as never],
        1,
        {
          workerPath: bootstrapUrl(),
          // core 已在引导脚本里定义，这里不再提供路径（即便提供也不会被读取）
          workerBlobURL: false,
          legacyCore: false,
          legacyLang: false,
          gzip: true,
          logger: (message) => {
            if (typeof message === "object" && message !== null) {
              const status = String((message as { status?: string }).status ?? "");
              const progress = Number((message as { progress?: number }).progress ?? 0);
              onProgress?.({ status, progress });
            }
          },
          errorHandler: (error) => {
            console.warn("端侧 OCR worker 出错", error);
          },
        },
      );
      return worker;
    })().catch((error) => {
      // 失败后允许重试，否则一次失败会让整个会话永久不可用
      workerPromise = null;
      throw error;
    });
  }
  return workerPromise;
}

/** 预处理：灰度 + 放大 + 对比拉伸，纯本地 Canvas 操作。 */
async function preprocess(file: Blob): Promise<Blob> {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(2, Math.max(1, 1600 / Math.max(bitmap.width, bitmap.height)));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  const context = canvas.getContext("2d");
  if (!context) return file;
  context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);

  const image = context.getImageData(0, 0, canvas.width, canvas.height);
  const data = image.data;
  for (let i = 0; i < data.length; i += 4) {
    const gray = data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114;
    const stretched = Math.max(0, Math.min(255, (gray - 96) * 1.65));
    data[i] = stretched;
    data[i + 1] = stretched;
    data[i + 2] = stretched;
  }
  context.putImageData(image, 0, 0);
  const out = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
  return out ?? file;
}

/** OCR 识别：返回原文与置信度。图片不出浏览器。 */
export async function recognizeLabel(
  file: Blob,
  onProgress?: (state: OcrProgress) => void,
): Promise<OcrResult> {
  const worker = await ensureWorker(onProgress);
  const prepared = await preprocess(file);

  await worker.setParameters({
    tessedit_pageseg_mode: "6" as never,
    preserve_interword_spaces: "1",
  });

  const result = await worker.recognize(prepared);
  const text = result.data.text ?? "";
  const confidence = Number(result.data.confidence ?? 0);
  return { text, confidence, ingredientLines: extractIngredientLines(text) };
}

/**
 * 从 OCR 原文机械抽取「配料」段落行。
 * 只按分隔符切分，不做任何语义解释或补齐。
 */
export function extractIngredientLines(text: string): string[] {
  const lines = text
    .split(/[\n\r]+/)
    .map((line) => line.replace(/\s+/g, "").trim())
    .filter(Boolean);

  const startIndex = lines.findIndex((line) => /配料|配料表|原料/.test(line));
  if (startIndex >= 0) {
    const anchor = lines[startIndex].replace(/^.*?配料(表)?[:：]?/, "");
    const collected = anchor ? [anchor] : [];
    for (const line of lines.slice(startIndex + 1)) {
      if (/营养|成分表|致敏|过敏原|净含量|保质期|贮存|生产|执行标准|SC/.test(line)) break;
      collected.push(line);
    }
    return collected
      .flatMap((line) => line.split(/[、,，;；。]+/))
      .map((item) => item.trim())
      .filter((item) => item.length > 0);
  }

  // 无「配料」锚点时，抽取包含常见食材分隔符的最长行（纯机械判断）
  const candidate = lines
    .filter((line) => /[、,，;；]/.test(line) && line.length > 4)
    .sort((a, b) => b.length - a.length)[0];
  return candidate
    ? candidate
        .split(/[、,，;；。]+/)
        .map((item) => item.trim())
        .filter(Boolean)
    : [];
}

export async function disposeOcr(): Promise<void> {
  if (workerPromise) {
    try {
      const worker = await workerPromise;
      await worker.terminate();
    } catch {
      // 初始化失败时无 worker 可终止
    }
    workerPromise = null;
  }
  if (blobCache.bootstrap) URL.revokeObjectURL(blobCache.bootstrap);
  blobCache.bootstrap = undefined;
}
