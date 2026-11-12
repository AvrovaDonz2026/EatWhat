import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  buildSourceLabeler,
  ConsumerSource,
  DemoMark,
  DocSection,
  EmptyState,
  MarginNote,
  shortDate,
  SourceFocusSwitch,
  SourceLine,
  SourceNotes,
  StatusTag,
  TraceDisclosure,
} from "@/components/ui";
import { formatHitNotice, matchIngredients, matchOcrText, normalizeText } from "@/engine/local_diet_matcher";
import { addScanHistory, listRules, type DietRule } from "@/local_store/db";
import {
  buildHashIndex,
  coverageLabel,
  demoGtins,
  loadDossier,
  lookupGtin,
  withHashes,
  type DossierBundle,
  type GtinLookup,
} from "@/services/static_dossier_client";
import { detectBarcode } from "@/ocr/barcode";
import { extractIngredientLines, recognizeLabel, type OcrProgress } from "@/ocr/local_ocr";
import type { IngredientsDocument } from "@/types";

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

export type CompareFocus = "static_archive" | "ocr_text";

export function ScanPage({ onOpenDossier, showToast, onDataChanged }: ScanPageProps) {
  const [query, setQuery] = useState("");
  const [bundle, setBundle] = useState<DossierBundle | null>(null);
  const [lookup, setLookup] = useState<GtinLookup | null>(null);
  const [noMatchGtin, setNoMatchGtin] = useState<string | null>(null);
  const [rules, setRules] = useState<DietRule[]>([]);
  const [busy, setBusy] = useState(false);
  const [statusNote, setStatusNote] = useState<string | null>(null);
  /** 06 区块的主视图焦点：两份来源始终并列，切换只改变焦点。 */
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
        showToast("请输入条码或粘贴配料文字");
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
      setStatusNote("图片仅在浏览器内存中处理，不上传、不落盘");

      // 1) 端侧条码识别
      try {
        const barcode = await detectBarcode(file);
        if (barcode) {
          setBarcodeText(barcode.text);
          setQuery(barcode.text);
          showToast(`端侧识别到条码 ${barcode.text}（${barcode.format}）`);
        } else {
          setStatusNote("未从图片中识别到条码；仍可继续端侧 OCR 提取配料表");
        }
      } catch (error) {
        console.warn("条码识别失败", error);
        setStatusNote("条码识别不可用；可继续端侧 OCR 或手动输入");
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
        showToast("端侧 OCR 未完成，可手动粘贴配料文字");
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

  /** 条码静态档案侧的机械命中条数（配料 + 标签致敏原），与 OCR 侧分别计数。 */
  const staticHitCount =
    (ingredientHits?.ingredientHits.length ?? 0) + (ingredientHits?.allergenHits.length ?? 0);

  const record = bundle?.record ?? lookup?.record ?? null;
  const coverage = record?.coverage_manifest ?? [];
  const ingredientsDoc = bundle?.ingredients ?? null;
  const sourceCount = ingredientsDoc
    ? new Set(ingredientsDoc.ingredients.map((item) => item.source_id)).size
    : 0;

  /** source_id → 可读名称，一律取自载荷里的 coverage_manifest.display_name。 */
  const sourceLabel = useMemo(
    () => buildSourceLabeler(coverage.length > 0 ? coverage : bundle?.product?.coverage_manifest ?? []),
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


  return (
    <div className="space-y-10">
      {/* 01 — 检索 */}
      <DocSection
        index="01"
        title="按条码检索公开档案"
        subtitle="输入条码或使用内置演示样例。未命中时只会显示「当前已接入静态档案库未匹配到记录」，不代表任何结论。"
        right={<StatusTag tone="none">只读静态 JSON · 零 Cookie</StatusTag>}
        marginalia={
          <>
            <MarginNote label="检索方式" value="同站静态分片 GET /data/v1/gtin/<code>{gtin}.json</code>" />
            <MarginNote label="未命中语义" value="QUERIED_NO_MATCH —— 仅表示本静态库现有分片无该条码" />
            <MarginNote
              label="演示样例"
              value="夹具为合成演示数据，仅用于展示文档结构"
            />
          </>
        }
      >
        <div className="flex flex-col sm:flex-row gap-3">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void runLookup(query);
            }}
            placeholder="输入条码（如 6901234567892）"
            inputMode="numeric"
            aria-label="条码"
            className="field-input field-input--mono flex-1"
          />
          <button
            type="button"
            disabled={busy}
            onClick={() => void runLookup(query)}
            className="btn btn--primary shrink-0"
          >
            {busy ? "检索中…" : "检索公开档案"}
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 pt-4">
          <span className="t-12 label-track text-[color:var(--ink-faint)]">演示样例</span>
          {demos.map((item) => (
            <button
              key={item.gtin}
              type="button"
              onClick={() => {
                setQuery(item.gtin);
                void runLookup(item.gtin);
              }}
              className="mono px-2 py-1 border border-[color:var(--rule)] text-[color:var(--ink-soft)] hover:border-[color:var(--teal)] hover:text-[color:var(--teal-deep)] transition-colors"
              data-demo-gtin={item.gtin}
              data-demo-origin={item.origin}
            >
              {item.label}
            </button>
          ))}
        </div>

        {/* 演示样例来源属性：由条目自带的 origin 字段驱动，不在这里硬编码条码 */}
        {demos.some((item) => item.origin === "DEMO_FALLBACK") ? (
          <div className="pt-3">
            <DemoMark scope="scan-demo-list" />
          </div>
        ) : null}
      </DocSection>

      {/* 02 — 端侧识别 */}
      <DocSection
        index="02"
        title="包装拍照 · 端侧条码与 OCR"
        subtitle="图片不出浏览器：条码识别与配料文字识别均在本地 WASM 内执行，原图不落盘、不上传。"
        right={<StatusTag tone="filled">Local-first</StatusTag>}
        marginalia={
          <>
            <MarginNote label="条码内核" value="zxing-wasm · 随单文件内联" />
            <MarginNote label="OCR 内核" value="tesseract.js LSTM · 语言数据内联" />
            <MarginNote label="生命周期" value="原图仅存于内存；刷新即消失，不写入磁盘" />
            {barcodeText ? <MarginNote label="识别条码" value={<span className="mono">{barcodeText}</span>} /> : null}
            {ocrConfidence !== null ? (
              <MarginNote label="OCR 置信度" value={<span className="mono">{ocrConfidence.toFixed(1)}</span>} />
            ) : null}
          </>
        }
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
            拍照 / 选择包装图片
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
              清除本地图片
            </button>
          ) : null}
        </div>

        {statusNote ? <p className="t-12 text-[color:var(--ink-muted)] pt-2">{statusNote}</p> : null}

        {ocrBusy ? (
          <div className="pt-3">
            <div className="meter">
              <div
                className="meter__fill"
                style={{ width: `${Math.round((ocrProgress?.progress ?? 0) * 100)}%` }}
              />
            </div>
            <p className="t-12 text-[color:var(--ink-muted)] pt-1.5">
              {ocrProgress ? ocrProgressText(ocrProgress) : "准备端侧识别…"}
            </p>
          </div>
        ) : null}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pt-5">
          <div>
            <p className="t-12 label-track text-[color:var(--ink-faint)] pb-2">
              本地预览 · 内存中
            </p>
            {imagePreview ? (
              <img
                src={imagePreview}
                alt="包装预览"
                className="w-full max-h-72 object-contain border border-[color:var(--rule)] bg-[color:var(--paper-raised)]"
              />
            ) : (
              <EmptyState text="尚未选择图片。原图仅用于本次识别，刷新页面即消失。" />
            )}
          </div>
          <div>
            <p className="t-12 label-track text-[color:var(--ink-faint)] pb-2">
              端侧 OCR 配料文字 · 可手动修正
            </p>
            <textarea
              value={ocrText}
              onChange={(event) => setOcrText(event.target.value)}
              rows={7}
              placeholder="识别结果会出现在这里；也可以直接粘贴包装上的配料表文字"
              className="w-full t-13 leading-relaxed p-3 border border-[color:var(--rule-strong)] bg-[color:var(--paper-raised)] focus:outline-none focus:border-[color:var(--teal)] resize-y"
            />
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

      {noMatchGtin ? (
        <DocSection index="03" title="检索结果" subtitle={`条码 ${noMatchGtin}`}>
          <div className="border border-dashed border-[color:var(--rule-strong)] px-4 py-3">
            <p className="mono !text-[12px] text-[color:var(--ink-soft)]">QUERIED_NO_MATCH</p>
            <p className="t-13 text-[color:var(--ink-soft)] pt-1.5 leading-relaxed">
              当前已接入静态档案库未匹配到记录。这不代表任何结论，仅表示本静态库现有分片中没有该条码的记录。
            </p>
            <p className="t-12 text-[color:var(--ink-muted)] pt-2 leading-relaxed">
              静态库覆盖范围可在「16 区块档案与来源」页查看；未接入或不可用的数据源会在覆盖清单中如实披露。
            </p>
          </div>
        </DocSection>
      ) : null}

      {record ? (
        <DocSection
          index="03"
          title="数据源覆盖清单"
          subtitle={
            bundle?.origin === "DEMO_FALLBACK"
              ? "当前为内置离线样例（未检测到静态档案库），结构与正式产物一致。"
              : "逐源披露本次查询状态；未接入与不可用的来源均如实列出。"
          }
          right={<StatusTag tone="filled">{record.query_status}</StatusTag>}
          marginalia={
            <>
              <MarginNote label="来源数" value={`${coverage.length} 个已注册来源`} />
              <MarginNote label="记录条数" value={`精确命中 ${record.exact_match_count} · 需手动确认 ${record.manual_confirm_count}`} />
              {coverage.length > 0 ? (
                <div>
                  <p className="t-12 label-track text-[color:var(--ink-faint)]">本次取得日期</p>
                  <SourceNotes
                    records={candidateTraceRecords}
                    labeler={sourceLabel}
                  />
                </div>
              ) : null}
            </>
          }
        >
          {bundle?.origin === "DEMO_FALLBACK" ? (
            <div className="pb-4">
              <DemoMark scope="scan-03-coverage" />
            </div>
          ) : null}
          {/* 覆盖清单：手机改成「每个来源一块上下分段」，而不是三列被压窄。
              字段与覆盖状态一字不改，只是不再是三列表格。 */}
          <div className="m-only">
            {coverage.map((entry) => (
              <div
                key={entry.source_id}
                className="py-3 border-b border-dotted border-[color:var(--rule-hair)]"
              >
                <p className="t-14 text-[color:var(--ink)] leading-snug">{entry.display_name}</p>
                {entry.authority ? (
                  <p className="t-12 text-[color:var(--ink-faint)] pt-0.5">{entry.authority}</p>
                ) : null}
                <p className="t-12 text-[color:var(--ink-soft)] pt-1.5">
                  访问形态：<span className="mono">{entry.access_mode}</span>
                </p>
                <p className="t-12 text-[color:var(--ink-soft)] pt-0.5">
                  本次状态：{coverageLabel(entry.coverage_state)}
                </p>
                {entry.note ? (
                  <p className="t-12 text-[color:var(--ink-faint)] pt-0.5 leading-relaxed">{entry.note}</p>
                ) : null}
                <p className="mono text-[color:var(--ink-faint)] pt-0.5">{entry.source_id}</p>
              </div>
            ))}
          </div>

          <table className="data-table m-desktop-only">
            <thead>
              <tr>
                <th>数据源</th>
                <th>访问形态</th>
                <th>本次状态</th>
              </tr>
            </thead>
            <tbody>
              {coverage.map((entry) => (
                <tr key={entry.source_id}>
                  <td>
                    <span className="t-13 text-[color:var(--ink)]">{entry.display_name}</span>
                    {entry.authority ? (
                      <span className="block t-12 text-[color:var(--ink-faint)]">{entry.authority}</span>
                    ) : null}
                    <span className="block mono text-[color:var(--ink-faint)]">{entry.source_id}</span>
                  </td>
                  <td className="mono text-[color:var(--ink-muted)]">{entry.access_mode}</td>
                  <td>
                    <span className="t-12 text-[color:var(--ink-soft)]">{coverageLabel(entry.coverage_state)}</span>
                    {entry.note ? (
                      <span className="block t-12 text-[color:var(--ink-faint)]">{entry.note}</span>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </DocSection>
      ) : null}

      {record ? (
        <DocSection
          index="04"
          title="条码候选 · 不自动合并"
          subtitle={record.no_auto_merge_notice}
          right={
            <div className="flex flex-wrap gap-2">
              <StatusTag tone="filled">精确命中 {record.exact_match_count}</StatusTag>
              <StatusTag tone="pending">需手动确认 {record.manual_confirm_count}</StatusTag>
            </div>
          }
          marginalia={
            <>
              <MarginNote label="合并策略" value="候选并列展示，任何合并均需人工确认" />
              <MarginNote label="候选条数" value={`${record.match_candidates.length} 条`} />
              {candidateTraceRecords.length > 0 ? (
                <div>
                  <p className="t-12 label-track text-[color:var(--ink-faint)]">来源</p>
                  <SourceNotes records={candidateTraceRecords} labeler={sourceLabel} />
                </div>
              ) : null}
            </>
          }
          trace={
            candidateTraceRecords.length > 0 ? (
              <TraceDisclosure records={candidateTraceRecords} label="查看来源与证据" />
            ) : null
          }
        >
          {bundle?.origin === "DEMO_FALLBACK" ? (
            <div className="pb-4">
              <DemoMark scope="scan-04-candidates" />
            </div>
          ) : null}
          <div>
            {record.match_candidates.map((candidate) => (
              <div
                key={candidate.candidate_id}
                className="py-2.5 border-b border-dotted border-[color:var(--rule-hair)] first:pt-0"
              >
                <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1 m-cand-row">
                  <div className="min-w-0 flex-1 m-cand-body">
                    <p className="t-14 text-[color:var(--ink)]">
                      {candidate.display_name || "（该来源未提供商品名）"}
                    </p>
                    <p className="t-13 text-[color:var(--ink-muted)] pt-0.5 mono m-cand-id">
                      {candidate.identifier_type} = {candidate.identifier_value}
                      {candidate.net_content ? ` · 净含量 ${candidate.net_content}` : ""}
                      {candidate.pack_type ? ` · ${candidate.pack_type}` : ""}
                    </p>
                    <p className="t-13 text-[color:var(--ink-faint)] pt-0.5">{candidate.link_reason}</p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0 m-cand-tag">
                    <StatusTag tone={candidate.link_mode === "EXACT_IDENTIFIER_MATCH" ? "filled" : "pending"}>
                      {candidate.link_mode}
                    </StatusTag>
                  </div>
                </div>
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
            note={`候选 ${candidateRecords.length} 条，各自标注来源与关联方式，不自动合并`}
          />
          <div className="pt-3">
            <button
              type="button"
              onClick={() =>
                onOpenDossier(record.gtin, record.entity_id, bundle?.origin ?? "STATIC_LIBRARY")
              }
              className="btn btn--text"
            >
              查看 16 区块档案与来源追溯 →
            </button>
          </div>
        </DocSection>
      ) : null}

      {ingredientsDoc ? (
        <DocSection
          index="05"
          title="配料与营养原文"
          subtitle={`来自 ${sourceCount} 个来源的原始记录，逐项绑定证据；数值仅按原文呈现，不做换算之外的加工。`}
          marginalia={
            <>
              <MarginNote label="配料条数" value={`${ingredientsDoc.ingredients.length} 项`} />
              <MarginNote label="致敏原原文" value={`${ingredientsDoc.explicit_allergens.length} 条`} />
              <MarginNote label="营养项" value={`${ingredientsDoc.nutrition_facts.length} 条`} />
              {ingredientsRecords.length > 0 ? (
                <div>
                  <p className="t-12 label-track text-[color:var(--ink-faint)]">来源与取得</p>
                  <SourceNotes records={ingredientsRecords} labeler={sourceLabel} />
                </div>
              ) : null}
            </>
          }
          trace={
            ingredientsRecords.length > 0 ? (
              <TraceDisclosure records={ingredientsRecords} label="查看来源与证据" />
            ) : null
          }
        >
          {bundle?.origin === "DEMO_FALLBACK" ? (
            <div className="pb-4">
              <DemoMark scope="scan-05-ingredients" />
            </div>
          ) : null}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8 m-stack">
            <div>
              <p className="t-12 label-track text-[color:var(--ink-faint)] pb-1 m-subhead">
                配料表原文顺序
              </p>
              {ingredientsDoc.ingredients.length === 0 ? (
                <EmptyState text="该档案未包含配料表原文记录。" />
              ) : (
                <ol>
                  {ingredientsDoc.ingredients.map((item, index) => (
                    <li
                      key={`${item.evidence_id}-${item.order}-${index}`}
                      className="py-1.5 border-b border-dotted border-[color:var(--rule-hair)]"
                    >
                      <span className="t-14 text-[color:var(--ink)] flex gap-2 min-w-0">
                        <span className="mono text-[color:var(--ink-faint)] shrink-0 w-5 text-right">
                          {item.order}
                        </span>
                        {/* 术语列禁止收缩：CJK 的 min-content 只有 1 个字宽，
                          * 一旦作为可收缩 flex 子项就会在窄容器里被压成逐字竖排。 */}
                        <span className="term-cell">{item.raw_name}</span>
                      </span>
                      {/* 手机上不逐条重复来源：本组末尾统一给一次「来源 + 采集日期」。 */}
                      <SourceLine
                        className="m-desktop-only"
                        name={sourceLabel(item.source_id)}
                        date={shortDate(item.retrieved_at)}
                      />
                    </li>
                  ))}
                </ol>
              )}
              <ConsumerSource
                records={ingredientsDoc.ingredients}
                labeler={sourceLabel}
                note={`配料 ${ingredientsDoc.ingredients.length} 项，按标签原文顺序`}
              />

              <p className="t-12 label-track text-[color:var(--ink-faint)] pt-6 pb-1 m-subhead">
                标签明确书写的致敏原原文
              </p>
              {ingredientsDoc.explicit_allergens.length === 0 ? (
                <EmptyState text="该档案未包含标签致敏原原文记录。" />
              ) : (
                <ul>
                  {ingredientsDoc.explicit_allergens.map((item, index) => (
                    <li
                      key={`${item.evidence_id}-${index}`}
                      className="py-1.5 border-b border-dotted border-[color:var(--rule-hair)]"
                    >
                      <span className="t-14 text-[color:var(--ink)]">{item.raw_text}</span>
                      <SourceLine
                        className="m-desktop-only"
                        name={sourceLabel(item.source_id)}
                        date={shortDate(item.retrieved_at)}
                      />
                      {item.standard_ref ? (
                        <p className="t-12 text-[color:var(--ink-faint)] pt-0.5">
                          标签标准引用：{item.standard_ref}
                        </p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
              <ConsumerSource
                records={ingredientsDoc.explicit_allergens}
                labeler={sourceLabel}
                note={`标签致敏原 ${ingredientsDoc.explicit_allergens.length} 条`}
              />

              {ingredientsDoc.explicit_unsuitable_groups.length > 0 ? (
                <>
                  <p className="t-12 label-track text-[color:var(--ink-faint)] pt-6 pb-1 m-subhead">
                    标签标注的不适宜人群原文
                  </p>
                  <ul>
                    {ingredientsDoc.explicit_unsuitable_groups.map((item, index) => (
                      <li
                        key={`${item.evidence_id}-${index}`}
                        className="py-1.5 border-b border-dotted border-[color:var(--rule-hair)]"
                      >
                        <span className="t-14 text-[color:var(--ink)]">{item.raw_text}</span>
                        <SourceLine
                          className="m-desktop-only"
                          name={sourceLabel(item.source_id)}
                          date={shortDate(item.retrieved_at)}
                        />
                      </li>
                    ))}
                  </ul>
                  <ConsumerSource
                    records={ingredientsDoc.explicit_unsuitable_groups}
                    labeler={sourceLabel}
                    note={`不适宜人群原文 ${ingredientsDoc.explicit_unsuitable_groups.length} 条`}
                  />
                </>
              ) : null}
            </div>

            <div>
              <p className="t-12 label-track text-[color:var(--ink-faint)] pb-1 m-subhead">
                营养成分表原文
              </p>
              {ingredientsDoc.nutrition_facts.length === 0 ? (
                <EmptyState text="该档案未包含营养成分表记录。" />
              ) : (
                <>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>营养素</th>
                        <th className="text-right">原文数值</th>
                        {/* 手机：来源列整体挪到表下一处「来源 + 采集日期」，
                            避免 390px 下三列互挤（表格只留「营养素 / 原文数值」两列）。 */}
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
                  <ConsumerSource
                    records={ingredientsDoc.nutrition_facts}
                    labeler={sourceLabel}
                    note={`营养项 ${ingredientsDoc.nutrition_facts.length} 条，数值按原文呈现`}
                  />
                </>
              )}

              <p className="mt-4 t-12 text-[color:var(--ink-muted)] leading-relaxed border-l-2 border-[color:var(--ochre)] pl-3">
                {ingredientsDoc.disclaimer}
              </p>
            </div>
          </div>
        </DocSection>
      ) : null}

      <DocSection
        index="06"
        title="本地忌口词条机械对照"
        subtitle={
          rules.length === 0
            ? "尚未添加本地忌口词条；可在「我的忌口与饮食计划」页添加，或在下方即时添加。"
            : `当前本地词条 ${rules.length} 条；对照为归一化后的纯子串 / 别名机械匹配。`
        }
        right={<StatusTag tone="none">零推断 · 纯字面</StatusTag>}
        marginalia={
          <>
            <MarginNote label="词条存储" value="仅本浏览器 IndexedDB · diet_rules" />
            <MarginNote label="归一化" value="NFKC · 去空白 · 小写" />
            <MarginNote label="命中条数" value={`${staticHitCount} 条（条码静态档案）+ ${ocrHits.length} 条（OCR 文本）`} />
            <MarginNote label="两份来源" value="并列呈现，各自标注来源；切换只改变主视图焦点，不隐藏数据" />
            {hitRecords.length > 0 ? (
              <div>
                <p className="t-12 label-track text-[color:var(--ink-faint)]">来源与取得</p>
                <SourceNotes records={hitRecords} labeler={sourceLabel} />
              </div>
            ) : null}
          </>
        }
        trace={hitRecords.length > 0 ? <TraceDisclosure records={hitRecords} label="查看来源与证据" /> : null}
      >
        <QuickRuleAdder
          onAdded={() => {
            void (async () => setRules(await listRules()))();
          }}
          showToast={showToast}
        />

        {/* 06 顶部：明确当前对照的是哪一份来源，并支持一键切换（并列，不合并） */}
        <div className="compare-head" data-compare-focus={compareFocus}>
          <SourceFocusSwitch
            legend="当前对照来源"
            name="compare-source-focus"
            value={compareFocus}
            options={[
              {
                value: "static_archive",
                label: "条码静态档案配料",
                count: staticHitCount,
              },
              {
                value: "ocr_text",
                label: "OCR / 手动修正文本",
                count: ocrHits.length,
              },
            ]}
            onChange={(next) => setCompareFocus(next as CompareFocus)}
          />
          <p className="t-12 text-[color:var(--ink-muted)] pt-2 leading-relaxed max-w-[68ch]">
            当前主视图焦点：
            <span className="t-13 text-[color:var(--teal-deep)] font-semibold pl-1">
              {compareFocus === "static_archive" ? "条码静态档案配料" : "OCR / 手动修正文本"}
            </span>
            。两份来源始终并列列出、各自标注来源，<span className="italic">不会拼成一个合并列表</span>；
            切换只改变阅读焦点，不隐藏任何一方的数据。
          </p>
          {compareFocus === "static_archive" && !ingredientsDoc ? (
            <p className="t-12 text-[color:var(--ink-muted)] pt-1 leading-relaxed max-w-[68ch]">
              当前尚无条码静态档案配料：先在上方检索条码，或切换到 OCR / 手动修正文本。
            </p>
          ) : null}
          {compareFocus === "ocr_text" && !ocrText.trim() ? (
            <p className="t-12 text-[color:var(--ink-muted)] pt-1 leading-relaxed max-w-[68ch]">
              当前 OCR / 手动修正文本为空：可在 02 区块拍照识别或直接粘贴配料文字。
            </p>
          ) : null}
        </div>

        <div className="compare-grid pt-5">
          {/* 来源 A：条码静态档案（bundle.ingredients） */}
          <div
            className={`compare-pane${compareFocus === "static_archive" ? " is-focus" : ""}`}
            data-compare-pane="static_archive"
          >
            <div className="compare-pane__head">
              <span className="t-12 label-track text-[color:var(--ink-faint)]">来源 A</span>
              <p className="t-13 text-[color:var(--ink)] font-semibold">条码静态档案配料</p>
              <span className="t-12 text-[color:var(--ink-muted)]">
                来自静态分片 <span className="mono">v1/ingredients/{`{entity_id}`}.json</span> / 命中 {staticHitCount} 条
              </span>
            </div>
            {bundle?.origin === "DEMO_FALLBACK" ? (
              <div className="pt-2">
                <DemoMark scope="scan-06-static" />
              </div>
            ) : null}

            {ingredientHits && ingredientHits.ingredientHits.length > 0 ? (
              <div className="pt-3">
                <p className="t-12 label-track text-[color:var(--ink-faint)] pb-2">配料表命中</p>
                {ingredientHits.ingredientHits.map((hit, index) => (
                  <div
                    key={`${hit.evidenceId}-${hit.order}-${index}`}
                    className="py-2 border-b border-dotted border-[color:var(--rule-hair)]"
                    data-hit-source="static_archive"
                  >
                    <p className="t-14 text-[color:var(--ink)]">{formatHitNotice(hit)}</p>
                    {/* 手机：同一栏内所有命中共享同一来源，逐条重复没有信息量。 */}
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
              <div className="pt-4">
                <p className="t-12 label-track text-[color:var(--ink-faint)] pb-2">标签致敏原命中</p>
                {ingredientHits.allergenHits.map((hit, index) => (
                  <div
                    key={`${hit.evidenceId}-${index}`}
                    className="py-2 border-b border-dotted border-[color:var(--rule-hair)]"
                    data-hit-source="static_archive"
                  >
                    <p className="t-14 text-[color:var(--ink)]">
                      标签致敏原原文「{hit.rawText}」命中您的本地忌口词条「{hit.matchedTerm}」
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
                <EmptyState text="尚未取得条码静态档案配料：先检索条码，此处才会出现该来源的机械命中。" />
              </div>
            ) : staticHitCount === 0 ? (
              <div className="pt-3">
                <EmptyState
                  text={
                    rules.length === 0
                      ? "添加本地忌口词条后，此处会显示条码静态档案配料中的机械命中项。"
                      : "条码静态档案配料中未出现命中项；这仅表示词条未出现，不代表任何其他结论。"
                  }
                />
              </div>
            ) : null}
          </div>

          {/* 来源 B：端侧 OCR / 手动修正文本 */}
          <div
            className={`compare-pane${compareFocus === "ocr_text" ? " is-focus" : ""}`}
            data-compare-pane="ocr_text"
          >
            <div className="compare-pane__head">
              <span className="t-12 label-track text-[color:var(--ink-faint)]">来源 B</span>
              <p className="t-13 text-[color:var(--ink)] font-semibold">OCR / 手动修正文本</p>
              <span className="t-12 text-[color:var(--ink-muted)]">
                仅存于本浏览器内存与 IndexedDB · 命中 {ocrHits.length} 条
              </span>
            </div>
            <div className="pt-2">
              <div className="demo-mark demo-mark--inline" data-demo-scope="scan-06-ocr" role="note">
                <span className="demo-mark__label">端侧文本</span>
                <p className="demo-mark__note">
                  此来源为本次拍照识别或手动粘贴的文本，不是联网取得的商品库分片；原图与文本均留在本浏览器。
                </p>
              </div>
            </div>

            {ocrHits.length > 0 ? (
              <div className="pt-3">
                <p className="t-12 label-track text-[color:var(--ink-faint)] pb-2">文本行命中</p>
                {ocrHits.map((hit, index) => (
                  <div
                    key={index}
                    className="py-2 border-b border-dotted border-[color:var(--rule-hair)]"
                    data-hit-source="ocr_text"
                  >
                    <p className="t-14 text-[color:var(--ink)]">
                      OCR 第 {hit.lineIndex} 行「{hit.line}」命中您的本地忌口词条「{hit.matchedTerm}」
                    </p>
                  </div>
                ))}
              </div>
            ) : !ocrText.trim() ? (
              <div className="pt-3">
                <EmptyState text="尚未拍摄包装或粘贴配料文字：在 02 区块识别后，此处会出现该来源的机械命中。" />
              </div>
            ) : rules.length === 0 ? (
              <div className="pt-3">
                <EmptyState text="添加本地忌口词条后，此处会显示 OCR / 手动修正文本中的机械命中项。" />
              </div>
            ) : (
              <div className="pt-3">
                <EmptyState text="OCR / 手动修正文本中未出现命中项；这仅表示词条未出现，不代表任何其他结论。" />
              </div>
            )}
          </div>
        </div>

        <p className="pt-4 t-12 text-[color:var(--ink-muted)] leading-relaxed max-w-[68ch]">
          本提示仅作配料词条机械对照，不构成医疗、营养或购买建议，请以商品实物标签为准。两份来源分别对照、互不合并。
        </p>
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
    showToast(`已加入本地词条「${term}」（仅存于本浏览器）`);
  };
  return (
    <div className="flex flex-col sm:flex-row gap-3 sm:items-end">
      <label className="flex-1 min-w-0">
        <span className="t-12 text-[color:var(--ink-muted)] block pb-1">即时添加本地忌口词条</span>
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
