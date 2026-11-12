/**
 * 本地备份的读取 / 校验 / 写入。
 *
 * 红线：
 * - 全程零网络：导出只生成本地文件（页面用 Blob + a[download] 触发），
 *   导入只用 FileReader 读用户选中的文件，绝不读取或上传到任何端点；
 * - 先完整校验、再写入：校验不通过时一行数据都不写，现有数据保持不变；
 * - 写入走单个 IndexedDB 事务，任一 store 失败则整体回滚。
 */
import {
  BACKUP_APP,
  BACKUP_MAX_BYTES,
  BACKUP_MAX_HISTORY,
  BACKUP_MAX_LOGS,
  BACKUP_MAX_RULES,
  BACKUP_SCHEMA_VERSION,
  localDb,
  type DietLogEntry,
  type DietPlan,
  type DietRule,
  type LocalBackupPayload,
  type ScanHistoryEntry,
} from "./db";

export type BackupPayload = LocalBackupPayload;

export type BackupValidation =
  | { ok: true; payload: BackupPayload }
  | { ok: false; issues: string[] };

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isFiniteNumber = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);

const isText = (value: unknown): value is string => typeof value === "string";

const clampText = (value: string, max: number) => value.slice(0, max);

const NUMERIC_KEYS = ["energyKj", "proteinG", "fatG", "carbohydrateG", "sodiumMg"] as const;

/* -------------------------------------------------------------- 结构校验 */

/** 校验单条忌口词条；问题写入局部数组，不污染其它条目的判定。 */
function validateRule(value: unknown, index: number): { rule: DietRule | null; issues: string[] } {
  const issues: string[] = [];
  if (!isObject(value)) {
    return { rule: null, issues: [`diet_rules[${index}] 不是对象`] };
  }
  const { id, term, aliases, createdAt } = value;
  if (!isText(id) || !id.trim()) issues.push(`diet_rules[${index}].id 必须是非空字符串`);
  if (!isText(term) || !term.trim()) issues.push(`diet_rules[${index}].term 必须是非空字符串`);
  if (isText(term) && term.trim().length > 200) issues.push(`diet_rules[${index}].term 超过 200 字符`);
  if (!Array.isArray(aliases)) {
    issues.push(`diet_rules[${index}].aliases 必须是字符串数组`);
  } else if (aliases.some((alias) => !isText(alias))) {
    issues.push(`diet_rules[${index}].aliases 必须全部是字符串`);
  } else if (aliases.length > 50) {
    issues.push(`diet_rules[${index}].aliases 超过 50 项`);
  }
  if (!isFiniteNumber(createdAt)) issues.push(`diet_rules[${index}].createdAt 必须是数字`);
  if (issues.length > 0) return { rule: null, issues };

  return {
    rule: {
      id: clampText(id as string, 120),
      term: clampText((term as string).trim(), 200),
      aliases: (aliases as string[]).map((alias) => clampText(alias.trim(), 200)).filter(Boolean),
      createdAt: createdAt as number,
    },
    issues,
  };
}

/** 校验单条饮食记录；数值必须是有限数字，来源说明必须非空。 */
function validateLogEntry(value: unknown, index: number): { log: DietLogEntry | null; issues: string[] } {
  const issues: string[] = [];
  if (!isObject(value)) {
    return { log: null, issues: [`diet_plan.logs[${index}] 不是对象`] };
  }
  for (const key of NUMERIC_KEYS) {
    if (!isFiniteNumber(value[key])) issues.push(`diet_plan.logs[${index}].${key} 必须是数字`);
  }
  if (!isText(value.id) || !value.id.trim()) issues.push(`diet_plan.logs[${index}].id 必须是非空字符串`);
  if (!isText(value.label) || !value.label.trim()) issues.push(`diet_plan.logs[${index}].label 必须是非空字符串`);
  if (!isText(value.origin) || !value.origin.trim()) issues.push(`diet_plan.logs[${index}].origin 必须是非空字符串`);
  if (!isFiniteNumber(value.createdAt)) issues.push(`diet_plan.logs[${index}].createdAt 必须是数字`);
  if (value.grams !== undefined && !isFiniteNumber(value.grams)) {
    issues.push(`diet_plan.logs[${index}].grams 必须是数字`);
  }
  if (issues.length > 0) return { log: null, issues };

  const evidence = Array.isArray(value.evidence)
    ? value.evidence.filter(isObject).map((item) => ({
        source_id: isText(item.source_id) ? clampText(item.source_id, 120) : undefined,
        source_name: isText(item.source_name) ? clampText(item.source_name, 200) : undefined,
        evidence_id: isText(item.evidence_id) ? clampText(item.evidence_id, 120) : undefined,
        retrieved_at: isText(item.retrieved_at) ? clampText(item.retrieved_at, 40) : undefined,
        payload_sha256: isText(item.payload_sha256) ? clampText(item.payload_sha256, 80) : undefined,
      }))
    : [];

  return {
    log: {
      id: clampText(value.id as string, 120),
      label: clampText(value.label as string, 200),
      energyKj: value.energyKj as number,
      proteinG: value.proteinG as number,
      fatG: value.fatG as number,
      carbohydrateG: value.carbohydrateG as number,
      sodiumMg: value.sodiumMg as number,
      createdAt: value.createdAt as number,
      origin: clampText(value.origin as string, 200),
      grams: value.grams as number | undefined,
      entity_id: isText(value.entity_id) ? clampText(value.entity_id, 120) : undefined,
      gtin: isText(value.gtin) ? clampText(value.gtin, 40) : undefined,
      nutrition_basis: isText(value.nutrition_basis) ? clampText(value.nutrition_basis, 40) : undefined,
      evidence,
    },
    issues,
  };
}

function validatePlan(value: unknown): { plan: DietPlan | null; issues: string[] } {
  const issues: string[] = [];
  if (!isObject(value)) return { plan: null, issues: ["diet_plan 不是对象"] };
  if (!isObject(value.targets)) return { plan: null, issues: ["diet_plan.targets 不是对象"] };

  const targets = value.targets as Record<string, unknown>;
  for (const key of NUMERIC_KEYS) {
    const target = targets[key];
    if (!isFiniteNumber(target) || target < 0) {
      issues.push(`diet_plan.targets.${key} 必须是非负数字`);
    }
  }
  if (!Array.isArray(value.logs)) {
    issues.push("diet_plan.logs 必须是数组");
    return { plan: null, issues };
  }
  if (value.logs.length > BACKUP_MAX_LOGS) {
    issues.push(`diet_plan.logs 超过 ${BACKUP_MAX_LOGS} 条`);
    return { plan: null, issues };
  }

  const logs: DietLogEntry[] = [];
  value.logs.forEach((item, index) => {
    const result = validateLogEntry(item, index);
    issues.push(...result.issues);
    if (result.log) logs.push(result.log);
  });
  if (issues.length > 0) return { plan: null, issues };

  return {
    plan: {
      id: "default",
      targets: {
        energyKj: targets.energyKj as number,
        proteinG: targets.proteinG as number,
        fatG: targets.fatG as number,
        carbohydrateG: targets.carbohydrateG as number,
        sodiumMg: targets.sodiumMg as number,
      },
      logs,
      updatedAt: isFiniteNumber(value.updatedAt) ? value.updatedAt : Date.now(),
    },
    issues,
  };
}

function validateHistory(value: unknown, index: number): { entry: ScanHistoryEntry | null; issues: string[] } {
  const issues: string[] = [];
  if (!isObject(value)) {
    return { entry: null, issues: [`scan_history[${index}] 不是对象`] };
  }
  if (!isText(value.id) || !value.id.trim()) issues.push(`scan_history[${index}].id 必须是非空字符串`);
  if (!isText(value.ocrText)) issues.push(`scan_history[${index}].ocrText 必须是字符串`);
  if (!isText(value.gtin)) issues.push(`scan_history[${index}].gtin 必须是字符串`);
  if (!isFiniteNumber(value.ts)) issues.push(`scan_history[${index}].ts 必须是数字`);
  if (!isText(value.origin) || !value.origin.trim()) issues.push(`scan_history[${index}].origin 必须是非空字符串`);
  if (issues.length > 0) return { entry: null, issues };

  return {
    entry: {
      id: clampText(value.id as string, 120),
      ocrText: clampText(value.ocrText as string, 400),
      gtin: clampText(value.gtin as string, 40),
      ts: value.ts as number,
      origin: clampText(value.origin as string, 120),
    },
    issues,
  };
}

/**
 * 校验备份文本。只解析与校验，不触碰 IndexedDB。
 * 结构问题最多收集 12 条，避免错误列表过长。
 */
export function validateBackup(text: string): BackupValidation {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { ok: false, issues: ["文件不是合法 JSON，无法解析"] };
  }
  if (!isObject(parsed)) return { ok: false, issues: ["备份文件顶层必须是 JSON 对象"] };

  const issues: string[] = [];
  if (parsed.app !== BACKUP_APP) issues.push(`app 字段必须是 "${BACKUP_APP}"`);
  if (parsed.schema_version !== BACKUP_SCHEMA_VERSION) {
    issues.push(
      `schema_version 必须是 "${BACKUP_SCHEMA_VERSION}"（实际：${JSON.stringify(parsed.schema_version)}）`,
    );
  }

  const rulesRaw = parsed.diet_rules;
  if (!Array.isArray(rulesRaw)) {
    issues.push("diet_rules 必须是数组");
  } else if (rulesRaw.length > BACKUP_MAX_RULES) {
    issues.push(`diet_rules 超过 ${BACKUP_MAX_RULES} 条`);
  }
  if (!Array.isArray(parsed.scan_history)) {
    issues.push("scan_history 必须是数组");
  } else if (parsed.scan_history.length > BACKUP_MAX_HISTORY) {
    issues.push(`scan_history 超过 ${BACKUP_MAX_HISTORY} 条`);
  }
  if (issues.length > 0) return { ok: false, issues: issues.slice(0, 12) };

  const rules: DietRule[] = [];
  (rulesRaw as unknown[]).forEach((item, index) => {
    const result = validateRule(item, index);
    issues.push(...result.issues);
    if (result.rule) rules.push(result.rule);
  });

  const planResult = validatePlan(parsed.diet_plan);
  issues.push(...planResult.issues);

  const history: ScanHistoryEntry[] = [];
  (parsed.scan_history as unknown[]).forEach((item, index) => {
    const result = validateHistory(item, index);
    issues.push(...result.issues);
    if (result.entry) history.push(result.entry);
  });

  if (issues.length > 0 || !planResult.plan) {
    return { ok: false, issues: issues.slice(0, 12) };
  }

  return {
    ok: true,
    payload: {
      schema_version: BACKUP_SCHEMA_VERSION,
      app: BACKUP_APP,
      exported_at: isText(parsed.exported_at) ? parsed.exported_at : "",
      diet_rules: rules,
      diet_plan: planResult.plan,
      scan_history: history,
    },
  };
}

/* ---------------------------------------------------------------- 写入 */

export type BackupImportMode = "replace" | "merge";

export const IMPORT_MODE_LABEL: Record<BackupImportMode, string> = {
  replace: "覆盖",
  merge: "合并",
};

export const IMPORT_MODE_HINT: Record<BackupImportMode, string> = {
  replace: "覆盖：先清空本地词条与扫码历史，再写入备份内容。",
  merge: "合并：按 id 并入，同 id 以备份内容为准；本地其它条目保留。",
};

export type BackupImportResult = {
  mode: BackupImportMode;
  rules: number;
  logs: number;
  history: number;
};

/**
 * 把已校验通过的备份写入 IndexedDB。
 *
 * - replace：先清空 diet_rules 与 scan_history，再写入备份内容；
 * - merge：按 id 写入（`put`），同 id 覆盖，其余保留；
 * - diet_plan 恒为单条记录，两种模式都以备份内容为准；
 * - 单个事务，任一 store 抛错即整体回滚（不会留下半份数据）。
 */
export async function applyBackup(
  payload: BackupPayload,
  mode: BackupImportMode,
): Promise<BackupImportResult> {
  const db = await localDb();
  const tx = db.transaction(["diet_rules", "diet_plan", "scan_history"], "readwrite");
  const rulesStore = tx.objectStore("diet_rules");
  const planStore = tx.objectStore("diet_plan");
  const historyStore = tx.objectStore("scan_history");

  if (mode === "replace") {
    await rulesStore.clear();
    await historyStore.clear();
  }

  for (const rule of payload.diet_rules) await rulesStore.put(rule);
  for (const entry of payload.scan_history) await historyStore.put(entry);
  await planStore.put({ ...payload.diet_plan, id: "default", updatedAt: Date.now() });

  await tx.done;

  return {
    mode,
    rules: payload.diet_rules.length,
    logs: payload.diet_plan.logs.length,
    history: payload.scan_history.length,
  };
}

/** 读取用户选中的备份文件（FileReader，本地读取，不发任何请求）。 */
export function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    if (file.size > BACKUP_MAX_BYTES) {
      reject(new Error(`文件超过 ${Math.round(BACKUP_MAX_BYTES / 1024 / 1024)} MB 上限`));
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("文件读取失败"));
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.readAsText(file);
  });
}
