/**
 * 营养表按克重折算 —— 纯算术。
 *
 * 只做：读取营养成分表的「每 100 克」原文数值 → 乘以 克重 / 100 → 累加。
 * 不做任何摄入建议、阈值判定、好坏评价，也不使用任何语义配色。
 *
 * 三条自我限制：
 * 1. 计量基准不是「每 100 克」（例如每 100 毫升）时拒绝折算，不假设 1 毫升 = 1 克；
 * 2. 单位与目标单位不一致且无确定换算关系时拒绝该营养素；
 * 3. 营养表缺少某一项时，把缺失项如实列出，不补任何推断值。
 */
import type { NutritionEntry } from "@/types";

export type NutrientKey = "energyKj" | "proteinG" | "fatG" | "carbohydrateG" | "sodiumMg";

export const NUTRIENT_ORDER: NutrientKey[] = [
  "energyKj",
  "proteinG",
  "fatG",
  "carbohydrateG",
  "sodiumMg",
];

export const NUTRIENT_LABEL: Record<NutrientKey, { label: string; unit: string }> = {
  energyKj: { label: "能量", unit: "kJ" },
  proteinG: { label: "蛋白质", unit: "g" },
  fatG: { label: "脂肪", unit: "g" },
  carbohydrateG: { label: "碳水化合物", unit: "g" },
  sodiumMg: { label: "钠", unit: "mg" },
};

const KEY_BY_NUTRIENT: Record<string, NutrientKey> = {
  能量: "energyKj",
  蛋白质: "proteinG",
  脂肪: "fatG",
  碳水化合物: "carbohydrateG",
  钠: "sodiumMg",
};

export type BasisUnit = "克" | "毫升" | "未知";
export type NutritionBasis = "PER_100G" | "PER_100ML" | "NONE";

export type ParsedNutrientValue = {
  amount: number;
  unit: string;
  basisAmount: number;
  basisUnit: BasisUnit;
};

const VALUE_PATTERN = /([0-9]+(?:\.[0-9]+)?)\s*(千焦|千卡|毫克|克|g|mg|kj|kcal)\s*\/\s*([0-9]+(?:\.[0-9]+)?)\s*(克|毫升|g|ml)/i;

/** 解析「2130千焦/100克」这类原文；无法机械解析时返回 null，不做猜测。 */
export function parseNutrientValue(raw: string): ParsedNutrientValue | null {
  const match = VALUE_PATTERN.exec(raw);
  if (!match) return null;
  const amount = Number(match[1]);
  const basisAmount = Number(match[3]);
  if (!Number.isFinite(amount) || !Number.isFinite(basisAmount) || basisAmount <= 0) return null;
  const unit = match[2];
  const basisRaw = match[4].toLowerCase();
  const basisUnit: BasisUnit =
    basisRaw === "毫升" || basisRaw === "ml" ? "毫升" : basisRaw === "克" || basisRaw === "g" ? "克" : "未知";
  return { amount, unit, basisAmount, basisUnit };
}

/** 把原文单位换算到目标单位；没有确定换算关系时返回 null。 */
function unitFactor(key: NutrientKey, unit: string): number | null {
  const normalized = unit.toLowerCase();
  if (key === "energyKj") {
    if (normalized === "千焦" || normalized === "kj") return 1;
    if (normalized === "千卡" || normalized === "kcal") return 4.184;
    return null;
  }
  if (key === "sodiumMg") {
    if (normalized === "毫克" || normalized === "mg") return 1;
    if (normalized === "克" || normalized === "g") return 1000;
    return null;
  }
  if (normalized === "克" || normalized === "g") return 1;
  if (normalized === "毫克" || normalized === "mg") return 0.001;
  return null;
}

export type NutritionEvidenceRef = {
  source_id: string;
  evidence_id: string;
  retrieved_at: string;
};

export type ScaledNutrition = {
  totals: Record<NutrientKey, number>;
  converted: boolean;
  basis: NutritionBasis;
  note: string;
  /** 该档案营养表中缺失的营养素（未计入，不补推断值）。 */
  missing: NutrientKey[];
  /** 同一营养素在该档案出现多条原文（不合并，按并列处理，本次未折算该项）。 */
  conflicting: NutrientKey[];
  /** 参与本次折算的营养原文（供追溯展示）。 */
  used: { nutrient: string; raw_value: string }[];
  /** 参与本次折算的证据绑定。 */
  evidence: NutritionEvidenceRef[];
};

const emptyTotals = (): Record<NutrientKey, number> => ({
  energyKj: 0,
  proteinG: 0,
  fatG: 0,
  carbohydrateG: 0,
  sodiumMg: 0,
});

const missingOf = (facts: NutritionEntry[]): NutrientKey[] => {
  const present = new Set<NutrientKey>();
  for (const entry of facts) {
    const key = KEY_BY_NUTRIENT[entry.nutrient];
    if (key && parseNutrientValue(entry.raw_value)) present.add(key);
  }
  return NUTRIENT_ORDER.filter((key) => !present.has(key));
};

/**
 * 按克重折算一份营养表。
 *
 * @param facts 该档案的 nutrition_facts 原文条目
 * @param grams 用户输入的克重（> 0）
 * @param entity 用于追溯的 entity / gtin 标识
 */
export function scaledNutrition(
  facts: NutritionEntry[] | null | undefined,
  grams: number,
  entity: { gtin: string; entity_id: string },
): ScaledNutrition {
  const base: ScaledNutrition = {
    totals: emptyTotals(),
    converted: false,
    basis: "NONE",
    note: "",
    used: [],
    evidence: [],
    missing: [],
    conflicting: [],
  };

  if (!facts || facts.length === 0) {
    return {
      ...base,
      note: "该档案未包含营养成分表记录，本次记录未计入任何数值。",
      missing: [...NUTRIENT_ORDER],
      conflicting: [],
    };
  }

  const parsed: { entry: NutritionEntry; value: ParsedNutrientValue }[] = [];
  for (const entry of facts) {
    const value = parseNutrientValue(entry.raw_value);
    if (value) parsed.push({ entry, value });
  }

  const basisUnits = new Set(parsed.map((item) => item.value.basisUnit));
  if (basisUnits.size > 1) {
    return {
      ...base,
      missing: missingOf(facts),
      note: "该档案营养表的计量基准不统一（同时存在每 100 克与每 100 毫升），本次记录未折算。",
    };
  }

  const basisUnit: BasisUnit = parsed[0]?.value.basisUnit ?? "未知";
  if (basisUnit === "毫升") {
    return {
      ...base,
      basis: "PER_100ML",
      missing: missingOf(facts),
      note: "该档案营养表按「每 100 毫升」标注，本页不做毫升与克之间的换算，本次记录未计入数值。",
    };
  }
  if (basisUnit !== "克") {
    return {
      ...base,
      missing: missingOf(facts),
      note: "该档案营养表的计量基准无法机械解析，本次记录未折算。",
    };
  }

  const totals = emptyTotals();
  const used: { nutrient: string; raw_value: string }[] = [];
  const evidence = new Map<string, NutritionEvidenceRef>();
  const conflicting: NutrientKey[] = [];
  let convertedAny = false;

  /** 同一营养素若出现多条不一致原文，按并列处理，不选一条作准、不取平均。 */
  const amountsByKey = new Map<NutrientKey, Set<string>>();
  for (const { entry, value } of parsed) {
    const key = KEY_BY_NUTRIENT[entry.nutrient];
    if (!key) continue;
    const set = amountsByKey.get(key) ?? new Set<string>();
    set.add(`${value.amount}|${value.unit}|${value.basisAmount}`);
    amountsByKey.set(key, set);
  }
  for (const [key, set] of amountsByKey) {
    if (set.size > 1) conflicting.push(key);
  }

  for (const { entry, value } of parsed) {
    const key = KEY_BY_NUTRIENT[entry.nutrient];
    if (!key) continue;
    if (conflicting.includes(key)) continue;
    const factor = unitFactor(key, value.unit);
    if (factor === null) continue;
    const ratio = grams / value.basisAmount;
    totals[key] = Number((value.amount * ratio * factor).toFixed(2));
    used.push({ nutrient: entry.nutrient, raw_value: entry.raw_value });
    convertedAny = true;
    const refKey = `${entry.source_id}|${entry.evidence_id}`;
    if (!evidence.has(refKey)) {
      evidence.set(refKey, {
        source_id: entry.source_id,
        evidence_id: entry.evidence_id,
        retrieved_at: entry.retrieved_at,
      });
    }
  }

  const missing = NUTRIENT_ORDER.filter(
    (key) =>
      !conflicting.includes(key) && !used.some((item) => KEY_BY_NUTRIENT[item.nutrient] === key),
  );

  return {
    totals,
    converted: convertedAny,
    basis: "PER_100G",
    note: convertedAny
      ? `按 ${entity.entity_id} 营养表「每 100 克」原文数值 × ${formatGrams(grams)} / 100 折算。`
      : "该档案营养表的单位无法机械换算到目标单位，本次记录未计入数值。",
    missing,
    conflicting,
    used,
    evidence: [...evidence.values()],
  };
}

export function formatGrams(grams: number): string {
  const rounded = Math.round(grams * 100) / 100;
  return String(rounded);
}

/** 展示用数字格式：kJ / mg 取整数，g 取一位小数（只做显示舍入）。 */
export function formatNutrient(key: NutrientKey, value: number): string {
  return key === "energyKj" || key === "sodiumMg" ? value.toFixed(0) : value.toFixed(1);
}

/**
 * 进度比例的显示口径 —— 纯算术，只做一步取整。
 *
 * `ratio` 由调用方按 `已计入 / 自设目标 × 100` 算出（未做任何截断）。
 *
 * - 四舍五入到 **0.1**：`Math.round(ratio * 10) / 10`；
 * - 整数百分数省略 `.0`（`0` 显示为 `0`，不是 `0.0`）；
 * - ≥ 1e21 时 `toFixed(1)` 会退回指数记法，此时改写成**不分组整数**（`106500000000000000000000`），
 *   不出现 `1.065e+23` 这类记法，也不会得到超长小数；
 * - 返回 `null` 表示「给不出有限且可读的数字」：`NaN` / `±Infinity`。此时界面**不显示百分比**，
 *   绝不显示 `Infinity` / `NaN` / 伪造的 `0`。
 *
 * 这个函数不判断「是否越线」，也不返回任何等级，越线与否只体现在数值本身。
 */
export function formatRatioPercent(ratio: number): string | null {
  if (!Number.isFinite(ratio)) return null;
  const rounded = Math.round(ratio * 10) / 10;
  if (!Number.isFinite(rounded)) return null;
  const fixed = rounded.toFixed(1);
  if (!/e/i.test(fixed)) return fixed.endsWith(".0") ? fixed.slice(0, -2) : fixed;
  // 极大比例（≥ 1e21 时 `toFixed` 会退回指数记法）：改写成不分组整数，仍不出现 `e+21`。
  const integer = Math.round(rounded).toLocaleString("en-US", {
    useGrouping: false,
    maximumFractionDigits: 0,
  });
  return /e/i.test(integer) ? null : integer;
}

export function missingLabels(missing: NutrientKey[]): string[] {
  return missing.map((key) => NUTRIENT_LABEL[key].label);
}
