/**
 * 端侧条码识别（zxing-wasm）。
 *
 * 红线：图片只在浏览器内存中处理，不上传、不落盘、不写日志。
 * WASM 二进制随单文件打包内联，运行时不发任何网络请求。
 */
import { prepareZXingModule, readBarcodes } from "zxing-wasm/reader";
import zxingWasmUrl from "zxing-wasm/reader/zxing_reader.wasm?url";

let zxingReady: Promise<void> | null = null;

async function ensureZXing(): Promise<void> {
  if (!zxingReady) {
    zxingReady = (async () => {
      const response = await fetch(zxingWasmUrl, { credentials: "omit", cache: "force-cache" });
      const buffer = await response.arrayBuffer();
      const bytes = new Uint8Array(buffer);
      prepareZXingModule({
        overrides: {
          wasmBinary: bytes,
          locateFile: (path: string) => (path.endsWith(".wasm") ? zxingWasmUrl : path),
        },
        fireImmediately: true,
      });
    })();
  }
  return zxingReady;
}

export interface BarcodeResult {
  text: string;
  format: string;
}

/** 从图片文件识别条码。找不到时返回 null（不抛错）。 */
export async function detectBarcode(file: Blob): Promise<BarcodeResult | null> {
  await ensureZXing();
  const results = await readBarcodes(file, {
    formats: ["EAN-13", "EAN-8", "UPC-A", "UPC-E", "Code128", "Code39", "ITF", "QRCode", "DataMatrix"],
    tryHarder: true,
    maxNumberOfSymbols: 4,
  });
  const hit = results.find((result) => result.isValid && result.text);
  if (!hit) return null;
  return { text: hit.text, format: hit.format ?? "UNKNOWN" };
}

/** 从摄像头帧识别条码（video 帧转 canvas → bitmap）。 */
export async function detectBarcodeFromVideo(video: HTMLVideoElement): Promise<BarcodeResult | null> {
  await ensureZXing();
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const context = canvas.getContext("2d");
  if (!context) return null;
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
  if (!blob) return null;
  return detectBarcode(blob);
}
