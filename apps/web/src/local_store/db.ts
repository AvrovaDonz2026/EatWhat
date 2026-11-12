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
export const DEFAULT_TARGETS: DietTargets = {
  energyKj: 8400,
  proteinG: 60,
  fatG: 65,
  carbohydrateG: 300,
  sodiumMg: 2000,
};

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

/** 导出本地数据（用户主动触发的本地文件下载，不经网络）。 */
export async function exportLocalData(): Promise<string> {
  const rules = await listRules();
  const plan = await loadPlan();
  const history = await listScanHistory(200);
  return JSON.stringify({ exportedAt: new Date().toISOString(), diet_rules: rules, diet_plan: plan, scan_history: history }, null, 2);
}

/** 本地数据总览（仅用于界面展示，不发送任何地方）。 */
export async function localDataSummary(): Promise<{ rules: number; logs: number; history: number }> {
  const rules = await listRules();
  const plan = await loadPlan();
  const history = await listScanHistory(500);
  return { rules: rules.length, logs: plan.logs.length, history: history.length };
}
