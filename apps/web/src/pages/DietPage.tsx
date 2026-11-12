import { useCallback, useEffect, useMemo, useState } from "react";
import { DocSection, EmptyState, EvidenceTag, MarginNote, StatusTag } from "@/components/ui";
import {
  addRule,
  clearRules,
  deleteRule,
  exportLocalData,
  listRules,
  loadPlan,
  savePlan,
  type DietLogEntry,
  type DietPlan,
  type DietRule,
  type DietTargets,
} from "@/local_store/db";
import { GB7718_ALLERGEN_PRESETS } from "@/local_store/allergen_presets";
import { matchIngredients } from "@/engine/local_diet_matcher";
import { loadDossier, type DossierBundle } from "@/services/static_dossier_client";

interface DietPageProps {
  gtin: string;
  showToast: (text: string) => void;
  onDataChanged: () => void;
}

const TARGET_LABELS: { key: keyof DietTargets; label: string; unit: string }[] = [
  { key: "energyKj", label: "能量", unit: "kJ" },
  { key: "proteinG", label: "蛋白质", unit: "g" },
  { key: "fatG", label: "脂肪", unit: "g" },
  { key: "carbohydrateG", label: "碳水化合物", unit: "g" },
  { key: "sodiumMg", label: "钠", unit: "mg" },
];

export function DietPage({ gtin, showToast, onDataChanged }: DietPageProps) {
  const [rules, setRules] = useState<DietRule[]>([]);
  const [term, setTerm] = useState("");
  const [aliases, setAliases] = useState("");
  const [plan, setPlan] = useState<DietPlan | null>(null);
  const [bundle, setBundle] = useState<DossierBundle | null>(null);
  const [logLabel, setLogLabel] = useState("");
  const [logServing, setLogServing] = useState("100");

  const refresh = useCallback(async () => {
    setRules(await listRules());
    setPlan(await loadPlan());
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    void (async () => {
      if (!gtin) {
        setBundle(null);
        return;
      }
      setBundle(await loadDossier(gtin));
    })();
  }, [gtin]);

  const hits = useMemo(() => {
    if (!bundle?.ingredients) return null;
    return matchIngredients(bundle.ingredients.ingredients, bundle.ingredients.explicit_allergens, rules);
  }, [bundle, rules]);

  const addTerm = async () => {
    const clean = term.trim();
    if (!clean) return;
    await addRule(
      clean,
      aliases
        .split(/[,，、;；]+/)
        .map((item) => item.trim())
        .filter(Boolean),
    );
    setTerm("");
    setAliases("");
    await refresh();
    onDataChanged();
    showToast(`已加入本地词条「${clean}」`);
  };

  const addPreset = async (presetTerm: string, presetAliases: string[]) => {
    if (rules.some((rule) => rule.term === presetTerm)) {
      showToast("该预设词条已存在");
      return;
    }
    await addRule(presetTerm, presetAliases);
    await refresh();
    onDataChanged();
    showToast(`已加入 GB 7718-2025 预设「${presetTerm}」`);
  };

  const updateTarget = (key: keyof DietTargets, value: number) => {
    if (!plan) return;
    const next: DietPlan = { ...plan, targets: { ...plan.targets, [key]: value } };
    setPlan(next);
    void savePlan(next).then(onDataChanged);
  };

  const totals = useMemo(() => {
    if (!plan) return { energyKj: 0, proteinG: 0, fatG: 0, carbohydrateG: 0, sodiumMg: 0 };
    return plan.logs.reduce(
      (acc, entry) => ({
        energyKj: acc.energyKj + entry.energyKj,
        proteinG: acc.proteinG + entry.proteinG,
        fatG: acc.fatG + entry.fatG,
        carbohydrateG: acc.carbohydrateG + entry.carbohydrateG,
        sodiumMg: acc.sodiumMg + entry.sodiumMg,
      }),
      { energyKj: 0, proteinG: 0, fatG: 0, carbohydrateG: 0, sodiumMg: 0 },
    );
  }, [plan]);

  const addLog = async () => {
    if (!plan) return;
    const label = logLabel.trim() || "手动录入条目";
    const numeric = parseNutritionDigits(bundle, Number(logServing) / 100);
    const entry: DietLogEntry = {
      id: `log_${Math.random().toString(36).slice(2, 10)}`,
      label,
      ...numeric,
      createdAt: Date.now(),
      origin: numeric.source,
    };
    const next: DietPlan = { ...plan, logs: [...plan.logs, entry] };
    setPlan(next);
    setLogLabel("");
    await savePlan(next);
    onDataChanged();
    showToast("已记录到本地饮食计划");
  };

  const removeLog = async (id: string) => {
    if (!plan) return;
    const next: DietPlan = { ...plan, logs: plan.logs.filter((entry) => entry.id !== id) };
    setPlan(next);
    await savePlan(next);
    onDataChanged();
  };

  const downloadLocal = async () => {
    const json = await exportLocalData();
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "my-local-diet-data.json";
    anchor.click();
    URL.revokeObjectURL(url);
    showToast("已在本地导出 JSON（不经网络）");
  };

  return (
    <div className="space-y-10">
      <DocSection
        index="01"
        title="本地忌口词条"
        subtitle="词条只保存在本浏览器 IndexedDB；对照为归一化后的纯子串 / 别名机械匹配，不做任何推断。"
        right={<StatusTag tone="filled">IndexedDB</StatusTag>}
        marginalia={
          <>
            <MarginNote label="词条条数" value={`${rules.length} 条`} />
            <MarginNote label="存储位置" value="IndexedDB · 库名 product-public-dossier-local · 表 diet_rules" />
            <MarginNote label="归一化" value="NFKC → 去空白 → 小写，再做子串比对" />
          </>
        }
      >
        <p className="t-12 label-track text-[color:var(--ink-faint)] pb-2">
          GB 7718-2025 致敏原预设 · 点击加入本地
        </p>
        <div className="flex flex-wrap gap-1.5">
          {GB7718_ALLERGEN_PRESETS.map((preset) => {
            const active = rules.some((rule) => rule.term === preset.term);
            return (
              <button
                key={preset.term}
                type="button"
                onClick={() => void addPreset(preset.term, preset.aliases)}
                title={`别名：${preset.aliases.join("、")}`}
                className={`t-12 px-2 py-1 border transition-colors ${
                  active
                    ? "border-[color:var(--teal)] text-[color:var(--teal-deep)] bg-[color:var(--teal-wash)]"
                    : "border-[color:var(--rule)] text-[color:var(--ink-soft)] hover:border-[color:var(--teal)]"
                }`}
              >
                {preset.term}
                <span className="ml-1.5 t-12 text-[color:var(--ink-faint)]">{preset.group}</span>
              </button>
            );
          })}
        </div>

        <div className="pt-6 grid grid-cols-1 sm:grid-cols-2 gap-4">
          <label>
            <span className="t-12 text-[color:var(--ink-muted)] block pb-1">忌口词条</span>
            <input
              value={term}
              onChange={(event) => setTerm(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void addTerm();
              }}
              placeholder="忌口词条，例如：乳糖 / 花生"
              className="field-input"
            />
          </label>
          <label>
            <span className="t-12 text-[color:var(--ink-muted)] block pb-1">别名（可留空）</span>
            <input
              value={aliases}
              onChange={(event) => setAliases(event.target.value)}
              placeholder="用顿号或逗号分隔，例如：乳糖、 lactose"
              className="field-input"
            />
          </label>
        </div>

        <div className="flex flex-wrap gap-2 pt-4">
          <button type="button" onClick={() => void addTerm()} className="btn btn--primary">
            添加本地词条
          </button>
          <button
            type="button"
            onClick={() => {
              void clearRules().then(async () => {
                await refresh();
                onDataChanged();
                showToast("已清空本地词条");
              });
            }}
            className="btn btn--quiet"
          >
            清空
          </button>
          <button type="button" onClick={() => void downloadLocal()} className="btn btn--quiet">
            导出本地数据
          </button>
        </div>
        <p className="t-12 text-[color:var(--ink-faint)] pt-2">
          导出文件仅写入本机，不经网络发送。
        </p>

        <div className="pt-6">
          <p className="t-12 label-track text-[color:var(--ink-faint)] pb-2">
            已保存的词条
          </p>
          {rules.length === 0 ? (
            <EmptyState text="尚无本地忌口词条。添加后，扫码页会显示客观的配料词条命中提示。" />
          ) : (
            <div className="max-h-80 overflow-y-auto">
              {rules.map((rule) => (
                <div
                  key={rule.id}
                  className="py-2 border-b border-dotted border-[color:var(--rule-hair)] flex items-start justify-between gap-3"
                >
                  <div className="min-w-0">
                    <p className="t-13 text-[color:var(--ink)]">{rule.term}</p>
                    {rule.aliases.length > 0 ? (
                      <p className="t-12 text-[color:var(--ink-muted)] pt-0.5">
                        别名：{rule.aliases.join("、")}
                      </p>
                    ) : null}
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      void deleteRule(rule.id).then(async () => {
                        await refresh();
                        onDataChanged();
                      });
                    }}
                    className="btn btn--text shrink-0"
                  >
                    删除
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {hits && (hits.ingredientHits.length > 0 || hits.allergenHits.length > 0) ? (
          <div className="pt-6">
            <p className="t-12 label-track text-[color:var(--ink-faint)] pb-2">
              当前档案 {gtin ? `(${gtin})` : ""} 的机械对照结果
            </p>
            {hits.ingredientHits.map((hit, index) => (
              <div
                key={`${hit.evidenceId}-${index}`}
                className="py-2 border-b border-dotted border-[color:var(--rule-hair)] flex items-start justify-between gap-3"
              >
                <p className="t-13 text-[color:var(--ink)]">
                  配料表第 {hit.order} 项「{hit.ingredientRawName}」命中您的本地忌口词条「{hit.matchedTerm}」
                </p>
                <EvidenceTag sourceId={hit.sourceId} evidenceId={hit.evidenceId} />
              </div>
            ))}
            <p className="t-12 text-[color:var(--ink-muted)] leading-relaxed pt-3 max-w-[68ch]">
              {hits.disclaimer}
            </p>
          </div>
        ) : null}
      </DocSection>

      <DocSection
        index="02"
        title="饮食计划 · 本地目标与今日记录"
        subtitle="目标与记录只保存在本浏览器；数值条仅表示「已记录数值 / 自设目标值」的算术比值，不做任何评价。"
        right={<StatusTag tone="none">纯本地计算</StatusTag>}
        marginalia={
          <>
            <MarginNote label="记录条数" value={`${plan?.logs.length ?? 0} 条`} />
            <MarginNote label="数据来源" value={bundle?.ingredients ? "当前档案营养成分原文 × 克数等比换算" : "无档案数据，从 0 起始"} />
            <MarginNote label="换算方式" value="纯算术；不做四舍五入之外的加工" />
          </>
        }
      >
        {plan ? (
          <>
            <p className="t-12 label-track text-[color:var(--ink-faint)] pb-2">
              每日目标 · 可自行修改
            </p>
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
              {TARGET_LABELS.map((item) => (
                <label key={item.key}>
                  <span className="t-12 text-[color:var(--ink-muted)] block pb-1">
                    {item.label}
                    <span className="text-[color:var(--ink-faint)]"> / {item.unit}</span>
                  </span>
                  <input
                    type="number"
                    value={plan.targets[item.key]}
                    min={0}
                    onChange={(event) => updateTarget(item.key, Number(event.target.value) || 0)}
                    className="field-input field-input--mono"
                  />
                </label>
              ))}
            </div>

            <div className="pt-6">
              <p className="t-12 label-track text-[color:var(--ink-faint)] pb-2">
                今日累计 · 与自设目标并列
              </p>
              {TARGET_LABELS.map((item) => {
                const current = totals[item.key];
                const target = plan.targets[item.key];
                const pct = target > 0 ? Math.min(100, Math.round((current / target) * 100)) : 0;
                return (
                  <div key={item.key} className="py-1.5">
                    <div className="flex justify-between t-12 text-[color:var(--ink-muted)] pb-1">
                      <span>{item.label}</span>
                      <span className="mono text-[color:var(--ink-soft)]">
                        {current.toFixed(1)} / {target} {item.unit}
                        <span className="text-[color:var(--ink-faint)] ml-2">{pct}%</span>
                      </span>
                    </div>
                    <div className="meter">
                      <div className="meter__fill" style={{ width: `${pct}%` }} />
                    </div>
                  </div>
                );
              })}
              <p className="t-12 text-[color:var(--ink-muted)] leading-relaxed pt-2 max-w-[68ch]">
                数值条仅表示已记录数值与自设目标值的算术比值，不构成营养建议。
              </p>
            </div>

            <div className="pt-6">
              <p className="t-12 label-track text-[color:var(--ink-faint)] pb-2">今日记录</p>
              <div className="flex flex-col sm:flex-row gap-3 sm:items-end">
                <label className="flex-1 min-w-0">
                  <span className="t-12 text-[color:var(--ink-muted)] block pb-1">条目名称</span>
                  <input
                    value={logLabel}
                    onChange={(event) => setLogLabel(event.target.value)}
                    placeholder="例如：早餐乳粉 40g"
                    className="field-input"
                  />
                </label>
                <label className="w-full sm:w-24">
                  <span className="t-12 text-[color:var(--ink-muted)] block pb-1">克数</span>
                  <input
                    type="number"
                    value={logServing}
                    min={1}
                    onChange={(event) => setLogServing(event.target.value)}
                    placeholder="克数"
                    className="field-input field-input--mono"
                  />
                </label>
                <button type="button" onClick={() => void addLog()} className="btn btn--primary shrink-0">
                  记录
                </button>
              </div>
              <p className="t-12 text-[color:var(--ink-faint)] pt-2 leading-relaxed">
                {bundle?.ingredients
                  ? "记录时按当前档案「每 100 克」原始数值按克数等比换算（纯算术）。"
                  : "当前无档案数据，记录会以 0 起始，需手动补齐数值。"}
              </p>

              <div className="pt-3 max-h-64 overflow-y-auto">
                {plan.logs.length === 0 ? (
                  <EmptyState text="今日尚无记录。" />
                ) : (
                  plan.logs.map((entry) => (
                    <div
                      key={entry.id}
                      className="py-2 border-b border-dotted border-[color:var(--rule-hair)] flex items-start justify-between gap-3"
                    >
                      <div className="min-w-0">
                        <p className="t-13 text-[color:var(--ink)]">{entry.label}</p>
                        <p className="mono text-[color:var(--ink-muted)] pt-0.5">
                          {entry.origin} · 能量 {entry.energyKj.toFixed(0)}kJ · 蛋白{" "}
                          {entry.proteinG.toFixed(1)}g · 脂 {entry.fatG.toFixed(1)}g · 碳水{" "}
                          {entry.carbohydrateG.toFixed(1)}g · 钠 {entry.sodiumMg.toFixed(0)}mg
                        </p>
                      </div>
                      <button
                        type="button"
                        onClick={() => void removeLog(entry.id)}
                        className="btn btn--text shrink-0"
                      >
                        删除
                      </button>
                    </div>
                  ))
                )}
              </div>
            </div>
          </>
        ) : (
          <EmptyState text="正在读取本地饮食计划…" />
        )}
      </DocSection>
    </div>
  );
}

/** 从档案营养成分原文机械解析数值（仅算术换算，不做任何评价）。 */
function parseNutritionDigits(
  bundle: DossierBundle | null,
  factor: number,
): { energyKj: number; proteinG: number; fatG: number; carbohydrateG: number; sodiumMg: number; source: string } {
  const empty = { energyKj: 0, proteinG: 0, fatG: 0, carbohydrateG: 0, sodiumMg: 0, source: "MANUAL" };
  if (!bundle?.ingredients) return empty;

  const map: Record<string, keyof typeof empty> = {
    能量: "energyKj",
    蛋白质: "proteinG",
    脂肪: "fatG",
    碳水化合物: "carbohydrateG",
    钠: "sodiumMg",
  };
  const out = { ...empty, source: "NUTRITION_LABEL" as string };
  for (const entry of bundle.ingredients.nutrition_facts) {
    const key = map[entry.nutrient];
    if (!key) continue;
    const number = Number((entry.raw_value.match(/[0-9]+(\.[0-9]+)?/) ?? ["0"])[0]);
    // 钠单位为毫克，其余目标单位与原文一致（千焦/克）
    (out[key] as number) = Number((number * factor).toFixed(2));
  }
  return out;
}
