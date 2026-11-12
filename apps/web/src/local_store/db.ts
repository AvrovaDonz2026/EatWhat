/**
 * 端侧本地存储（IndexedDB）。
 *
 * 红线：
 * - 忌口词条、饮食计划、扫码历史、OCR 结果与原图 100% 留在浏览器；
 * - 绝不使用 HTTP Cookie，绝不上传，绝不写日志；
 * - 原图只驻留内存，不落盘（避免存储个人影像）。
 */
import { openDB, type DBSchema, type IDBPDatabase } from "idb";

export interface DietRule {
  id: string;
  term: string;
  aliases: string[];
  createdAt: number;
}

export interface DietTargets {
  energyKj: number;
  proteinG: number;
  fatG: number;
  carbohydrateG: number;
  sodiumMg: number;
}

/** 折算所依据的营养原文证据绑定（只记录引用，不复制数值）。 */
export interface DietLogEvidenceRef {
  source_id?: string;
  /** 记录时快照的可读来源名（来源切换后仍可读）。 */
  source_name?: string;
  evidence_id?: string;
  retrieved_at?: string;
  payload_sha256?: string;
}

export interface DietLogEntry {
  id: string;
  label: string;
  energyKj: number;
  proteinG: number;
  fatG: number;
  carbohydrateG: number;
  sodiumMg: number;
  createdAt: number;
  /** 来源说明：手动录入 / 商品营养原文 */
  origin: string;
  /** 本次记录输入的克重（可选：旧记录没有该字段）。 */
  grams?: number;
  /** 折算依据的档案 entity_id（可选）。 */
  entity_id?: string;
  /** 折算依据的商品条码（可选）。 */
  gtin?: string;
  /** 折算基准：PER_100G / PER_100ML / NONE（可选）。 */
  nutrition_basis?: string;
  /** 折算依据的营养原文证据绑定（可选，可为空数组）。 */
  evidence?: DietLogEvidenceRef[];
}

export interface DietPlan {
  id: "default";
  targets: DietTargets;
  logs: DietLogEntry[];
  updatedAt: number;
}

export interface ScanHistoryEntry {
  id: string;
  ocrText: string;
  gtin: string;
  ts: number;
  /** 数据来源：STATIC_LIBRARY / DEMO_FALLBACK / MANUAL_PASTE */
  origin: string;
}

interface LocalSchema extends DBSchema {
  diet_rules: { key: string; value: DietRule; indexes: { "by-term": string } };
  diet_plan: { key: string; value: DietPlan };
  scan_history: { key: string; value: ScanHistoryEntry; indexes: { "by-ts": number } };
}

const DB_NAME = "product-public-dossier-local";
const DB_VERSION = 1;

let dbPromise: Promise<IDBPDatabase<LocalSchema>> | null = null;

export function localDb(): Promise<IDBPDatabase<LocalSchema>> {
  if (!dbPromise) {
    dbPromise = openDB<LocalSchema>(DB_NAME, DB_VERSION, {
      upgrade(db) {
        if (!db.objectStoreNames.contains("diet_rules")) {
          const store = db.createObjectStore("diet_rules", { keyPath: "id" });
          store.createIndex("by-term", "term");
        }
        if (!db.objectStoreNames.contains("diet_plan")) {
          db.createObjectStore("diet_plan", { keyPath: "id" });
        }
        if (!db.objectStoreNames.contains("scan_history")) {
          const store = db.createObjectStore("scan_history", { keyPath: "id" });
          store.createIndex("by-ts", "ts");
        }
      },
      blocked() {
        console.warn("IndexedDB 升级被其他标签页阻塞");
      },
      terminated() {
        dbPromise = null;
      },
    });
  }
  return dbPromise;
}

export function newId(prefix: string): string {
  const random = crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2, 12);
  return `${prefix}_${random}`;
}

// ------------------------------------------------------------------ diet rules
export async function listRules(): Promise<DietRule[]> {
  const db = await localDb();
  const rules = await db.getAll("diet_rules");
  return rules.sort((a, b) => a.createdAt - b.createdAt);
}

export async function addRule(term: string, aliases: string[] = []): Promise<DietRule> {
  const db = await localDb();
  const clean = term.trim();
  const rule: DietRule = {
    id: newId("rule"),
    term: clean,
    aliases: aliases.map((a) => a.trim()).filter(Boolean),
    createdAt: Date.now(),
  };
  await db.put("diet_rules", rule);
  return rule;
}

export async function deleteRule(id: string): Promise<void> {
  const db = await localDb();
  await db.delete("diet_rules", id);
}

export async function clearRules(): Promise<void> {
  const db = await localDb();
  await db.clear("diet_rules");
}

// ------------------------------------------------------------------- diet plan
/**
 * 默认参考值 —— 取自 GB 28050—2011《预包装食品营养标签通则》附录 A 表 A.1
 * 的「营养素参考值（NRV）」：能量 8400 kJ、蛋白质 60 g、脂肪 ≤60 g、
 * 碳水化合物 300 g、钠 2000 mg。
 *
 * 该标准第 2.6 条把 NRV 定义为「专用于食品营养标签，用于比较食品营养成分含量的
 * 参考值」，它**不是**个人每日摄入目标。界面只在数值仍等于这里时才标注 NRV 出处
 * （见 `NRV_IS_UPPER_BOUND` 与 `DietPage` 的逐项判定）。
 *
 * 脂肪在表中是「≤60 g」上限；标准问答第（三十六）条明确计算 NRV% 时按 60 计算，
 * 因此这里存 `60`（纯数字，schema 不变），上限语义只在展示层用 `≤` 表达。
 */
export const DEFAULT_TARGETS: DietTargets = {
  energyKj: 8400,
  proteinG: 60,
  fatG: 60,
  carbohydrateG: 300,
  sodiumMg: 2000,
};

/**
 * 哪些默认参考值在上限意义上成立（GB 28050 附录 A 只有脂肪写作「≤」）。
 *
 * 只读展示常量，不落盘、不进备份、不改 `DietTargets` 的 schema ——
 * 存储值仍是数字，比较与计算一律按该数字（脂肪按 60）。
 */
export const NRV_IS_UPPER_BOUND: Record<keyof DietTargets, boolean> = {
  energyKj: false,
  proteinG: false,
  fatG: true,
  carbohydrateG: false,
  sodiumMg: false,
};

/** 该数值是否仍等于 GB 28050 附录 A 的默认参考值（逐项比较，不存标记位）。 */
export function isNrvValue(key: keyof DietTargets, value: number): boolean {
  return value === DEFAULT_TARGETS[key];
}

export function emptyPlan(): DietPlan {
  return { id: "default", targets: { ...DEFAULT_TARGETS }, logs: [], updatedAt: Date.now() };
}

export async function loadPlan(): Promise<DietPlan> {
  const db = await localDb();
  const plan = await db.get("diet_plan", "default");
  return plan ?? emptyPlan();
}

export async function savePlan(plan: DietPlan): Promise<void> {
  const db = await localDb();
  await db.put("diet_plan", { ...plan, id: "default", updatedAt: Date.now() });
}

// ---------------------------------------------------------------- scan history
export async function listScanHistory(limit = 50): Promise<ScanHistoryEntry[]> {
  const db = await localDb();
  const all = await db.getAllFromIndex("scan_history", "by-ts");
  return all.slice(-limit).reverse();
}

export async function addScanHistory(entry: Omit<ScanHistoryEntry, "id" | "ts">): Promise<void> {
  const db = await localDb();
  await db.put("scan_history", { ...entry, id: newId("scan"), ts: Date.now() });
}

export async function clearScanHistory(): Promise<void> {
  const db = await localDb();
  await db.clear("scan_history");
}

/** 备份文件标识与版本（导入校验的第一道断言）。 */
export const BACKUP_APP = "eatwhat-local-backup";
export const BACKUP_SCHEMA_VERSION = "1";
/** 单文件上限：超过则拒绝读取，避免把超大文件读进内存。 */
export const BACKUP_MAX_BYTES = 8 * 1024 * 1024;
export const BACKUP_MAX_RULES = 2000;
export const BACKUP_MAX_LOGS = 5000;
export const BACKUP_MAX_HISTORY = 5000;

/** 本地备份载荷（与 exportLocalData() 写出的 JSON 同构）。 */
export interface LocalBackupPayload {
  schema_version: string;
  app: string;
  exported_at: string;
  diet_rules: DietRule[];
  diet_plan: DietPlan;
  scan_history: ScanHistoryEntry[];
}

/** 导出本地数据（用户主动触发的本地文件下载，不经网络）。 */
export async function exportLocalData(): Promise<string> {
  const rules = await listRules();
  const plan = await loadPlan();
  const history = await listScanHistory(BACKUP_MAX_HISTORY);
  const payload: LocalBackupPayload = {
    schema_version: BACKUP_SCHEMA_VERSION,
    app: BACKUP_APP,
    exported_at: new Date().toISOString(),
    diet_rules: rules,
    diet_plan: { ...plan, id: "default" },
    scan_history: history,
  };
  return JSON.stringify(payload, null, 2);
}

/** 本地数据总览（仅用于界面展示，不发送任何地方）。 */
export async function localDataSummary(): Promise<{ rules: number; logs: number; history: number }> {
  const rules = await listRules();
  const plan = await loadPlan();
  const history = await listScanHistory(500);
  return { rules: rules.length, logs: plan.logs.length, history: history.length };
}
