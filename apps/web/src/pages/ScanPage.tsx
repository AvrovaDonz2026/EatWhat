import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  buildSourceLabeler,
  ConsumerSource,
  DemoMark,
  DocSection,
  EmptyState,
  shortDate,
  SourceFocusSwitch,
  SourceLine,
  SourceNotes,
  TraceDisclosure,
} from "@/components/ui";
import { formatHitNotice, matchIngredients, matchOcrText, normalizeText } from "@/engine/local_diet_matcher";
import { addScanHistory, listRules, type DietRule } from "@/local_store/db";
import {
  buildHashIndex,
  demoGtins,
  loadDossier,
  lookupGtin,
  withHashes,
  type DossierBundle,
  type GtinLookup,
} from "@/services/static_dossier_client";
import { detectBarcode } from "@/ocr/barcode";
import { extractIngredientLines, recognizeLabel, type OcrProgress } from "@/ocr/local_ocr";
import type { CoverageEntry, DossierBlock, IngredientsDocument } from "@/types";

/** 在配料文档里按 evidence_id 找回 retrieved_at（数据层已有，只做查找）。 */
function docRetrievedAt(doc: IngredientsDocument | null, evidenceId: string): string | undefined {
  if (!doc) return undefined;
  const groups: { evidence_id: string; retrieved_at: string }[][] = [
    doc.ingredients,
    doc.nutrition_facts,
    doc.explicit_allergens,
    doc.explicit_unsuitable_groups,
    doc.label_standard_refs,
  ];
  for (const group of groups) {
    const hit = group.find((item) => item.evidence_id === evidenceId);
    if (hit) return hit.retrieved_at;
  }
  return undefined;
}

/**
 * 覆盖状态 → 消费者一句话。
 *
 * 审计视图（DossierPage）继续显示原始枚举与 `access_mode`；这里只回答
 * 「这个来源这次给没给到东西」，不出现 `QUERIED_MATCHED` 这类工程值。
 */
const COVERAGE_SENTENCE: Record<CoverageEntry["coverage_state"], string> = {
  QUERIED_MATCHED: "这次查到了记录",
  QUERIED_NO_MATCH: "这个来源里没有这条条码",
  QUERY_FAILED: "这次没连上",
  NOT_QUERIED: "这次没有查这个来源",
  ACCESS_PENDING: "还没拿到授权，暂时看不到",
  SOURCE_UNAVAILABLE: "这个来源暂时打不开",
};

/** 标识符类型 → 人话。审计视图仍显示原始类型。 */
const IDENTIFIER_PLAIN: Record<string, string> = {
  GTIN: "条码",
  UDI_DI: "UDI 编号",
  SC_NO: "生产许可证号",
  STANDARD_NO: "标准号",
  REG_NO: "注册号",
};

/** 关联方式 → 人话。原始 `link_mode` 仍以 data 属性承载，审计视图显示枚举。 */
const LINK_MODE_PLAIN: Record<string, string> = {
  EXACT_IDENTIFIER_MATCH: "条码完全对得上",
  MANUAL_CONFIRM: "只有别的编号对得上，条码本身不一致，需要你自己确认",
};

interface ScanPageProps {
  onOpenDossier: (gtin: string, entityId: string, origin: "STATIC_LIBRARY" | "DEMO_FALLBACK") => void;
  showToast: (text: string) => void;
  onDataChanged: () => void;
}

const ocrProgressText = (state: OcrProgress): string => {
  const map: Record<string, string> = {
    "loading tesseract core": "加载端侧 OCR 内核",
    "initializing tesseract": "初始化端侧 OCR",
    "loading language traineddata": "加载中英文语言数据",
    "initializing api": "初始化识别接口",
    "recognizing text": "识别配料文字",
  };
  return `${map[state.status] ?? state.status} ${Math.round(state.progress * 100)}%`;
};

/** 从 16 区块的字段里取一段人话事实：按标签分组，值相同的去重，保留来源。 */
interface FactValue {
  value: string;
  sourceId?: string;
}
interface FactRow {
  label: string;
  values: FactValue[];
}

function factsFromBlock(block: DossierBlock | undefined, labels: string[]): FactRow[] {
  if (!block) return [];
  const rows = new Map<string, FactValue[]>();
  for (const field of block.fields) {
    if (!labels.includes(field.label)) continue;
    const value = typeof field.value === "string" || typeof field.value === "number" ? String(field.value) : "";
    if (!value) continue;
    const bucket = rows.get(field.label) ?? [];
    if (!bucket.some((item) => item.value === value)) {
      bucket.push({ value, sourceId: field.source_id });
    }
    rows.set(field.label, bucket);
  }
  return labels
    .filter((label) => rows.has(label))
    .map((label) => ({ label, values: rows.get(label) ?? [] }));
}

export type CompareFocus = "static_archive" | "ocr_text";

export function ScanPage({ onOpenDossier, showToast, onDataChanged }: ScanPageProps) {
  const [query, setQuery] = useState("");
  const [bundle, setBundle] = useState<DossierBundle | null>(null);
  const [lookup, setLookup] = useState<GtinLookup | null>(null);
  const [noMatchGtin, setNoMatchGtin] = useState<string | null>(null);
  const [rules, setRules] = useState<DietRule[]>([]);
  const [busy, setBusy] = useState(false);
  const [statusNote, setStatusNote] = useState<string | null>(null);
  /** 忌口对照的主视图焦点：两份来源始终并列，切换只改变焦点。 */
  const [compareFocus, setCompareFocus] = useState<CompareFocus>("static_archive");

  // 端侧图片 / OCR
  const [imagePreview, setImagePreview] = useState<string | null>(null);
  const [barcodeText, setBarcodeText] = useState<string | null>(null);
  const [ocrText, setOcrText] = useState("");
  const [ocrProgress, setOcrProgress] = useState<OcrProgress | null>(null);
  const [ocrBusy, setOcrBusy] = useState(false);
  const [ocrConfidence, setOcrConfidence] = useState<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const demos = useMemo(() => demoGtins(), []);

  useEffect(() => {
    void (async () => setRules(await listRules()))();
  }, []);

  const runLookup = useCallback(
    async (rawGtin: string) => {
      const gtin = rawGtin.replace(/[^0-9]/g, "");
      if (!gtin) {
        showToast("请输入条码数字，或直接拍一张包装照片");
        return;
      }
      setBusy(true);
      setStatusNote(null);
      setNoMatchGtin(null);
      try {
        const found = await lookupGtin(gtin);
        if (!found) {
          setBundle(null);
          setLookup(null);
          setNoMatchGtin(gtin);
          return;
        }
        setLookup(found);
        const full = await loadDossier(gtin);
        setBundle(full);
        await addScanHistory({ gtin, ocrText: "", origin: found.origin });
        onDataChanged();
      } finally {
        setBusy(false);
      }
    },
    [onDataChanged, showToast],
  );

  const handleFile = useCallback(
    async (file: File) => {
      setOcrText("");
      setOcrConfidence(null);
      setBarcodeText(null);
      const previewUrl = URL.createObjectURL(file);
      setImagePreview(previewUrl);
      setStatusNote("这张照片只在浏览器内存里处理，不上传、不落盘");

      // 1) 端侧条码识别
      try {
        const barcode = await detectBarcode(file);
        if (barcode) {
          setBarcodeText(barcode.text);
          setQuery(barcode.text);
          showToast(`照片里读到条码 ${barcode.text}（${barcode.format}）`);
        } else {
          setStatusNote("照片里没读到条码；仍可以继续识别配料文字，或手动输入");
        }
      } catch (error) {
        console.warn("条码识别失败", error);
        setStatusNote("这台设备暂时用不了条码识别；可以继续识别配料文字，或手动输入");
      }

      // 2) 端侧 OCR
      setOcrBusy(true);
      try {
        const result = await recognizeLabel(file, (state) => setOcrProgress(state));
        setOcrText(result.text);
        setOcrConfidence(result.confidence);
        await addScanHistory({
          gtin: "",
          ocrText: result.text.slice(0, 400),
          origin: "LOCAL_OCR",
        });
        onDataChanged();
      } catch (error) {
        console.warn("端侧 OCR 失败", error);
        showToast("这次没识别完成，可以直接把包装上的配料文字粘贴进来");
      } finally {
        setOcrBusy(false);
        setOcrProgress(null);
      }
    },
    [onDataChanged, showToast],
  );

  const ingredientHits = useMemo(() => {
    if (!bundle?.ingredients) return null;
    return matchIngredients(bundle.ingredients.ingredients, bundle.ingredients.explicit_allergens, rules);
  }, [bundle, rules]);

  const ocrHits = useMemo(() => (ocrText ? matchOcrText(ocrText, rules) : []), [ocrText, rules]);
  const ingredientList = useMemo(() => (ocrText ? extractIngredientLines(ocrText) : []), [ocrText]);

  /** 条码查到的配料那一侧的机械命中条数（配料 + 标签致敏原），与 OCR 侧分别计数。 */
  const staticHitCount =
    (ingredientHits?.ingredientHits.length ?? 0) + (ingredientHits?.allergenHits.length ?? 0);

  const record = bundle?.record ?? lookup?.record ?? null;
  const coverage = record?.coverage_manifest ?? [];
  const ingredientsDoc = bundle?.ingredients ?? null;

  /** source_id → 可读名称，一律取自载荷里的 coverage_manifest.display_name。 */
  const sourceLabel = useMemo(
    () =>
      buildSourceLabeler(coverage.length > 0 ? coverage : bundle?.product?.coverage_manifest ?? [], {
        // 消费者主线用紧凑名（只取来源自己的名称），句内引用不会被机构全称撑满；
        // 审计视图仍然用「机构 · 名称」全称。
        compact: true,
      }),
    [coverage, bundle],
  );

  const candidateRecords = useMemo(() => record?.match_candidates ?? [], [record]);

  /** 只读哈希索引：配料/营养条目不携带哈希，按 evidence_id 从观测与时间线补齐。 */
  const hashFor = useMemo(() => buildHashIndex(bundle), [bundle]);

  const ingredientsRecords = useMemo(() => {
    if (!ingredientsDoc) return [];
    return withHashes(
      [
        ...ingredientsDoc.ingredients,
        ...ingredientsDoc.nutrition_facts,
        ...ingredientsDoc.explicit_allergens,
        ...ingredientsDoc.explicit_unsuitable_groups,
        ...ingredientsDoc.label_standard_refs,
      ],
      hashFor,
    );
  }, [ingredientsDoc, hashFor]);

  const candidateTraceRecords = useMemo(() => withHashes(candidateRecords, hashFor), [candidateRecords, hashFor]);

  const hitRecords = useMemo(() => {
    if (!ingredientHits) return [];
    return withHashes(
      [
        ...ingredientHits.ingredientHits.map((hit) => ({
          source_id: hit.sourceId,
          evidence_id: hit.evidenceId,
          retrieved_at: docRetrievedAt(ingredientsDoc, hit.evidenceId),
        })),
        ...ingredientHits.allergenHits.map((hit) => ({
          source_id: hit.sourceId,
          evidence_id: hit.evidenceId,
          retrieved_at: docRetrievedAt(ingredientsDoc, hit.evidenceId),
        })),
      ],
      hashFor,
    );
  }, [ingredientHits, ingredientsDoc, hashFor]);

  const hitRetrievedAt = useCallback(
    (evidenceId: string) => docRetrievedAt(ingredientsDoc, evidenceId),
    [ingredientsDoc],
  );

  /* ---------------------------------------------------------- 消费者三问的数据 */

  const blocksById = useMemo(() => {
    const map = new Map<string, DossierBlock>();
    for (const block of bundle?.product?.blocks ?? []) map.set(block.block_id, block);
    return map;
  }, [bundle]);

  /** 这是什么：包装与公开记录里的基本写法。不同来源写法不同时全部列出，不做择优。 */
  const identityRows = useMemo(() => {
    const fromBlock = factsFromBlock(blocksById.get("identity_basic"), [
      "商品名称",
      "品牌",
      "净含量",
      "包装形式",
      "条码（GTIN）",
      "条码厂商前缀",
    ]);
    if (fromBlock.length > 0) return fromBlock;
    // 没有 basic_info 区块时退回候选记录里的原文（同样只并列、不合并）
    const rows: FactRow[] = [];
    const names = candidateRecords
      .filter((item) => item.display_name)
      .map((item) => ({ value: item.display_name, sourceId: item.source_id }));
    const uniqueNames = names.filter(
      (item, index) => names.findIndex((other) => other.value === item.value) === index,
    );
    if (uniqueNames.length > 0) rows.push({ label: "商品名称", values: uniqueNames });
    if (record?.gtin) rows.push({ label: "条码", values: [{ value: record.gtin }] });
    return rows;
  }, [blocksById, candidateRecords, record]);

  const productName = identityRows.find((row) => row.label === "商品名称")?.values[0]?.value ?? "";
  const otherNames = identityRows.find((row) => row.label === "商品名称")?.values.slice(1) ?? [];
  const factRows = identityRows.filter((row) => row.label !== "商品名称");

  /** 谁做的：生产许可、注册 / 备案、条码厂商前缀的原文。 */
  const makerRows = useMemo(() => {
    const license = factsFromBlock(blocksById.get("license_records"), [
      "许可编号",
      "被许可人",
      "食品类别",
      "发证日期",
      "公开记录中的状态原文",
      "有效期至",
    ]);
    const registration = factsFromBlock(blocksById.get("registration_records"), [
      "注册/备案编号",
      "登载主体",
      "记录类型",
      "规格",
      "公开记录中的状态原文",
    ]);
    const prefix = factsFromBlock(blocksById.get("company_prefix"), ["条码厂商前缀"]);
    return [
      { group: "生产许可", rows: license },
      { group: "注册 / 备案", rows: registration },
      { group: "条码厂商前缀", rows: prefix },
    ].filter((entry) => entry.rows.length > 0);
  }, [blocksById]);

  const identityRecords = useMemo(
    () =>
      withHashes(
        (blocksById.get("identity_basic")?.fields ?? []).map((field) => ({
          source_id: field.source_id,
          evidence_id: field.evidence_id,
          retrieved_at: field.retrieved_at,
        })),
        hashFor,
      ),
    [blocksById, hashFor],
  );

  const makerRecords = useMemo(() => {
    const fields = ["license_records", "registration_records", "company_prefix"].flatMap(
      (id) => blocksById.get(id)?.fields ?? [],
    );
    return withHashes(
      fields.map((field) => ({
        source_id: field.source_id,
        evidence_id: field.evidence_id,
        retrieved_at: field.retrieved_at,
      })),
      hashFor,
    );
  }, [blocksById, hashFor]);

  return (
    <div className="space-y-14">
      {/* ---------------------------------------------------------------- 入口 */}
      <DocSection
        plain
        sectionKey="consumer-entry"
        title="扫码，看这包东西"
        subtitle="对着包装上的条码扫一下，或者直接输入那串数字。下面给的是各处公开记录里怎么写这件商品，每条后面都写了出处。"
      >
        <div className="flex flex-col sm:flex-row gap-3 sm:items-end">
          <label className="flex-1 min-w-0">
            <span className="consumer-input-label">包装上的条码数字</span>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void runLookup(query);
              }}
              placeholder="例如 6901234567892"
              inputMode="numeric"
              aria-label="条码"
              className="field-input field-input--mono"
            />
          </label>
          <button
            type="button"
            disabled={busy}
            onClick={() => void runLookup(query)}
            className="btn btn--primary shrink-0"
          >
            {busy ? "正在看…" : "看看是什么"}
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 pt-5">
          <span className="consumer-hint">没手边的东西？点一个样例看看结构：</span>
          {demos.map((item) => (
            <button
              key={item.gtin}
              type="button"
              onClick={() => {
                setQuery(item.gtin);
                void runLookup(item.gtin);
              }}
              className="consumer-chip mono"
              data-demo-gtin={item.gtin}
              data-demo-origin={item.origin}
            >
              {item.label}
            </button>
          ))}
        </div>

        {/* 演示样例来源属性：由条目自带的 origin 字段驱动，不在这里硬编码条码 */}
        {demos.some((item) => item.origin === "DEMO_FALLBACK") ? (
          <div className="pt-4">
            <DemoMark scope="scan-demo-list" />
          </div>
        ) : null}
      </DocSection>

      {noMatchGtin ? (
        <DocSection
          plain
          sectionKey="consumer-no-match"
          title="没有找到这条条码的记录"
          subtitle={`条码 ${noMatchGtin}`}
        >
          <p className="consumer-lede">
            我们接入的公开记录里暂时没有这一条。这不代表任何结论——只是这次没查到。
            你可以拍一张包装照片，让本机识别上面的文字，自己对照忌口词。
          </p>
        </DocSection>
      ) : null}

      {record ? (
        <>
          {/* ------------------------------------------------------ 这是什么 */}
          <DocSection
            plain
            sectionKey="consumer-identity"
            title="这是什么"
            subtitle="包装和公开记录里怎么写这件商品。几个来源写法不同时，全部列出，不替你挑一个。"
          >
            <div data-product-identity>
              {productName ? (
                <p className="serif consumer-product-name" data-product-name>
                  {productName}
                </p>
              ) : null}
              {otherNames.length > 0 ? (
                <p className="consumer-name-alt">
                  别的来源写作：
                  {otherNames.map((item, index) => (
                    <span key={`${item.value}-${index}`}>
                      {index > 0 ? "、" : ""}
                      {item.value}
                      {item.sourceId ? (
                        <span className="consumer-inline-source">
                          <span aria-hidden="true">·</span>
                          {sourceLabel(item.sourceId)}
                        </span>
                      ) : null}
                    </span>
                  ))}
                </p>
              ) : null}

              {factRows.length > 0 ? (
                <dl className="consumer-facts">
                  {factRows.map((row) => (
                    <div className="consumer-fact" key={row.label}>
                      <dt className="consumer-fact__label">{row.label}</dt>
                      <dd className="consumer-fact__value">
                        {row.values.map((item, index) => (
                          <span key={`${item.value}-${index}`}>
                            {index > 0 ? <span className="consumer-inline-sep">／</span> : null}
                            {item.value}
                            {/* 只有「同一字段出现两种写法」时才就地标来源：那才是真正需要分辨的地方 */}
                            {row.values.length > 1 && item.sourceId ? (
                              <span className="consumer-inline-source">
                                <span aria-hidden="true">·</span>
                                {sourceLabel(item.sourceId)}
                              </span>
                            ) : null}
                          </span>
                        ))}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : null}
            </div>
            {identityRecords.length > 0 ? (
              <div className="consumer-footnote">
                <SourceNotes records={identityRecords} labeler={sourceLabel} />
              </div>
            ) : null}
          </DocSection>

          {/* ---------------------------------------------------- 里面有什么 */}
          <DocSection
            plain
            sectionKey="consumer-ingredients"
            title="里面有什么"
            subtitle="配料按包装上的顺序逐项列出；营养成分照包装上的数字抄，不做任何折算。"
          >
            {bundle?.origin === "DEMO_FALLBACK" ? (
              <div className="pb-4">
                <DemoMark scope="scan-05-ingredients" />
              </div>
            ) : null}

            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-12 gap-y-10 consumer-stack">
              <div>
                <p className="consumer-subhead">配料表（按包装顺序）</p>
                {ingredientsDoc && ingredientsDoc.ingredients.length > 0 ? (
                  <ol>
                    {ingredientsDoc.ingredients.map((item, index) => (
                      <li key={`${item.evidence_id}-${item.order}-${index}`} className="consumer-list-item">
                        <span className="t-14 text-[color:var(--ink)] flex gap-2 min-w-0">
                          <span className="mono text-[color:var(--ink-faint)] shrink-0 w-5 text-right">
                            {item.order}
                          </span>
                          {/* 术语列禁止收缩：CJK 的 min-content 只有 1 个字宽，
                            * 一旦作为可收缩 flex 子项就会在窄容器里被压成逐字竖排。 */}
                          <span className="term-cell">{item.raw_name}</span>
                        </span>
                        <SourceLine
                          className="m-desktop-only"
                          name={sourceLabel(item.source_id)}
                          date={shortDate(item.retrieved_at)}
                        />
                      </li>
                    ))}
                  </ol>
                ) : (
                  <EmptyState text="这份记录里没有配料表原文。" />
                )}

                <p className="consumer-subhead pt-8">包装上写明的致敏原</p>
                {ingredientsDoc && ingredientsDoc.explicit_allergens.length > 0 ? (
                  <ul>
                    {ingredientsDoc.explicit_allergens.map((item, index) => (
                      <li key={`${item.evidence_id}-${index}`} className="consumer-list-item">
                        <span className="t-14 text-[color:var(--ink)]">{item.raw_text}</span>
                        <SourceLine
                          className="m-desktop-only"
                          name={sourceLabel(item.source_id)}
                          date={shortDate(item.retrieved_at)}
                        />
                        {item.standard_ref ? (
                          <p className="t-12 text-[color:var(--ink-faint)] pt-0.5">
                            包装上引用的标准编号：{item.standard_ref}
                          </p>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <EmptyState text="这份记录里没有标签致敏原原文。" />
                )}

                {ingredientsDoc && ingredientsDoc.explicit_unsuitable_groups.length > 0 ? (
                  <>
                    <p className="consumer-subhead pt-8">包装上写明的不适宜人群</p>
                    <ul>
                      {ingredientsDoc.explicit_unsuitable_groups.map((item, index) => (
                        <li key={`${item.evidence_id}-${index}`} className="consumer-list-item">
                          <span className="t-14 text-[color:var(--ink)]">{item.raw_text}</span>
                          <SourceLine
                            className="m-desktop-only"
                            name={sourceLabel(item.source_id)}
                            date={shortDate(item.retrieved_at)}
                          />
                        </li>
                      ))}
                    </ul>
                  </>
                ) : null}
              </div>

              <div>
                <p className="consumer-subhead">营养成分表（原文数值）</p>
                {ingredientsDoc && ingredientsDoc.nutrition_facts.length > 0 ? (
                  <>
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>营养素</th>
                          <th className="text-right">原文数值</th>
                          {/* 手机：来源列整体挪到表下一处「来源 + 采集日期」，避免三列互挤。 */}
                          <th className="text-right m-desktop-only">来源</th>
                        </tr>
                      </thead>
                      <tbody>
                        {ingredientsDoc.nutrition_facts.map((item, index) => (
                          <tr key={`${item.evidence_id}-${index}`}>
                            <td>{item.nutrient}</td>
                            <td className="text-right mono t-14">{item.raw_value}</td>
                            <td className="text-right m-desktop-only">
                              <SourceLine
                                name={sourceLabel(item.source_id)}
                                date={shortDate(item.retrieved_at)}
                              />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                ) : (
                  <EmptyState text="这份记录里没有营养成分表。" />
                )}

                <p className="consumer-lede pt-6">
                  数值照包装原文抄录，方便你按自己的吃法换算。怎么理解这些数字，我们不替你判断。
                </p>
              </div>
            </div>

            {ingredientsRecords.length > 0 ? (
              <div className="consumer-footnote">
                <SourceNotes records={ingredientsRecords} labeler={sourceLabel} />
                <TraceDisclosure records={ingredientsRecords} label="看这一段的原文编号与内容摘要" />
              </div>
            ) : null}
          </DocSection>

          {/* ---------------------------------------------------------- 谁做的 */}
          <DocSection
            plain
            sectionKey="consumer-maker"
            title="谁做的"
            subtitle="从公开的许可与注册记录里取到的原文。没有这类记录时，我们就说没有，不猜。"
          >
            {makerRows.length > 0 ? (
              <div className="space-y-8">
                {makerRows.map((entry) => (
                  <div key={entry.group}>
                    <p className="consumer-subhead">{entry.group}</p>
                    <dl className="consumer-facts">
                      {entry.rows.map((row) => (
                        <div className="consumer-fact" key={row.label}>
                          <dt className="consumer-fact__label">{row.label}</dt>
                          <dd className="consumer-fact__value">
                            {row.values.map((item, index) => (
                              <span key={`${item.value}-${index}`}>
                                {index > 0 ? <span className="consumer-inline-sep">／</span> : null}
                                {item.value}
                                {row.values.length > 1 && item.sourceId ? (
                                  <span className="consumer-inline-source">
                                    <span aria-hidden="true">·</span>
                                    {sourceLabel(item.sourceId)}
                                  </span>
                                ) : null}
                              </span>
                            ))}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState text="接入的公开记录里没有找到生产许可或注册信息。这不代表这件商品没有许可，只是我们这次没取到。" />
            )}

            {makerRecords.length > 0 ? (
              <div className="consumer-footnote">
                <SourceNotes records={makerRecords} labeler={sourceLabel} />
                <TraceDisclosure records={makerRecords} label="看这一段的原文编号与内容摘要" />
              </div>
            ) : null}
          </DocSection>

          {/* ------------------------------------------------ 这次查了哪些来源 */}
          <DocSection
            plain
            sectionKey="consumer-coverage"
            title="这次查到了哪些来源"
            subtitle="每一处公开记录都有各自的状态：查到、没查到、还没拿到授权。逐条说清楚，缺的部分就是缺。"
          >
            {bundle?.origin === "DEMO_FALLBACK" ? (
              <div className="pb-4">
                <DemoMark scope="scan-03-coverage" />
              </div>
            ) : null}

            <ul className="consumer-source-list">
              {coverage.map((entry) => (
                <li key={entry.source_id} className="consumer-source-row" data-coverage-row={entry.source_id}>
                  <p className="t-14 text-[color:var(--ink)] leading-snug">{entry.display_name}</p>
                  <p className="t-13 text-[color:var(--ink-muted)] pt-0.5">
                    {COVERAGE_SENTENCE[entry.coverage_state] ?? entry.coverage_state}
                  </p>
                </li>
              ))}
            </ul>
            {coverage.length === 0 ? <EmptyState text="这份记录没有附带来源清单。" /> : null}

            {coverage.length > 0 ? (
              <details className="trace consumer-audit-fold">
                <summary className="trace__summary">
                  <span className="trace__glyph" aria-hidden="true">
                    <span className="trace__glyph-plus">+</span>
                    <span className="trace__glyph-minus">−</span>
                  </span>
                  <span className="trace__text">看每个来源的原始编号与访问方式 ({coverage.length})</span>
                </summary>
                <div className="trace__body">
                  {coverage.map((entry) => (
                    <div className="trace__record" key={entry.source_id}>
                      <p className="t-13 text-[color:var(--ink)]">{entry.display_name}</p>
                      <SourceLine name={entry.source_id} date={undefined} />
                      <p className="t-12 text-[color:var(--ink-muted)] pt-0.5">
                        访问方式：<span className="mono">{entry.access_mode}</span>
                      </p>
                      {entry.note ? (
                        <p className="t-12 text-[color:var(--ink-faint)] pt-0.5 leading-relaxed">{entry.note}</p>
                      ) : null}
                    </div>
                  ))}
                </div>
              </details>
            ) : null}
          </DocSection>

          {/* ------------------------------------------------- 还有别的说法吗 */}
          <DocSection
            plain
            sectionKey="consumer-candidates"
            title="还有别的说法吗"
            subtitle="同一个条码，几个公开来源各自有记录。写法不同时分别列出，不替你合并成一条。"
          >
            {bundle?.origin === "DEMO_FALLBACK" ? (
              <div className="pb-4">
                <DemoMark scope="scan-04-candidates" />
              </div>
            ) : null}

            <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 pb-3">
              <span className="consumer-hint">
                条码完全对得上的 {record.exact_match_count} 条 · 需要你自己确认的 {record.manual_confirm_count} 条
              </span>
            </div>

            <div>
              {record.match_candidates.map((candidate) => (
                <div
                  key={candidate.candidate_id}
                  className="consumer-candidate"
                  data-link-mode={candidate.link_mode}
                >
                  <p className="t-14 text-[color:var(--ink)]">
                    {candidate.display_name || "（这个来源没写商品名）"}
                  </p>
                  <p className="t-13 text-[color:var(--ink-muted)] pt-0.5 mono m-cand-id">
                    {IDENTIFIER_PLAIN[candidate.identifier_type] ?? candidate.identifier_type} ={" "}
                    {candidate.identifier_value}
                    {candidate.net_content ? ` · 净含量 ${candidate.net_content}` : ""}
                    {candidate.pack_type ? ` · ${candidate.pack_type}` : ""}
                  </p>
                  <p className="t-13 text-[color:var(--ink-soft)] pt-1">
                    {LINK_MODE_PLAIN[candidate.link_mode] ?? candidate.link_mode}
                  </p>
                  <SourceLine
                    className="m-desktop-only"
                    name={sourceLabel(candidate.source_id)}
                    date={shortDate(candidate.retrieved_at)}
                  />
                </div>
              ))}
            </div>

            <ConsumerSource
              records={candidateRecords}
              labeler={sourceLabel}
              note={`共 ${candidateRecords.length} 条，各自标注来源与关联方式，不自动合并`}
            />

            <div className="pt-6">
              <button
                type="button"
                onClick={() =>
                  onOpenDossier(record.gtin, record.entity_id, bundle?.origin ?? "STATIC_LIBRARY")
                }
                className="btn btn--text"
              >
                看原始记录与出处 →
              </button>
            </div>

            {candidateTraceRecords.length > 0 ? (
              <div className="consumer-footnote">
                <SourceNotes records={candidateTraceRecords} labeler={sourceLabel} />
                <TraceDisclosure records={candidateTraceRecords} label="看这一段的原文编号与内容摘要" />
              </div>
            ) : null}
          </DocSection>
        </>
      ) : null}

      {/* ------------------------------------------------------------ 拍照识别 */}
      <DocSection
        plain
        sectionKey="consumer-ocr"
        title="拍张包装照片"
        subtitle="照片不出这台设备：条码与配料文字都在本机识别，原图不落盘、不上传。识别结果你可以自己改。"
      >
        <div className="flex flex-wrap items-center gap-3">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            capture="environment"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void handleFile(file);
              event.target.value = "";
            }}
          />
          <button type="button" onClick={() => fileInputRef.current?.click()} className="btn btn--quiet">
            拍照 / 选一张包装图
          </button>
          {imagePreview ? (
            <button
              type="button"
              onClick={() => {
                URL.revokeObjectURL(imagePreview);
                setImagePreview(null);
                setOcrText("");
                setBarcodeText(null);
              }}
              className="btn btn--text"
            >
              清掉这张照片
            </button>
          ) : null}
        </div>

        {statusNote ? <p className="t-13 text-[color:var(--ink-muted)] pt-3">{statusNote}</p> : null}
        {barcodeText ? (
          <p className="t-13 text-[color:var(--ink-soft)] pt-2">
            照片里的条码：<span className="mono">{barcodeText}</span>
          </p>
        ) : null}

        {ocrBusy ? (
          <div className="pt-4">
            <div className="meter">
              <div
                className="meter__fill"
                style={{ width: `${Math.round((ocrProgress?.progress ?? 0) * 100)}%` }}
              />
            </div>
            <p className="t-12 text-[color:var(--ink-muted)] pt-1.5">
              {ocrProgress ? ocrProgressText(ocrProgress) : "准备本机识别…"}
            </p>
          </div>
        ) : null}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-12 gap-y-8 pt-6">
          <div>
            <p className="consumer-subhead">照片预览（只在内存里）</p>
            {imagePreview ? (
              <img
                src={imagePreview}
                alt="包装预览"
                className="w-full max-h-72 object-contain border border-[color:var(--rule)] bg-[color:var(--paper-raised)]"
              />
            ) : (
              <EmptyState text="还没有选照片。原图只用于这一次识别，刷新页面就没了。" />
            )}
          </div>
          <div>
            <p className="consumer-subhead">识别出的配料文字 · 可以直接改</p>
            <textarea
              value={ocrText}
              onChange={(event) => setOcrText(event.target.value)}
              rows={7}
              placeholder="识别结果会出现在这里；也可以直接把包装上的配料表抄进来"
              className="w-full t-13 leading-relaxed p-3 border border-[color:var(--rule-strong)] bg-[color:var(--paper-raised)] focus:outline-none focus:border-[color:var(--teal)] resize-y"
            />
            {ocrConfidence !== null ? (
              <p className="t-12 text-[color:var(--ink-faint)] pt-1">识别把握度 {ocrConfidence.toFixed(1)}</p>
            ) : null}
            {ingredientList.length > 0 ? (
              <ol className="pt-2 space-y-0.5">
                {ingredientList.map((item, index) => (
                  <li key={`${item}-${index}`} className="t-12 text-[color:var(--ink-soft)] flex gap-2">
                    <span className="mono text-[color:var(--ink-faint)] w-5 shrink-0 text-right">
                      {index + 1}
                    </span>
                    <span>{item}</span>
                  </li>
                ))}
              </ol>
            ) : null}
          </div>
        </div>
      </DocSection>

      {/* ------------------------------------------------------ 忌口字面对照 */}
      <DocSection
        plain
        sectionKey="consumer-compare"
        title="按包装文字对照忌口"
        subtitle={
          rules.length === 0
            ? "你还没添加忌口词条。到「我的忌口与饮食计划」里加一个，或在下面直接加。"
            : `你写了 ${rules.length} 个忌口词。这里只做字面比对：你的词出现在包装文字里，就把那一项列出来。`
        }
      >
        <QuickRuleAdder
          onAdded={() => {
            void (async () => setRules(await listRules()))();
          }}
          showToast={showToast}
        />

        {/* 顶部：明确当前对照的是哪一份来源，并支持一键切换（并列，不合并） */}
        <div className="compare-head" data-compare-focus={compareFocus}>
          <SourceFocusSwitch
            legend="当前对照来源"
            name="compare-source-focus"
            value={compareFocus}
            options={[
              {
                value: "static_archive",
                label: "条码查到的配料",
                count: staticHitCount,
              },
              {
                value: "ocr_text",
                label: "照片识别 / 手抄的文字",
                count: ocrHits.length,
              },
            ]}
            onChange={(next) => setCompareFocus(next as CompareFocus)}
          />
          <p className="consumer-lede pt-2 max-w-[64ch]">
            现在看的是
            <span className="consumer-emphasis">
              {compareFocus === "static_archive" ? "条码查到的配料" : "照片识别 / 手抄的文字"}
            </span>
            。两份内容始终各自列着、各自标来源，不会拼成一个列表；切换只是换个角度看。
          </p>
          {compareFocus === "static_archive" && !ingredientsDoc ? (
            <p className="consumer-lede pt-1 max-w-[64ch]">
              还没有条码查到的配料：先在上面输入条码，或切到「照片识别 / 手抄的文字」。
            </p>
          ) : null}
          {compareFocus === "ocr_text" && !ocrText.trim() ? (
            <p className="consumer-lede pt-1 max-w-[64ch]">
              这一侧还是空的：在上面拍一张包装照，或直接把配料文字抄进来。
            </p>
          ) : null}
        </div>

        <div className="compare-grid pt-6">
          {/* 来源 A：条码查到的配料 */}
          <div
            className={`compare-pane${compareFocus === "static_archive" ? " is-focus" : ""}`}
            data-compare-pane="static_archive"
          >
            <div className="compare-pane__head">
              <p className="t-13 text-[color:var(--ink)] font-semibold">条码查到的配料</p>
              <span className="t-12 text-[color:var(--ink-muted)]">
                这次共 {staticHitCount} 项与你的词对上
              </span>
            </div>
            {bundle?.origin === "DEMO_FALLBACK" ? (
              <div className="pt-2">
                <DemoMark scope="scan-06-static" />
              </div>
            ) : null}

            {ingredientHits && ingredientHits.ingredientHits.length > 0 ? (
              <div className="pt-4">
                <p className="consumer-subhead">配料里对上的</p>
                {ingredientHits.ingredientHits.map((hit, index) => (
                  <div
                    key={`${hit.evidenceId}-${hit.order}-${index}`}
                    className="consumer-list-item"
                    data-hit-source="static_archive"
                  >
                    <p className="t-14 text-[color:var(--ink)]">{formatHitNotice(hit)}</p>
                    <SourceLine
                      className="m-desktop-only"
                      name={sourceLabel(hit.sourceId)}
                      date={shortDate(hitRetrievedAt(hit.evidenceId))}
                    />
                  </div>
                ))}
              </div>
            ) : null}

            {ingredientHits && ingredientHits.allergenHits.length > 0 ? (
              <div className="pt-6">
                <p className="consumer-subhead">包装写明的致敏原里对上的</p>
                {ingredientHits.allergenHits.map((hit, index) => (
                  <div
                    key={`${hit.evidenceId}-${index}`}
                    className="consumer-list-item"
                    data-hit-source="static_archive"
                  >
                    <p className="t-14 text-[color:var(--ink)]">
                      包装原文「{hit.rawText}」里有你写的「{hit.matchedTerm}」
                    </p>
                    <SourceLine
                      className="m-desktop-only"
                      name={sourceLabel(hit.sourceId)}
                      date={shortDate(hitRetrievedAt(hit.evidenceId))}
                    />
                  </div>
                ))}
              </div>
            ) : null}

            {!ingredientsDoc ? (
              <div className="pt-3">
                <EmptyState text="还没有条码查到的配料：先输入条码，这里才会出现字面对上的项。" />
              </div>
            ) : staticHitCount === 0 ? (
              <div className="pt-3">
                <EmptyState
                  text={
                    rules.length === 0
                      ? "加上忌口词之后，这里会列出包装配料里对上的项。"
                      : "条码查到的配料里没有出现你的词。这只说明没出现，别的什么也不能说明。"
                  }
                />
              </div>
            ) : null}
          </div>

          {/* 来源 B：照片识别 / 手抄文字 */}
          <div
            className={`compare-pane${compareFocus === "ocr_text" ? " is-focus" : ""}`}
            data-compare-pane="ocr_text"
          >
            <div className="compare-pane__head">
              <p className="t-13 text-[color:var(--ink)] font-semibold">照片识别 / 手抄的文字</p>
              <span className="t-12 text-[color:var(--ink-muted)]">
                只在这台设备上 · 共 {ocrHits.length} 项对上
              </span>
            </div>
            <div className="pt-2">
              <div className="demo-mark demo-mark--inline" data-demo-scope="scan-06-ocr" role="note">
                <span className="demo-mark__label">本机文字</span>
                <p className="demo-mark__note">
                  这一侧是这次拍照识别或你手抄的文字，不是从网上取来的商品库记录；原图和文字都留在这台设备上。
                </p>
              </div>
            </div>

            {ocrHits.length > 0 ? (
              <div className="pt-4">
                <p className="consumer-subhead">文字行里对上的</p>
                {ocrHits.map((hit, index) => (
                  <div
                    key={index}
                    className="consumer-list-item"
                    data-hit-source="ocr_text"
                  >
                    <p className="t-14 text-[color:var(--ink)]">
                      照片文字第 {hit.lineIndex} 行「{hit.line}」里有你写的「{hit.matchedTerm}」
                    </p>
                  </div>
                ))}
              </div>
            ) : !ocrText.trim() ? (
              <div className="pt-3">
                <EmptyState text="还没有拍照或抄入文字：上面识别之后，这里会出现字面对上的项。" />
              </div>
            ) : rules.length === 0 ? (
              <div className="pt-3">
                <EmptyState text="加上忌口词之后，这里会列出照片文字里对上的项。" />
              </div>
            ) : (
              <div className="pt-3">
                <EmptyState text="照片文字里没有出现你的词。这只说明没出现，别的什么也不能说明。" />
              </div>
            )}
          </div>
        </div>

        <p className="consumer-lede pt-6 max-w-[64ch]">
          这里只做字面比对：你的词出现在包装文字里，就列出来；没出现就写没出现。
          它不判断这件商品能不能吃，也不构成医疗、营养或购买意见——吃什么、怎么吃，你自己定。
          两份内容分别对照，不合并。
        </p>

        {hitRecords.length > 0 ? (
          <div className="consumer-footnote">
            <SourceNotes records={hitRecords} labeler={sourceLabel} />
            <TraceDisclosure records={hitRecords} label="看这一段对上的原文编号与内容摘要" />
          </div>
        ) : null}
      </DocSection>
    </div>
  );
}

function QuickRuleAdder({ onAdded, showToast }: { onAdded: () => void; showToast: (text: string) => void }) {
  const [value, setValue] = useState("");
  const add = async () => {
    const term = value.trim();
    if (!term) return;
    const { addRule } = await import("@/local_store/db");
    await addRule(term, []);
    setValue("");
    onAdded();
    showToast(`已加入本地词条「${term}」（只在这台设备上）`);
  };
  return (
    <div className="flex flex-col sm:flex-row gap-3 sm:items-end pb-6">
      <label className="flex-1 min-w-0">
        <span className="consumer-input-label">现在加一个忌口词</span>
        <input
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void add();
          }}
          placeholder="例如：乳糖"
          className="field-input"
        />
      </label>
      <button type="button" onClick={() => void add()} className="btn btn--quiet shrink-0">
        加入本地词条
      </button>
    </div>
  );
}

/** 供测试断言使用的归一化导出（不参与渲染）。 */
export const __normalizeText = normalizeText;
