/**
 * 静态档案客户端：只读 /v1/ JSON 分片。
 *
 * 红线：
 * - 只做 GET 读取，绝不发送用户本地数据；
 * - 无静态库时退回内置演示样例（DEMO_FALLBACK），并明确标注来源为本地样例；
 * - 未命中一律返回 QUERIED_NO_MATCH，且文案不得含任何定性措辞。
 */
import type {
  CoverageEntry,
  DemoEntry,
  DemoIndex,
  GtinRecord,
  IngredientsDocument,
  Manifest,
  ProductDossier,
} from "@/types";
import { DEMO_FALLBACK } from "./demo_fallback";

const DATA_BASE = (import.meta.env.VITE_DATA_BASE as string | undefined) ?? "./data";

export type DataOrigin = "STATIC_LIBRARY" | "DEMO_FALLBACK";

export interface GtinLookup {
  origin: DataOrigin;
  record: GtinRecord;
  note?: string;
}

export interface DossierBundle {
  origin: DataOrigin;
  record: GtinRecord;
  product: ProductDossier | null;
  ingredients: IngredientsDocument | null;
}

let manifestCache: Manifest | null = null;
let demoCache: DemoEntry[] | null = null;

async function fetchJson<T>(path: string, signal?: AbortSignal): Promise<T | null> {
  try {
    const response = await fetch(new URL(path, new URL(DATA_BASE + "/", window.location.href)).toString(), {
      method: "GET",
      credentials: "omit",
      cache: "force-cache",
      signal,
      referrerPolicy: "no-referrer",
    });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

export async function loadManifest(signal?: AbortSignal): Promise<Manifest | null> {
  if (manifestCache) return manifestCache;
  const manifest = await fetchJson<Manifest>("v1/manifest.json", signal);
  if (manifest) manifestCache = manifest;
  return manifest;
}

async function loadDemoIndex(signal?: AbortSignal): Promise<DemoEntry[]> {
  if (demoCache) return demoCache;
  const index = await fetchJson<DemoIndex>("v1/demo/index.json", signal);
  const entries = index?.entries?.length ? index.entries : DEMO_FALLBACK.entries;
  demoCache = entries;
  return entries;
}

function demoToLookup(entry: DemoEntry): GtinLookup {
  return {
    origin: "DEMO_FALLBACK",
    note: "未检测到静态档案库，当前展示内置离线样例数据结构",
    record: {
      schema_version: "1.0",
      build_id: DEMO_FALLBACK.build_id,
      built_at: DEMO_FALLBACK.built_at,
      gtin: entry.gtin,
      entity_id: entry.entity_id,
      query_status: entry.query_status,
      query_status_note: "当前已接入静态档案库已匹配到记录（本地样例）",
      match_candidates: entry.match_candidates,
      coverage_manifest: entry.coverage_manifest,
      exact_match_count: entry.match_candidates.filter((c) => c.link_mode === "EXACT_IDENTIFIER_MATCH").length,
      manual_confirm_count: entry.match_candidates.filter((c) => c.link_mode === "MANUAL_CONFIRM").length,
      no_auto_merge_notice:
        "候选仅并列展示；仅 EXACT_IDENTIFIER_MATCH 建立关联，其余需 MANUAL_CONFIRM",
    },
  };
}

/** 按条码查询。未命中时返回 null，由调用方展示 QUERIED_NO_MATCH。 */
export async function lookupGtin(gtin: string, signal?: AbortSignal): Promise<GtinLookup | null> {
  const normalized = gtin.replace(/[^0-9]/g, "");
  const fromLibrary = await fetchJson<GtinRecord>(`v1/gtin/${normalized}.json`, signal);
  if (fromLibrary) return { origin: "STATIC_LIBRARY", record: fromLibrary };

  const demo = await loadDemoIndex(signal);
  const hit = demo.find((entry) => entry.gtin === normalized);
  return hit ? demoToLookup(hit) : null;
}

/** 读取完整档案（产品 + 配料）。 */
export async function loadDossier(gtin: string, signal?: AbortSignal): Promise<DossierBundle | null> {
  const lookup = await lookupGtin(gtin, signal);
  if (!lookup) return null;

  if (lookup.origin === "STATIC_LIBRARY") {
    const entityId = lookup.record.entity_id;
    const [product, ingredients] = await Promise.all([
      fetchJson<ProductDossier>(`v1/products/${entityId}.json`, signal),
      fetchJson<IngredientsDocument>(`v1/ingredients/${entityId}.json`, signal),
    ]);
    return { origin: lookup.origin, record: lookup.record, product, ingredients };
  }

  const demo = await loadDemoIndex(signal);
  const entry = demo.find((item) => item.gtin === gtin.replace(/[^0-9]/g, ""));
  if (!entry) return null;
  return {
    origin: "DEMO_FALLBACK",
    record: lookup.record,
    product: {
      schema_version: "1.0",
      build_id: DEMO_FALLBACK.build_id,
      built_at: DEMO_FALLBACK.built_at,
      entity_id: entry.entity_id,
      gtin: entry.gtin,
      observation_policy: {
        no_final_truth_field: true,
        no_auto_merge: true,
        observations_bound_to: ["source_id", "evidence_id", "retrieved_at", "raw_value"],
      },
      coverage_manifest: entry.coverage_manifest,
      blocks: entry.blocks,
      block_order: entry.blocks.map((b) => b.block_id),
      observations: [],
      history_timeline: [],
      source_visibility_events: [],
    },
    ingredients: entry.ingredients,
  };
}

/** 内置样例条码（供演示按钮使用，无静态库时也有数据）。 */
export interface DemoGtinEntry {
  gtin: string;
  label: string;
  /** 该条目所属数据来源：始终为端侧兜底样例，供界面按字段渲染来源标记。 */
  origin: DataOrigin;
}

export function demoGtins(): DemoGtinEntry[] {
  return DEMO_FALLBACK.entries.map((entry) => ({
    gtin: entry.gtin,
    label: `${entry.match_candidates[0]?.display_name ?? "样例商品"} (${entry.gtin})`,
    origin: "DEMO_FALLBACK",
  }));
}

/**
 * 只读的 evidence_id → payload_sha256 索引。
 *
 * 展示字段（配料/营养/字段/条目）本身不带哈希；哈希记录在 observations 与
 * history_timeline 里。这里把三处来源合并成一张查找表，供溯源披露按需展开时使用。
 * 只读，不回写任何数据。
 */
export function buildHashIndex(bundle: DossierBundle | null | undefined): (evidenceId?: string | null) => string | undefined {
  const map = new Map<string, string>();
  if (!bundle) return () => undefined;
  const add = (evidenceId?: string, hash?: string) => {
    if (!evidenceId || !hash) return;
    if (!map.has(evidenceId)) map.set(evidenceId, hash);
  };
  for (const observation of bundle.product?.observations ?? []) add(observation.evidence_id, observation.payload_sha256);
  for (const entry of bundle.product?.history_timeline ?? []) add(entry.evidence_id, entry.payload_sha256);
  for (const block of bundle.product?.blocks ?? []) {
    for (const item of block.items) add(String(item.evidence_id ?? ""), item.payload_sha256 ? String(item.payload_sha256) : undefined);
  }
  return (evidenceId) => (evidenceId ? map.get(evidenceId) : undefined);
}

/** 把溯源记录按 evidence_id 补上 payload_sha256（只在展开时用于展示）。 */
export function withHashes<T extends { evidence_id?: string | null; payload_sha256?: string | null }>(
  records: T[],
  hashFor: (evidenceId?: string | null) => string | undefined,
): T[] {
  return records.map((record) =>
    record.payload_sha256 ? record : { ...record, payload_sha256: hashFor(record.evidence_id) ?? null },
  );
}

export function coverageLabel(state: CoverageEntry["coverage_state"]): string {  switch (state) {
    case "QUERIED_MATCHED":
      return "本次查询取得记录";
    case "QUERIED_NO_MATCH":
      return "本次查询未取得记录";
    case "QUERY_FAILED":
      return "本次查询失败";
    case "NOT_QUERIED":
      return "本次未查询该源";
    case "ACCESS_PENDING":
      return "接入待授权";
    case "SOURCE_UNAVAILABLE":
      return "来源当前不可访问";
    default:
      return state;
  }
}
