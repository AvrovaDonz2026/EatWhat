/**
 * 端侧 OCR 提取配料表文字（tesseract.js）。
 *
 * 红线：
 * - worker 脚本、core（内嵌 wasm 的 .wasm.js）、语言数据全部内联进单文件，
 *   因此运行时不会发出任何网络请求；
 * - 图片只作为 Blob 在内存中交给 worker，不上传、不落盘；
 * - 语言数据按字节直接传入 worker（gzip 由 worker 内部解压）。
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
const blobCache: { core?: string; worker?: string; language?: Uint8Array } = {};

function blobUrl(source: string, mime: string): string {
  return URL.createObjectURL(new Blob([source], { type: mime }));
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
      blobCache.core ??= blobUrl(coreSource, "application/javascript");
      blobCache.worker ??= blobUrl(workerSource, "application/javascript");
      const data = await languageBytes();

      const worker = await createWorker(
        // 语言以「字节 + code」形式传入，避免 worker 内部再发起 fetch
        [{ code: "chi_sim", data } as never],
        1,
        {
          workerPath: blobCache.worker,
          corePath: blobCache.core,
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
    })();
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
    const worker = await workerPromise;
    await worker.terminate();
    workerPromise = null;
  }
  if (blobCache.core) URL.revokeObjectURL(blobCache.core);
  if (blobCache.worker) URL.revokeObjectURL(blobCache.worker);
  blobCache.core = undefined;
  blobCache.worker = undefined;
}
