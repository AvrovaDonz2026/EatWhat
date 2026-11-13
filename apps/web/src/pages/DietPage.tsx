import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  buildSourceLabeler,
  DocSection,
  EmptyState,
  MarginNote,
  shortDate,
  SourceLine,
  SourceNotes,
  StatusTag,
  TraceDisclosure,
  type TraceRecord,
} from "@/components/ui";
import {
  addRule,
  clearRules,
  deleteRule,
  exportLocalData,
  isNrvValue,
  listRules,
  loadPlan,
  NRV_IS_UPPER_BOUND,
  savePlan,
  type DietLogEntry,
  type DietPlan,
  type DietRule,
  type DietTargets,
} from "@/local_store/db";
import {
  IMPORT_MODE_HINT,
  IMPORT_MODE_LABEL,
  applyBackup,
  readFileAsText,
  validateBackup,
  type BackupImportMode,
} from "@/local_store/backup";
import { GB7718_ALLERGEN_PRESETS } from "@/local_store/allergen_presets";
import { matchIngredients } from "@/engine/local_diet_matcher";
import {
  NUTRIENT_LABEL,
  NUTRIENT_ORDER,
  formatGrams,
  formatNutrient,
  formatRatioPercent,
  missingLabels,
  scaledNutrition,
  type NutrientKey,
} from "@/engine/nutrition_scaling";
import { buildHashIndex, demoGtins, loadDossier, withHashes, type DossierBundle } from "@/services/static_dossier_client";

interface DietPageProps {
  gtin: string;
  showToast: (text: string) => void;
  onDataChanged: () => void;
}

/**
 * 参考值行按「能量 / 蛋白质 / 脂肪 / 碳水 / 钠」顺序展示。
 *
 * 默认数值取自 GB 28050—2011 附录 A 表 A.1 的营养素参考值（NRV）；用户改动某一项后，
 * 该项按「自设值」呈现，不再挂标准出处（逐项判定，见 `isNrvValue`）。
 */
const TARGET_LABELS: { key: keyof DietTargets; label: string; unit: string }[] = NUTRIENT_ORDER.map(
  (key) => ({ key, label: NUTRIENT_LABEL[key].label, unit: NUTRIENT_LABEL[key].unit }),
);

type ImportStatus =
  | { kind: "idle" }
  | { kind: "busy"; message: string }
  | { kind: "ok"; message: string }
  | { kind: "error"; message: string; issues: string[] };

const emptyTotals = (): Record<NutrientKey, number> => ({
  energyKj: 0,
  proteinG: 0,
  fatG: 0,
  carbohydrateG: 0,
  sodiumMg: 0,
});

export function DietPage({ gtin, showToast, onDataChanged }: DietPageProps) {
  const [rules, setRules] = useState<DietRule[]>([]);
  const [term, setTerm] = useState("");
  const [aliases, setAliases] = useState("");
  const [plan, setPlan] = useState<DietPlan | null>(null);
  const [bundle, setBundle] = useState<DossierBundle | null>(null);
  const [activeGtin, setActiveGtin] = useState("");
  const [gtinInput, setGtinInput] = useState("");
  const [productBusy, setProductBusy] = useState(false);
  const [productNote, setProductNote] = useState<string | null>(null);
  const [logLabel, setLogLabel] = useState("");
  const [logServing, setLogServing] = useState("100");
  const [logError, setLogError] = useState<string | null>(null);

  const [importMode, setImportMode] = useState<BackupImportMode>("merge");
  const [importStatus, setImportStatus] = useState<ImportStatus>({ kind: "idle" });
  const importInputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    setRules(await listRules());
    setPlan(await loadPlan());
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const loadProduct = useCallback(async (rawGtin: string) => {
    const normalized = rawGtin.replace(/[^0-9]/g, "");
    if (!normalized) {
      setBundle(null);
      setActiveGtin("");
      setProductNote("未指定折算依据商品；记录前需先载入一份含营养成分表的档案。");
      return;
    }
    setProductBusy(true);
    try {
      const loaded = await loadDossier(normalized);
      if (!loaded) {
        setBundle(null);
        setActiveGtin(normalized);
        setProductNote("当前已接入静态档案库未匹配到该条码，无法作为折算依据。");
        return;
      }
      setBundle(loaded);
      setActiveGtin(normalized);
      setGtinInput(normalized);
      setProductNote(
        loaded.ingredients
          ? `已载入 ${loaded.record.entity_id} 的配料与营养原文（来源 ${loaded.origin}）。`
          : "该条码档案不含配料与营养分片，无法折算。",
      );
    } finally {
      setProductBusy(false);
    }
  }, []);

  useEffect(() => {
    // 父组件在检索 / 打开档案时传入条码：跟随切换折算依据。
    // 刻意只依赖父级 gtin：activeGtin 是本地状态，作为依赖会造成重复载入。
    if (gtin && gtin !== activeGtin) {
      setGtinInput(gtin);
      void loadProduct(gtin);
    }
  }, [gtin]);

  const hits = useMemo(() => {
    if (!bundle?.ingredients) return null;
    return matchIngredients(bundle.ingredients.ingredients, bundle.ingredients.explicit_allergens, rules);
  }, [bundle, rules]);

  /** source_id → 可读名称：取自本次查询的 coverage_manifest。 */
  const sourceLabel = useMemo(
    () => buildSourceLabeler(bundle?.record.coverage_manifest ?? bundle?.product?.coverage_manifest ?? []),
    [bundle],
  );

  /** 只读哈希索引：营养条目本身不带哈希，按 evidence_id 从观测与时间线补齐。 */
  const hashFor = useMemo(() => buildHashIndex(bundle), [bundle]);

  /**
   * 在配料文档里按 evidence_id 反查 retrieved_at（只读查找，不改写数据）。
   * 必须定义在消费它的 useMemo 之前，避免渲染期取到未初始化的绑定。
   */
  const docRetrievedAt = useCallback(
    (evidenceId: string) => {
      const doc = bundle?.ingredients;
      if (!doc) return undefined;
      const groups: { evidence_id: string; retrieved_at: string }[][] = [
        doc.ingredients,
        doc.explicit_allergens,
      ];
      for (const group of groups) {
        const found = group.find((item) => item.evidence_id === evidenceId);
        if (found) return found.retrieved_at;
      }
      return undefined;
    },
    [bundle],
  );

  const dietHitRecords = useMemo(() => {
    if (!hits) return [];
    return withHashes(
      [
        ...hits.ingredientHits.map((hit) => ({
          source_id: hit.sourceId,
          evidence_id: hit.evidenceId,
          retrieved_at: docRetrievedAt(hit.evidenceId),
        })),
        ...hits.allergenHits.map((hit) => ({
          source_id: hit.sourceId,
          evidence_id: hit.evidenceId,
          retrieved_at: docRetrievedAt(hit.evidenceId),
        })),
      ],
      buildHashIndex(bundle),
    );
  }, [hits, docRetrievedAt, bundle]);

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

  const grams = Number(logServing);
  const gramsValid = Number.isFinite(grams) && grams > 0 && grams <= 100000;

  /** 本次折算结果（纯算术），同时作为记录写入的内容与界面预览。 */
  const draft = useMemo(
    () =>
      scaledNutrition(
        bundle?.ingredients?.nutrition_facts,
        gramsValid ? grams : 0,
        { gtin: activeGtin, entity_id: bundle?.record.entity_id ?? "" },
      ),
    [bundle, grams, gramsValid, activeGtin],
  );

  /** 折算证据绑定：补上 source 可读名与 payload_sha256，随记录一起落盘。 */
  const draftEvidence = useMemo(
    () =>
      draft.evidence.map((ref) => ({
        source_id: ref.source_id,
        source_name: sourceLabel(ref.source_id),
        evidence_id: ref.evidence_id,
        retrieved_at: ref.retrieved_at,
        payload_sha256: hashFor(ref.evidence_id),
      })),
    [draft, sourceLabel, hashFor],
  );

  const draftRecords = useMemo(
    () =>
      withHashes(
        draftEvidence.map((ref) => ({
          source_id: ref.source_id,
          evidence_id: ref.evidence_id,
          retrieved_at: ref.retrieved_at,
          payload_sha256: ref.payload_sha256,
        })),
        hashFor,
      ),
    [draftEvidence, hashFor],
  );

  const totals = useMemo(() => {
    const base = emptyTotals();
    if (!plan) return base;
    for (const entry of plan.logs) {
      for (const key of NUTRIENT_ORDER) base[key] += entry[key];
    }
    return base;
  }, [plan]);

  /**
   * 逐项判定「当前数值是否仍等于 GB 28050 附录 A 的参考值」。
   *
   * 纯展示层派生状态：只比较 `plan.targets` 与 `DEFAULT_TARGETS`，不落盘、不进备份、
   * 不新增 schema 字段。用户改过某项后该项即变为「自设值」，不再引用标准出处。
   */
  const nrvFlags = useMemo(() => {
    const flags = {} as Record<keyof DietTargets, boolean>;
    for (const key of NUTRIENT_ORDER) flags[key] = plan ? isNrvValue(key, plan.targets[key]) : false;
    return flags;
  }, [plan]);

  const nrvCount = NUTRIENT_ORDER.filter((key) => nrvFlags[key]).length;
  const allNrv = nrvCount === NUTRIENT_ORDER.length;
  const noneNrv = nrvCount === 0;

  const addLog = async () => {
    if (!plan) return;
    if (!gramsValid) {
      setLogError("克重必须是大于 0 且不超过 100000 的数字。");
      return;
    }
    if (!bundle?.ingredients) {
      setLogError("尚未载入含营养成分表的商品档案，本次未记录（不会以 0 或推测值占位）。");
      return;
    }
    if (!draft.converted) {
      setLogError(draft.note || "该档案营养表无法按克重折算，本次未记录。");
      return;
    }
    setLogError(null);
    const label = logLabel.trim() || `商品 ${activeGtin}`;
    const entry: DietLogEntry = {
      id: `log_${Math.random().toString(36).slice(2, 10)}`,
      label,
      ...draft.totals,
      createdAt: Date.now(),
      origin: "NUTRITION_LABEL",
      grams,
      entity_id: bundle.record.entity_id,
      gtin: activeGtin,
      nutrition_basis: draft.basis,
      evidence: draftEvidence,
    };
    const next: DietPlan = { ...plan, logs: [...plan.logs, entry] };
    setPlan(next);
    setLogLabel("");
    await savePlan(next);
    onDataChanged();
    showToast(`已按 ${formatGrams(grams)} g 折算并记入本地`);
  };

  const removeLog = async (id: string) => {
    if (!plan) return;
    const next: DietPlan = { ...plan, logs: plan.logs.filter((entry) => entry.id !== id) };
    setPlan(next);
    await savePlan(next);
    onDataChanged();
  };

  const exportBackup = async () => {
    const json = await exportLocalData();
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    const stamp = new Date().toISOString().slice(0, 10);
    anchor.href = url;
    anchor.download = `eatwhat-local-backup-${stamp}.json`;
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    showToast("已生成本地 JSON 备份文件（未经网络发送）");
  };

  const importBackup = async (file: File) => {
    setImportStatus({ kind: "busy", message: "正在本地读取并校验文件…" });
    try {
      const text = await readFileAsText(file);
      const validated = validateBackup(text);
      if (!validated.ok) {
        setImportStatus({
          kind: "error",
          message: "备份结构校验未通过，现有本地数据未被改动。",
          issues: validated.issues,
        });
        return;
      }
      const result = await applyBackup(validated.payload, importMode);
      await refresh();
      onDataChanged();
      setImportStatus({
        kind: "ok",
        message: `已按「${IMPORT_MODE_LABEL[importMode]}」写入：${result.rules} 条词条 / ${result.logs} 条饮食记录 / ${result.history} 条扫码历史。`,
      });
    } catch (error) {
      setImportStatus({
        kind: "error",
        message: `导入未执行，现有本地数据未被改动：${error instanceof Error ? error.message : "未知错误"}`,
        issues: [],
      });
    }
  };

  /** 消费视图：来源名取记录时快照的 display_name，避免切换商品后丢失可读名。 */
  const storedTraceRecords = useCallback(
    (entry: DietLogEntry): TraceRecord[] =>
      (entry.evidence ?? []).map((ref) => ({
        source_id: ref.source_id,
        evidence_id: ref.evidence_id,
        retrieved_at: ref.retrieved_at,
        payload_sha256: ref.payload_sha256,
      })),
    [],
  );

  const storedLabeler = useCallback(
    (entry: DietLogEntry) => {
      const names = new Map<string, string>();
      for (const ref of entry.evidence ?? []) {
        if (ref.source_id && ref.source_name) names.set(ref.source_id, ref.source_name);
      }
      return (sourceId?: string | null) => {
        if (!sourceId) return "来源未标注";
        return names.get(sourceId) ?? sourceId;
      };
    },
    [],
  );

  return (
    <div className="space-y-10">
      <DocSection
        plain
        sectionKey="diet-rules"
        title="你写的忌口词"
        subtitle="这些词只存在这台设备上。比对是字面的：你的词出现在包装文字里，就列出来，不做任何推断。"
        right={<StatusTag tone="filled">IndexedDB</StatusTag>}
        marginalia={
          <>
            <MarginNote label="词条条数" value={`${rules.length} 条`} />
            <MarginNote label="存储位置" value="IndexedDB · 库名 product-public-dossier-local · 表 diet_rules" />
            <MarginNote label="归一化" value="NFKC → 去空白 → 小写，再做子串比对" />
          </>
        }
      >
        <p className="consumer-subhead">
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
                className={`t-12 border transition-colors flex items-center gap-1 text-left min-h-[44px] px-2.5 py-2 ${
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
        </div>

        <div className="pt-6">
          <p className="consumer-subhead">已保存的词条</p>
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
            <p className="consumer-subhead">
              {activeGtin ? `条码 ${activeGtin} 的配料里，这些对上了` : "这条条码的配料里，这些对上了"}
            </p>
            {hits.ingredientHits.map((hit, index) => (
              <div
                key={`${hit.evidenceId}-${index}`}
                className="py-2 border-b border-dotted border-[color:var(--rule-hair)]"
              >
                <p className="t-14 text-[color:var(--ink)]">
                  配料表第 {hit.order} 项「{hit.ingredientRawName}」命中您的本地忌口词条「{hit.matchedTerm}」
                </p>
                <SourceLine
                  className="m-desktop-only"
                  name={sourceLabel(hit.sourceId)}
                  date={shortDate(docRetrievedAt(hit.evidenceId))}
                />
              </div>
            ))}
            <p className="t-12 text-[color:var(--ink-muted)] leading-relaxed pt-3 max-w-[68ch]">
              {hits.disclaimer}
            </p>
            <TraceDisclosure records={dietHitRecords} label="查看来源与证据" />
          </div>
        ) : null}
      </DocSection>

      <DocSection
        plain
        sectionKey="diet-plan"
        title="吃了多少，按克重折算"
        subtitle="折算只做一件事：包装营养成分表的「每 100 克」原文数值 × 你输入的克重 / 100，纯算术。默认数值取自 GB 28050—2011 附录 A 表 A.1 的营养素参考值（NRV），该标准把它定义为「专用于食品营养标签，用于比较食品营养成分含量的参考值」，不是个人摄入量标准；改动任一数值后，该项按自设值呈现，不再标注标准出处。界面不做任何评价。"
        right={<StatusTag tone="none">纯本地计算</StatusTag>}
        marginalia={
          <>
            <MarginNote label="记录条数" value={`${plan?.logs.length ?? 0} 条`} />
            <MarginNote
              label="折算依据"
              value={
                bundle?.ingredients
                  ? `${bundle.record.entity_id} · 营养原文 ${bundle.ingredients.nutrition_facts.length} 条`
                  : "尚未载入含营养成分表的档案"
              }
            />
            <MarginNote label="换算方式" value="每 100 克原文数值 × 克重 / 100；不做四舍五入之外的加工" />
            {draft.missing.length > 0 ? (
              <MarginNote label="该档案缺少" value={missingLabels(draft.missing).join("、")} />
            ) : null}
            {draft.converted && draftRecords.length > 0 ? (
              <div>
                <p className="consumer-subhead">营养数据来源</p>
                <SourceNotes records={draftRecords} labeler={sourceLabel} />
              </div>
            ) : null}
          </>
        }
      >
        <p className="consumer-subhead">
          折算依据：先载入一件商品的营养成分表
        </p>
        <div className="flex flex-col sm:flex-row gap-3 sm:items-end">
          <label className="flex-1 min-w-0">
            <span className="t-12 text-[color:var(--ink-muted)] block pb-1">商品条码</span>
            <input
              value={gtinInput}
              onChange={(event) => setGtinInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void loadProduct(gtinInput);
              }}
              placeholder="输入条码，例如 6901234567892"
              inputMode="numeric"
              aria-label="折算依据条码"
              className="field-input field-input--mono"
            />
          </label>
          <button
            type="button"
            disabled={productBusy}
            onClick={() => void loadProduct(gtinInput)}
            className="btn btn--quiet shrink-0"
          >
            {productBusy ? "载入中…" : "载入档案营养表"}
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 pt-3">
          <span className="consumer-subhead">样例条码</span>
          {demoGtins().map((item) => (
            <button
              key={item.gtin}
              type="button"
              onClick={() => {
                setGtinInput(item.gtin);
                void loadProduct(item.gtin);
              }}
              className="mono inline-flex items-center min-h-[44px] px-2.5 py-2 border border-[color:var(--rule)] text-[color:var(--ink-soft)] hover:border-[color:var(--teal)] hover:text-[color:var(--teal-deep)] transition-colors"
            >
              {item.gtin}
            </button>
          ))}
        </div>
        {productNote ? (
          <p className="t-12 text-[color:var(--ink-muted)] pt-2 leading-relaxed max-w-[68ch]">{productNote}</p>
        ) : null}
        {bundle?.ingredients && bundle.ingredients.nutrition_facts.length > 0 ? (
          <div className="pt-3">
            <p className="consumer-subhead">本次将使用的营养原文</p>
            <table className="data-table">
              <thead>
                <tr>
                  <th>营养素</th>
                  <th className="text-right">原文数值</th>
                </tr>
              </thead>
              <tbody>
                {bundle.ingredients.nutrition_facts.map((item, index) => (
                  <tr key={`${item.evidence_id}-${index}`}>
                    <td>{item.nutrient}</td>
                    <td className="text-right mono t-14">{item.raw_value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}

        {plan ? (
          <>
            <p
              className="consumer-subhead pt-8"
              data-diet-target-header={allNrv ? "nrv" : noneNrv ? "self" : "mixed"}
            >
              {allNrv
                ? "营养素参考值（NRV · GB 28050—2011 附录 A）· 可自行修改"
                : noneNrv
                  ? "自设值（可自行修改）"
                  : "参考值 / 自设值（可自行修改）"}
            </p>
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 m-target-grid">
              {TARGET_LABELS.map((item) => (
                <label key={item.key}>
                  <span
                    className="t-12 text-[color:var(--ink-muted)] block pb-1"
                    data-diet-target-label={item.key}
                  >
                    {item.label}
                    <span className="text-[color:var(--ink-faint)]"> / {item.unit}</span>
                    <span className="text-[color:var(--ink-faint)]">
                      {" "}
                      · {nrvFlags[item.key] ? "参考值" : "自设值"}
                    </span>
                  </span>
                  <input
                    type="number"
                    value={plan.targets[item.key]}
                    min={0}
                    onChange={(event) => updateTarget(item.key, Number(event.target.value) || 0)}
                    className="field-input field-input--mono"
                    data-diet-target-input={item.key}
                  />
                </label>
              ))}
            </div>
            <p
              className="t-12 text-[color:var(--ink-faint)] pt-2 leading-relaxed max-w-[68ch]"
              data-diet-nrv-note={allNrv ? "all" : noneNrv ? "none" : "mixed"}
            >
              {allNrv ? (
                <>
                  参考值取自 GB 28050—2011《预包装食品营养标签通则》附录 A 表 A.1 的营养素参考值（NRV）：
                  五项均与该表相同，其中脂肪按标准写作「≤60 g」，计算百分比时按 60 计。
                </>
              ) : noneNrv ? (
                <>五项均已改为自设值，本页不再引用 GB 28050 的营养素参考值。</>
              ) : (
                <>
                  与 GB 28050—2011 附录 A 表 A.1 相同的项标为「参考值」，改动过的项标为「自设值」，
                  后者不再引用该标准（脂肪在该表中写作「≤60 g」，计算百分比时按 60 计）。
                </>
              )}
              该标准第 2.6 条把 NRV 定义为「专用于食品营养标签，用于比较食品营养成分含量的参考值」，
              不是个人摄入量标准，本页不据此给出任何摄入结论。
            </p>

            <div className="pt-6">
              <p className="consumer-subhead">
                今日累计 · 已计入 / {allNrv ? "参考值（NRV）" : "参考值或自设值"}
              </p>
              {TARGET_LABELS.map((item) => {
                const current = totals[item.key];
                const target = plan.targets[item.key];
                const hasTarget = target > 0;
                const isNrv = nrvFlags[item.key];
                const upperBound = isNrv && NRV_IS_UPPER_BOUND[item.key];
                const ratio = hasTarget ? (current / target) * 100 : 0;
                const barWidth = Math.min(100, Math.max(0, ratio));
                const exceeded = hasTarget && current > target;
                /** 纯算术百分比：对照值为 0 时不计算也不渲染（避免除零与伪造值）。 */
                const percent = hasTarget ? formatRatioPercent(ratio) : null;
                return (
                  <div
                    key={item.key}
                    className="py-2 border-b border-[color:var(--rule-hair)]"
                    data-diet-meter-row={item.key}
                    data-diet-target-source={isNrv ? "nrv" : "self"}
                    data-diet-upper-bound={upperBound ? "true" : "false"}
                  >
                    <div className="flex flex-wrap justify-between gap-x-4 gap-y-0.5 t-12 text-[color:var(--ink-muted)]">
                      <span>{item.label}</span>
                      <span className="mono text-[color:var(--ink-soft)]">
                        已计入 {formatNutrient(item.key, current)} / {isNrv ? "参考值" : "自设值"}{" "}
                        {upperBound ? "≤" : ""}
                        {formatNutrient(item.key, target)} {item.unit}
                      </span>
                    </div>
                    <div className="hairline-meter mt-1.5" aria-hidden="true">
                      <div className="hairline-meter__fill" style={{ width: `${barWidth}%` }} />
                      {/* 100% 刻度线：中性标尺刻度，两种状态都渲染，不编码任何等级。 */}
                      <div className="hairline-meter__tick" />
                    </div>
                    <p
                      className="t-12 text-[color:var(--ink-muted)] pt-1 break-words"
                      data-diet-meter-caption={item.key}
                    >
                      {!hasTarget
                        ? "未设置参考值或自设值；仅记录已计入数值。"
                        : exceeded
                          ? `已超出目标 ${formatNutrient(item.key, current - target)} ${item.unit}`
                          : isNrv
                            ? "未超出该参考值"
                            : "未超出当前自设值"}
                      {percent === null ? null : (
                        <>
                          {" · "}
                          <span className="mono">
                            {isNrv ? "NRV" : "占自设值"} {percent}%
                          </span>
                        </>
                      )}
                    </p>
                  </div>
                );
              })}
              <p className="t-12 text-[color:var(--ink-muted)] leading-relaxed pt-2 max-w-[68ch]">
                进度线仅表示「已计入数值 / 参考值（或自设值）」的算术比值；下方百分比为同一比值的算术结果，
                按 GB 28050—2011 第 A.2 条的修约间隔取整数（间隔 1）。超出时只做中性文字标注，
                不构成任何营养建议。
              </p>
            </div>

            <div className="pt-6">
              <p className="consumer-subhead">记录一条食物 · 输入克重</p>
              <div className="flex flex-col sm:flex-row gap-3 sm:items-end">
                <label className="flex-1 min-w-0">
                  <span className="t-12 text-[color:var(--ink-muted)] block pb-1">条目名称</span>
                  <input
                    value={logLabel}
                    onChange={(event) => setLogLabel(event.target.value)}
                    placeholder={`例如：早餐乳粉 50g（留空则记为 商品 ${activeGtin || "—"}）`}
                    className="field-input"
                  />
                </label>
                <label className="w-full sm:w-28">
                  <span className="t-12 text-[color:var(--ink-muted)] block pb-1">克重（g）</span>
                  <input
                    type="number"
                    value={logServing}
                    min={1}
                    step={0.1}
                    onChange={(event) => setLogServing(event.target.value)}
                    placeholder="例如 50"
                    aria-label="克重"
                    className="field-input field-input--mono"
                  />
                </label>
                <button
                  type="button"
                  disabled={!gramsValid || !bundle?.ingredients || !draft.converted}
                  onClick={() => void addLog()}
                  className="btn btn--primary shrink-0"
                >
                  记录
                </button>
              </div>

              {bundle?.ingredients ? (
                <div className="pt-3">
                  <p className="t-12 text-[color:var(--ink-muted)] leading-relaxed max-w-[68ch]">
                    本次折算：{draft.note}
                  </p>
                  {draft.converted ? (
                    <p className="mono text-[color:var(--ink-soft)] pt-1">
                      {NUTRIENT_ORDER.map(
                        (key) =>
                          `${NUTRIENT_LABEL[key].label} ${formatNutrient(key, draft.totals[key])}${NUTRIENT_LABEL[key].unit}`,
                      ).join(" · ")}
                    </p>
                  ) : null}
                  {draft.missing.length > 0 ? (
                    <p className="t-12 text-[color:var(--ink-muted)] pt-1">
                      该档案营养表缺少：{missingLabels(draft.missing).join("、")}（未计入，不以推测值补齐）。
                    </p>
                  ) : null}
                  {draft.conflicting.length > 0 ? (
                    <p className="t-12 text-[color:var(--ink-muted)] pt-1">
                      该档案中同项营养素有多条不一致原文（{missingLabels(draft.conflicting).join("、")}
                      ），按并列处理，本次未折算该项。
                    </p>
                  ) : null}
                  <p className="t-12 text-[color:var(--ink-faint)] pt-1">
                    折算依据：entity_id {bundle.record.entity_id} · {draft.basis}
                  </p>
                  <SourceNotes records={draftRecords} labeler={sourceLabel} />
                  {draftRecords.length > 0 ? (
                    <TraceDisclosure records={draftRecords} label="查看本次折算的营养来源与证据" />
                  ) : null}
                </div>
              ) : (
                <p className="t-12 text-[color:var(--ink-muted)] pt-3 leading-relaxed max-w-[68ch]">
                  尚未载入含营养成分表的商品档案。本页不会用 0 或推测值占位，请先在上方载入条码。
                </p>
              )}

              {logError ? (
                <p className="t-13 text-[color:var(--ink)] pt-2 leading-relaxed border-l-2 border-[color:var(--rule-strong)] pl-3 max-w-[68ch]">
                  {logError}
                </p>
              ) : null}
            </div>

            <div className="pt-6">
              <p className="consumer-subhead">今日记录</p>
              {plan.logs.length === 0 ? (
                <EmptyState text="今日尚无记录。" />
              ) : (
                <div className="max-h-80 overflow-y-auto">
                  {plan.logs.map((entry) => {
                    const records = storedTraceRecords(entry);
                    const labeler = storedLabeler(entry);
                    return (
                      <div
                        key={entry.id}
                        className="py-2.5 border-b border-dotted border-[color:var(--rule-hair)] flex items-start justify-between gap-3"
                      >
                        <div className="min-w-0">
                          <p className="t-13 text-[color:var(--ink)]">
                            {entry.label}
                            {entry.grams ? (
                              <span className="mono text-[color:var(--ink-muted)]"> · {formatGrams(entry.grams)} g</span>
                            ) : null}
                          </p>
                          <p className="mono text-[color:var(--ink-muted)] pt-0.5">
                            {NUTRIENT_ORDER.map(
                              (key) =>
                                `${NUTRIENT_LABEL[key].label} ${formatNutrient(key, entry[key])}${NUTRIENT_LABEL[key].unit}`,
                            ).join(" · ")}
                          </p>
                          <p className="t-12 text-[color:var(--ink-faint)] pt-0.5">
                            {entry.origin}
                            {entry.entity_id ? ` · entity_id ${entry.entity_id}` : ""}
                            {entry.gtin ? ` · 条码 ${entry.gtin}` : ""}
                            {entry.nutrition_basis ? ` · ${entry.nutrition_basis}` : ""}
                          </p>
                          {records.length > 0 ? (
                            <>
                              <SourceNotes records={records} labeler={labeler} />
                              <TraceDisclosure records={records} label="查看本条折算的来源与证据" />
                            </>
                          ) : null}
                        </div>
                        <button
                          type="button"
                          onClick={() => void removeLog(entry.id)}
                          className="btn btn--text shrink-0"
                        >
                          删除
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </>
        ) : (
          <EmptyState text="正在读取本地饮食计划…" />
        )}
      </DocSection>

      <DocSection
        plain
        sectionKey="diet-backup"
        title="备份这些数据"
        subtitle="导出一个本地 JSON 文件；导入时先检查结构，没问题才写进本地数据库。全程零网络：不走上传接口，也不向任何第三方发送。"
        right={<StatusTag tone="none">Blob + FileReader</StatusTag>}
        marginalia={
          <>
            <MarginNote label="备份内容" value="diet_rules / diet_plan / scan_history" />
            <MarginNote label="导入方式" value="覆盖 或 合并（按 id 去重，同 id 以备份为准）" />
            <MarginNote label="失败保护" value="结构校验不通过时一行都不写入，现有数据保持不变" />
            <MarginNote label="网络" value="导出与导入均在浏览器本地完成，不发任何请求" />
          </>
        }
      >
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => void exportBackup()} className="btn btn--primary">
            导出本地备份
          </button>
          <input
            ref={importInputRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            aria-label="本地备份文件"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void importBackup(file);
              event.target.value = "";
            }}
          />
          <button
            type="button"
            onClick={() => importInputRef.current?.click()}
            className="btn btn--quiet"
          >
            导入本地备份
          </button>
        </div>
        <p className="t-12 text-[color:var(--ink-faint)] pt-2 leading-relaxed max-w-[68ch]">
          导出文件仅写入本机下载目录，不经网络发送。导入时文件由浏览器本地读取（FileReader），不上传服务器。
        </p>

        <div className="pt-5">
          <p className="consumer-subhead">导入方式</p>
          <div className="space-y-2">
            {(["merge", "replace"] as BackupImportMode[]).map((mode) => (
              <label key={mode} className="m-choice-row t-13 text-[color:var(--ink-soft)]">
                <input
                  type="radio"
                  name="backup-import-mode"
                  value={mode}
                  checked={importMode === mode}
                  onChange={() => setImportMode(mode)}
                  className="mt-1"
                  data-tap-exempt="native-radio"
                />
                <span>
                  <span className="text-[color:var(--ink)]">{IMPORT_MODE_LABEL[mode]}</span>
                  <span className="t-12 text-[color:var(--ink-muted)]"> · {IMPORT_MODE_HINT[mode]}</span>
                </span>
              </label>
            ))}
          </div>
        </div>

        <div className="pt-5">
          {importStatus.kind === "idle" ? (
            <EmptyState text="尚未导入任何文件。选择备份 JSON 后会先校验结构，再按上方方式写入。" />
          ) : null}
          {importStatus.kind === "busy" ? (
            <p className="t-13 text-[color:var(--ink-soft)]">{importStatus.message}</p>
          ) : null}
          {importStatus.kind === "ok" ? (
            <div className="border-l-2 border-[color:var(--teal)] pl-3 py-1">
              <p className="t-13 text-[color:var(--ink)]">{importStatus.message}</p>
              <p className="t-12 text-[color:var(--ink-muted)] pt-0.5">数据只写入本浏览器 IndexedDB。</p>
            </div>
          ) : null}
          {importStatus.kind === "error" ? (
            <div className="border border-[color:var(--rule-strong)] px-3 py-2.5">
              <p className="t-13 text-[color:var(--ink)]">{importStatus.message}</p>
              {importStatus.issues.length > 0 ? (
                <ol className="pt-1.5 space-y-0.5">
                  {importStatus.issues.map((issue, index) => (
                    <li key={`${issue}-${index}`} className="t-12 text-[color:var(--ink-soft)]">
                      {index + 1}. {issue}
                    </li>
                  ))}
                </ol>
              ) : null}
            </div>
          ) : null}
        </div>
      </DocSection>
    </div>
  );
}
