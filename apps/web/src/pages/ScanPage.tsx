import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DocSection, EmptyState, EvidenceTag, MarginNote, StatusTag } from "@/components/ui";
import { formatHitNotice, matchIngredients, matchOcrText, normalizeText } from "@/engine/local_diet_matcher";
import { addScanHistory, listRules, type DietRule } from "@/local_store/db";
import {
  coverageLabel,
  demoGtins,
  loadDossier,
  lookupGtin,
  type DossierBundle,
  type GtinLookup,
} from "@/services/static_dossier_client";
import { detectBarcode } from "@/ocr/barcode";
import { extractIngredientLines, recognizeLabel, type OcrProgress } from "@/ocr/local_ocr";

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

export function ScanPage({ onOpenDossier, showToast, onDataChanged }: ScanPageProps) {
  const [query, setQuery] = useState("");
  const [bundle, setBundle] = useState<DossierBundle | null>(null);
  const [lookup, setLookup] = useState<GtinLookup | null>(null);
  const [noMatchGtin, setNoMatchGtin] = useState<string | null>(null);
  const [rules, setRules] = useState<DietRule[]>([]);
  const [busy, setBusy] = useState(false);
  const [statusNote, setStatusNote] = useState<string | null>(null);

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

  const record = bundle?.record ?? lookup?.record ?? null;
  const coverage = record?.coverage_manifest ?? [];
  const ingredientsDoc = bundle?.ingredients ?? null;
  const sourceCount = ingredientsDoc
    ? new Set(ingredientsDoc.ingredients.map((item) => item.source_id)).size
    : 0;

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
            >
              {item.label}
            </button>
          ))}
        </div>
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
            </>
          }
        >
          <table className="data-table">
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
                    <span className="evidence ml-2">{entry.source_id}</span>
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
            </>
          }
        >
          <div>
            {record.match_candidates.map((candidate) => (
              <div
                key={candidate.candidate_id}
                className="py-2.5 border-b border-dotted border-[color:var(--rule-hair)] first:pt-0"
              >
                <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
                  <div className="min-w-0 flex-1">
                    <p className="t-14 text-[color:var(--ink)]">
                      {candidate.display_name || "（该来源未提供商品名）"}
                    </p>
                    <p className="t-12 text-[color:var(--ink-muted)] pt-0.5 mono">
                      {candidate.identifier_type} = {candidate.identifier_value}
                      {candidate.net_content ? ` · 净含量 ${candidate.net_content}` : ""}
                      {candidate.pack_type ? ` · ${candidate.pack_type}` : ""}
                    </p>
                    <p className="t-12 text-[color:var(--ink-faint)] pt-0.5">{candidate.link_reason}</p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <StatusTag tone={candidate.link_mode === "EXACT_IDENTIFIER_MATCH" ? "filled" : "pending"}>
                      {candidate.link_mode}
                    </StatusTag>
                    <EvidenceTag sourceId={candidate.source_id} evidenceId={candidate.evidence_id} />
                  </div>
                </div>
              </div>
            ))}
          </div>
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
            </>
          }
        >
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            <div>
              <p className="t-12 label-track text-[color:var(--ink-faint)] pb-1">
                配料表原文顺序
              </p>
              {ingredientsDoc.ingredients.length === 0 ? (
                <EmptyState text="该档案未包含配料表原文记录。" />
              ) : (
                <ol>
                  {ingredientsDoc.ingredients.map((item, index) => (
                    <li
                      key={`${item.evidence_id}-${item.order}-${index}`}
                      className="flex items-start justify-between gap-3 py-1.5 border-b border-dotted border-[color:var(--rule-hair)]"
                    >
                      <span className="t-13 text-[color:var(--ink)] flex gap-2 min-w-0">
                        <span className="mono text-[color:var(--ink-faint)] shrink-0 w-5 text-right">
                          {item.order}
                        </span>
                        <span>{item.raw_name}</span>
                      </span>
                      <EvidenceTag sourceId={item.source_id} evidenceId={item.evidence_id} />
                    </li>
                  ))}
                </ol>
              )}

              <p className="t-12 label-track text-[color:var(--ink-faint)] pt-6 pb-1">
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
                      <div className="flex items-start justify-between gap-3">
                        <span className="t-13 text-[color:var(--ink)]">{item.raw_text}</span>
                        <EvidenceTag sourceId={item.source_id} evidenceId={item.evidence_id} />
                      </div>
                      {item.standard_ref ? (
                        <p className="t-12 text-[color:var(--ink-faint)] pt-0.5">
                          标签标准引用：{item.standard_ref}
                        </p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}

              {ingredientsDoc.explicit_unsuitable_groups.length > 0 ? (
                <>
                  <p className="t-12 label-track text-[color:var(--ink-faint)] pt-6 pb-1">
                    标签标注的不适宜人群原文
                  </p>
                  <ul>
                    {ingredientsDoc.explicit_unsuitable_groups.map((item, index) => (
                      <li
                        key={`${item.evidence_id}-${index}`}
                        className="py-1.5 border-b border-dotted border-[color:var(--rule-hair)] flex items-start justify-between gap-3"
                      >
                        <span className="t-13 text-[color:var(--ink)]">{item.raw_text}</span>
                        <EvidenceTag sourceId={item.source_id} evidenceId={item.evidence_id} />
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
            </div>

            <div>
              <p className="t-12 label-track text-[color:var(--ink-faint)] pb-1">
                营养成分表原文
              </p>
              {ingredientsDoc.nutrition_facts.length === 0 ? (
                <EmptyState text="该档案未包含营养成分表记录。" />
              ) : (
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>营养素</th>
                      <th className="text-right">原文数值</th>
                      <th className="text-right evidence-cell">证据</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ingredientsDoc.nutrition_facts.map((item, index) => (
                      <tr key={`${item.evidence_id}-${index}`}>
                        <td>{item.nutrient}</td>
                        <td className="text-right mono !text-[12px]">{item.raw_value}</td>
                        <td className="text-right evidence-cell">
                          <EvidenceTag sourceId={item.source_id} evidenceId={item.evidence_id} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
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
            <MarginNote label="命中条数" value={`${(ingredientHits?.ingredientHits.length ?? 0) + (ingredientHits?.allergenHits.length ?? 0) + ocrHits.length} 条`} />
          </>
        }
      >
        <QuickRuleAdder
          onAdded={() => {
            void (async () => setRules(await listRules()))();
          }}
          showToast={showToast}
        />

        {ingredientHits && ingredientHits.ingredientHits.length > 0 ? (
          <div className="pt-5">
            <p className="t-12 label-track text-[color:var(--ink-faint)] pb-2">档案配料命中</p>
            <div>
              {ingredientHits.ingredientHits.map((hit, index) => (
                <div
                  key={`${hit.evidenceId}-${hit.order}-${index}`}
                  className="py-2 border-b border-dotted border-[color:var(--rule-hair)] flex items-start justify-between gap-3"
                >
                  <p className="t-13 text-[color:var(--ink)]">{formatHitNotice(hit)}</p>
                  <EvidenceTag sourceId={hit.sourceId} evidenceId={hit.evidenceId} />
                </div>
              ))}
            </div>
            {ingredientHits.allergenHits.map((hit, index) => (
              <div
                key={`${hit.evidenceId}-${index}`}
                className="py-2 border-b border-dotted border-[color:var(--rule-hair)] flex items-start justify-between gap-3"
              >
                <p className="t-13 text-[color:var(--ink)]">
                  标签致敏原原文「{hit.rawText}」命中您的本地忌口词条「{hit.matchedTerm}」
                </p>
                <EvidenceTag sourceId={hit.sourceId} evidenceId={hit.evidenceId} />
              </div>
            ))}
          </div>
        ) : null}

        {ocrHits.length > 0 ? (
          <div className="pt-5">
            <p className="t-12 label-track text-[color:var(--ink-faint)] pb-2">
              端侧 OCR 文本命中 · 基于本次识别原文
            </p>
            {ocrHits.map((hit, index) => (
              <div
                key={index}
                className="py-2 border-b border-dotted border-[color:var(--rule-hair)]"
              >
                <p className="t-13 text-[color:var(--ink)]">
                  OCR 第 {hit.lineIndex} 行「{hit.line}」命中您的本地忌口词条「{hit.matchedTerm}」
                </p>
              </div>
            ))}
          </div>
        ) : null}

        {ingredientHits && ingredientHits.ingredientHits.length === 0 && ocrHits.length === 0 ? (
          <div className="pt-4">
            <EmptyState
              text={
                rules.length === 0
                  ? "添加本地忌口词条后，此处会显示配料原文中的机械命中项。"
                  : "本次比对未出现命中项；这仅表示词条未出现，不代表任何其他结论。"
              }
            />
          </div>
        ) : null}

        <p className="pt-4 t-12 text-[color:var(--ink-muted)] leading-relaxed max-w-[68ch]">
          本提示仅作配料词条机械对照，不构成医疗、营养或购买建议，请以商品实物标签为准。
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
