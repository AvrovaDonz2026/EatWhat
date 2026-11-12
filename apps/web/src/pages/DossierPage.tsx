import { useEffect, useMemo, useState } from "react";
import {
  buildSourceLabeler,
  DocSection,
  EmptyState,
  EvidenceTag,
  MarginNote,
  MetaPair,
  SourceNotes,
  StatusTag,
  TraceDisclosure,
} from "@/components/ui";
import { buildHashIndex, coverageLabel, loadDossier, type DossierBundle } from "@/services/static_dossier_client";
import type { DossierBlock, DossierField, DossierItem, Observation, TimelineEntry, VisibilityEvent } from "@/types";

interface DossierPageProps {
  gtin: string;
  entityId: string;
  dataOrigin: "STATIC_LIBRARY" | "DEMO_FALLBACK" | "NONE";
  buildId: string;
}

const BLOCK_ORDER = [
  "identity_basic",
  "match_candidates",
  "label_ingredients",
  "nutrition_facts",
  "explicit_allergens",
  "explicit_unsuitable_groups",
  "storage_and_shelf_life",
  "registration_records",
  "license_records",
  "company_prefix",
  "trademark_records",
  "referenced_standards",
  "import_export_records",
  "observations",
  "history_timeline",
  "source_visibility",
];

/** 区块标题保持简写，使 16 条编号规则线读起来像目录条目。 */
const BLOCK_TITLES: Record<string, string> = {
  identity_basic: "基本信息",
  match_candidates: "标识符候选与关联状态",
  label_ingredients: "配料表原文",
  nutrition_facts: "营养成分表原文",
  explicit_allergens: "致敏原原文提示",
  explicit_unsuitable_groups: "不适宜人群原文",
  storage_and_shelf_life: "贮存条件与保质期",
  registration_records: "注册 / 备案公开记录",
  license_records: "生产许可公开记录",
  company_prefix: "条码厂商前缀登记",
  trademark_records: "商标公开记录",
  referenced_standards: "引用标准文本元数据",
  import_export_records: "进出口商备案记录",
  observations: "多源观测并列",
  history_timeline: "历史快照时间线",
  source_visibility: "来源可见性与证据哈希",
};

/** 区块副标题：说明该区块固定呈现什么，而不是营销文案。 */
const BLOCK_NOTES: Record<string, string> = {
  identity_basic: "条码 / 品牌 / 商品名 / 净含量 / 包装形态的原文值",
  match_candidates: "各来源标识符候选，逐条标注关联方式",
  label_ingredients: "标签配料表按原文顺序逐项列出",
  nutrition_facts: "营养成分表按原文数值呈现，不做换算之外的加工",
  explicit_allergens: "标签上明确书写的致敏原原文",
  explicit_unsuitable_groups: "标签上明确标注的不适宜人群原文",
  storage_and_shelf_life: "贮存条件与保质期的原文表述",
  registration_records: "注册 / 备案类公开记录",
  license_records: "生产许可类公开记录",
  company_prefix: "条码厂商前缀登记信息",
  trademark_records: "商标类公开记录",
  referenced_standards: "标签引用标准的元数据",
  import_export_records: "进出口商备案类记录",
  observations: "同一商品在不同来源下的原始观测并列",
  history_timeline: "只追加不覆盖的快照时间线",
  source_visibility: "来源可见性事件与内容哈希",
};

const STATE_LABEL: Record<string, string> = {
  FILLED: "已收录记录",
  EMPTY: "无已采集记录",
  ACCESS_PENDING: "接入待授权",
  SOURCE_UNAVAILABLE: "来源当前不可访问",
  QUERY_FAILED: "本次查询失败",
};

const STATE_TONE: Record<string, "filled" | "pending" | "none"> = {
  FILLED: "filled",
  EMPTY: "none",
  ACCESS_PENDING: "pending",
  SOURCE_UNAVAILABLE: "pending",
  QUERY_FAILED: "pending",
};

export function DossierPage({ gtin, entityId, dataOrigin, buildId }: DossierPageProps) {
  const [bundle, setBundle] = useState<DossierBundle | null>(null);
  const [loading, setLoading] = useState(false);
  const [manualGtin, setManualGtin] = useState(gtin);
  const [openGtin, setOpenGtin] = useState(gtin);

  useEffect(() => {
    setOpenGtin(gtin);
    setManualGtin(gtin);
  }, [gtin]);

  useEffect(() => {
    if (!openGtin) {
      setBundle(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    void (async () => {
      const loaded = await loadDossier(openGtin);
      if (cancelled) return;
      setBundle(loaded);
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [openGtin]);

  const product = bundle?.product ?? null;

  /** source_id → 可读名称：取自档案自带的 coverage_manifest。 */
  const sourceLabel = useMemo(
    () => buildSourceLabeler(product?.coverage_manifest ?? bundle?.record.coverage_manifest ?? []),
    [product, bundle],
  );

  /** 审计视图里按 evidence_id 补齐 payload_sha256（来自 observations / timeline，只读查找）。 */
  const hashFor = useMemo(() => buildHashIndex(bundle), [bundle]);

  const blocks = useMemo<DossierBlock[]>(() => {
    if (!product) return [];
    const byId = new Map(product.blocks.map((block) => [block.block_id, block]));
    return BLOCK_ORDER.map((id) => {
      const block = byId.get(id);
      if (block) return block;
      return {
        block_id: id,
        title: BLOCK_TITLES[id] ?? id,
        state: "EMPTY",
        note: "本区块无已采集记录",
        fields: [],
        items: [],
      };
    });
  }, [product]);

  const queryField = (
    <div className="flex flex-col sm:flex-row gap-3 sm:items-end">
      <label className="flex-1 min-w-0">
        <span className="t-12 text-[color:var(--ink-muted)] block pb-1">条码</span>
        <input
          value={manualGtin}
          onChange={(event) => setManualGtin(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") setOpenGtin(manualGtin.replace(/[^0-9]/g, ""));
          }}
          placeholder="例如 6901234567892"
          inputMode="numeric"
          className="field-input field-input--mono"
        />
      </label>
      <button
        type="button"
        onClick={() => setOpenGtin(manualGtin.replace(/[^0-9]/g, ""))}
        className="btn btn--primary shrink-0"
      >
        打开档案
      </button>
    </div>
  );

  if (!openGtin) {
    return (
      <div className="space-y-10">
        <DocSection
          index="00"
          title="尚未选择档案"
          subtitle="先在「扫码与成分对照」页检索条码，或在此直接打开已收录的条码。"
          right={<StatusTag tone="none">entity_id: {entityId || "—"}</StatusTag>}
          marginalia={
            <>
              <MarginNote label="区块数" value="固定 16 个区块" />
              <MarginNote label="区块语义" value="要么给出原文与证据，要么明确标注无记录 / 接入待授权 / 来源不可访问" />
              <MarginNote label="build_id" value={<span className="mono">{buildId || "—"}</span>} />
            </>
          }
        >
          {queryField}
          <div className="pt-4">
            <EmptyState text="尚未选择商品。档案页固定展示 16 个区块，每个区块的收录状态均以文字如实标注。" />
          </div>
        </DocSection>
      </div>
    );
  }

  if (loading) {
    return (
      <DocSection index="00" title="正在读取静态档案" subtitle={`条码 ${openGtin}`}>
        <EmptyState text="读取静态 JSON 分片中…" />
      </DocSection>
    );
  }

  if (!product || !bundle) {
    return (
      <div className="space-y-10">
        {openGtin !== gtin ? (
          <DocSection index="00" title="打开其他条码" subtitle="当前查询条码与已选中档案不同">
            {queryField}
          </DocSection>
        ) : null}
        <DocSection index="01" title="未找到档案" subtitle={`条码 ${openGtin}`}>
          <div className="border border-dashed border-[color:var(--rule-strong)] px-4 py-3">
            <p className="mono !text-[12px] text-[color:var(--ink-soft)]">QUERIED_NO_MATCH</p>
            <p className="t-13 text-[color:var(--ink-soft)] pt-1.5 leading-relaxed">
              当前已接入静态档案库未匹配到记录。此提示不构成任何结论，仅表示现有静态分片中没有该条码。
            </p>
          </div>
        </DocSection>
      </div>
    );
  }

  const filledCount = product.blocks.filter((block) => block.state === "FILLED").length;

  return (
    <div className="space-y-10">
      {/* 档案封面页 */}
      <DocSection
        index="00"
        title="档案封面"
        subtitle="每个展示字段均绑定 source_id / evidence_id / retrieved_at / raw_value；不设最终真相字段。"
        right={
          <div className="flex flex-wrap gap-2">
            <StatusTag tone="filled">{bundle.origin}</StatusTag>
            <StatusTag tone={filledCount === 16 ? "filled" : "pending"}>
              {filledCount} / 16 区块已收录
            </StatusTag>
          </div>
        }
        marginalia={
          <>
            <MarginNote label="状态说明" value="「已收录」仅指存在原文记录，不代表任何评价" />
            <MarginNote label="观测记录数" value={<span className="mono">{product.observations.length}</span>} />
            <MarginNote
              label="可见性事件"
              value={<span className="mono">{product.source_visibility_events.length}</span>}
            />
            <MarginNote label="数据来源" value={dataOrigin} />
          </>
        }
      >
        <div className="border border-[color:var(--rule)] bg-[color:var(--paper-raised)] px-4 sm:px-6">
          <dl className="divide-y-0">
            <MetaPair label="条码 GTIN" value={product.gtin} mono />
            <MetaPair label="entity_id" value={product.entity_id} mono />
            <MetaPair label="build_id" value={product.build_id} mono />
            <MetaPair label="schema_version" value={product.schema_version} mono />
          </dl>
        </div>

        <div className="flex flex-wrap gap-x-5 gap-y-2 pt-3">
          <span className="evidence">
            <span className="evidence-chip">no_final_truth_field</span>
            <span className="text-[color:var(--ink)]">
              {String(product.observation_policy.no_final_truth_field)}
            </span>
          </span>
          <span className="evidence">
            <span className="evidence-chip">no_auto_merge</span>
            <span className="text-[color:var(--ink)]">{String(product.observation_policy.no_auto_merge)}</span>
          </span>
          <span className="evidence">
            <span className="evidence-chip">observations 绑定</span>
            <span className="text-[color:var(--ink)]">
              {product.observation_policy.observations_bound_to.join(" / ")}
            </span>
          </span>
        </div>

        <p className="t-12 label-track text-[color:var(--ink-faint)] pt-6 pb-2">
          数据源覆盖清单
        </p>
        <table className="data-table">
          <thead>
            <tr>
              <th>数据源</th>
              <th>访问形态</th>
              <th>覆盖状态</th>
            </tr>
          </thead>
          <tbody>
            {product.coverage_manifest.map((entry) => (
              <tr key={entry.source_id}>
                <td>
                  <span className="t-13">{entry.display_name}</span>
                  <span className="evidence ml-2">{entry.source_id}</span>
                </td>
                <td className="mono text-[color:var(--ink-muted)]">{entry.access_mode}</td>
                <td className="t-12 text-[color:var(--ink-soft)]">
                  {coverageLabel(entry.coverage_state)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {openGtin !== gtin ? (
          <div className="pt-6">{queryField}</div>
        ) : null}
      </DocSection>

      {/* 16 个区块：编号章节，而非 16 张同样的卡片 */}
      {blocks.map((block, index) => (
        <BlockSection
          key={block.block_id}
          index={index + 1}
          block={block}
          labeler={sourceLabel}
          hashFor={hashFor}
        />
      ))}

      <DocSection
        index="17"
        title="原始证据与历史时间线"
        subtitle="历史快照只追加、不覆盖；来源更新或下线不会删除旧快照。"
        right={<StatusTag tone="none">{product.history_timeline.length} 条快照</StatusTag>}
        marginalia={
          <>
            <MarginNote label="快照条数" value={`${product.history_timeline.length} 条`} />
            <MarginNote label="观测条数" value={`${product.observations.length} 条`} />
            <MarginNote label="可见性事件" value={`${product.source_visibility_events.length} 条`} />
          </>
        }
      >
        <Timeline observations={product.observations} timeline={product.history_timeline} />
        <div className="pt-6">
          <p className="t-12 label-track text-[color:var(--ink-faint)] pb-2">
            来源可见性事件 · source_visibility_event
          </p>
          <VisibilityList events={product.source_visibility_events} />
        </div>
      </DocSection>

      <p className="t-12 text-[color:var(--ink-muted)] leading-relaxed border-t border-[color:var(--rule)] pt-4 max-w-[80ch]">
        页面数据来源：{dataOrigin}。所有展示内容均为来源原文或原文的机械结构化结果，不含风险评分、真伪判断、合法性定性、关系推断、购买建议或功效评价。
      </p>
    </div>
  );
}

function BlockSection({
  index,
  block,
  labeler,
  hashFor,
}: {
  index: number;
  block: DossierBlock;
  labeler: (sourceId?: string | null) => string;
  hashFor: (evidenceId?: string | null) => string | undefined;
}) {
  const sectionSpecial =
    block.block_id === "observations" ||
    block.block_id === "history_timeline" ||
    block.block_id === "source_visibility";
  const empty =
    !sectionSpecial && (block.state !== "FILLED" || (block.fields.length === 0 && block.items.length === 0));

  const traceRecords = useMemo(() => {
    const out: {
      source_id?: string;
      evidence_id?: string;
      retrieved_at?: string;
      record_id?: string;
      payload_sha256?: string;
    }[] = [];
    for (const field of block.fields) {
      out.push({
        source_id: field.source_id,
        evidence_id: field.evidence_id,
        retrieved_at: field.retrieved_at,
        payload_sha256: hashFor(field.evidence_id),
      });
    }
    for (const item of block.items) {
      out.push({
        source_id: item.source_id ? String(item.source_id) : undefined,
        evidence_id: item.evidence_id ? String(item.evidence_id) : undefined,
        retrieved_at: item.retrieved_at ? String(item.retrieved_at) : undefined,
        record_id: item.record_id ? String(item.record_id) : undefined,
        payload_sha256: item.payload_sha256 ? String(item.payload_sha256) : hashFor(String(item.evidence_id)),
      });
    }
    return out;
  }, [block, hashFor]);

  const sourceIds = useMemo(() => {
    const ids = new Set<string>();
    for (const field of block.fields) if (field.source_id) ids.add(field.source_id);
    for (const item of block.items) if (item.source_id) ids.add(String(item.source_id));
    return [...ids];
  }, [block]);

  const sourceNames = sourceIds.map((id) => labeler(id));

  return (
    <section className="doc-block" data-block-id={block.block_id} data-block-state={block.state}>
      <div className="doc-block__head">
        <span className="doc-block__num">{String(index).padStart(2, "0")}</span>
        <div className="min-w-0">
          <h2 className="serif t-20 text-[color:var(--teal-deep)] leading-snug">{block.title}</h2>
          <p className="t-12 text-[color:var(--ink-muted)] pt-0.5 leading-relaxed max-w-[68ch]">
            {BLOCK_NOTES[block.block_id] ?? block.note}
          </p>
        </div>
        <StatusTag tone={STATE_TONE[block.state] ?? "none"}>{STATE_LABEL[block.state] ?? block.state}</StatusTag>
      </div>

      {empty ? (
        <div className="archive-grid pt-3">
          <EmptyState text="本区块当前无已采集记录。覆盖状态见封面页「数据源覆盖清单」；未接入或不可用的来源已如实披露。" />
          <aside className="marginalia" aria-label="来源与证据边注">
            <MarginNote label="区块状态" value={STATE_LABEL[block.state] ?? block.state} />
            <MarginNote label="来源" value="—" />
          </aside>
        </div>
      ) : (
        <div className="archive-grid pt-3">
          <div className="min-w-0">
            {block.fields.length > 0 ? (
              <dl>
                {block.fields.map((field, fieldIndex) => (
                  <FieldRow key={`${field.evidence_id}-${field.label}-${fieldIndex}`} field={field} />
                ))}
              </dl>
            ) : null}

            {block.items.length > 0 ? (
              <div className={`${block.fields.length > 0 ? "pt-4" : ""}`}>
                {block.items.map((item, itemIndex) => (
                  <ItemRow key={`${item.evidence_id ?? "item"}-${itemIndex}`} item={item} />
                ))}
              </div>
            ) : null}

            {/* 审计视图：完整哈希收进按需展开的披露，不再常驻边注 */}
            <TraceDisclosure records={traceRecords} label="区块来源与证据" />
          </div>

          <aside className="marginalia" aria-label="来源与证据边注">
            <MarginNote label="区块状态" value={STATE_LABEL[block.state] ?? block.state} />
            <MarginNote label="字段数" value={`${block.fields.length} 项字段 / ${block.items.length} 条记录`} />
            {sourceNames.length > 0 ? (
              <div>
                <p className="t-12 label-track text-[color:var(--ink-faint)]">来源与取得</p>
                <SourceNotes records={traceRecords} labeler={labeler} />
              </div>
            ) : (
              <MarginNote label="来源" value="—" />
            )}
          </aside>
        </div>
      )}
    </section>
  );
}

function FieldRow({ field }: { field: DossierField }) {
  const display = asText(field.value);
  const raw = asText(field.raw_value);
  return (
    <div className="field-row">
      <div className="min-w-0">
        <p className="field-label">{field.label}</p>
        <p className="mono text-[color:var(--ink-faint)] pt-0.5">{field.retrieved_at}</p>
      </div>
      <div className="min-w-0">
        <p className="field-value">{display || "—"}</p>
        {raw !== display ? (
          <p className="t-12 text-[color:var(--ink-muted)] pt-0.5">原文：{raw}</p>
        ) : null}
        <div className="pt-1">
          <EvidenceTag sourceId={field.source_id} evidenceId={field.evidence_id} />
        </div>
      </div>
    </div>
  );
}

function ItemRow({ item }: { item: DossierItem }) {
  const primary =
    asText(item.raw_name) ||
    asText(item.raw_text) ||
    asText(item.display_name) ||
    asText(item.standard_no) ||
    asText(item.nutrient) ||
    asText(item.path) ||
    "记录";
  const secondary: string[] = [];
  if (item.order) secondary.push(`第 ${item.order} 项`);
  if (item.raw_value && asText(item.raw_value) !== primary) secondary.push(`原文：${asText(item.raw_value)}`);
  if (item.search_keys && Array.isArray(item.search_keys))
    secondary.push(`检索键：${(item.search_keys as string[]).join("、")}`);
  if (item.link_mode) secondary.push(String(item.link_mode));
  if (item.event_type) secondary.push(String(item.event_type));
  // 审计视图保留哈希前缀（完整值在区块披露里）
  if (item.payload_sha256) secondary.push(`sha256:${String(item.payload_sha256).slice(0, 16)}…`);
  if (item.coverage_state) secondary.push(String(item.coverage_state));
  if (item.sha256) secondary.push(`sha256:${String(item.sha256).slice(0, 16)}…`);
  if (item.record_id) secondary.push(`record:${String(item.record_id)}`);

  return (
    <div className="py-2 border-b border-dotted border-[color:var(--rule-hair)]">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
        <div className="min-w-0 flex-1">
          <p className="t-13 text-[color:var(--ink)] break-all">{primary}</p>
          {secondary.length > 0 ? (
            <p className="t-12 text-[color:var(--ink-muted)] pt-0.5 break-all">{secondary.join(" · ")}</p>
          ) : null}
          {item.retrieved_at ? (
            <p className="mono text-[color:var(--ink-faint)] pt-0.5">
              {String(item.retrieved_at)}
            </p>
          ) : null}
        </div>
        <EvidenceTag sourceId={item.source_id} evidenceId={item.evidence_id} />
      </div>
    </div>
  );
}

function asText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function Timeline({ observations, timeline }: { observations: Observation[]; timeline: TimelineEntry[] }) {
  if (observations.length === 0 && timeline.length === 0) {
    return <EmptyState text="本档案暂无快照记录。" />;
  }
  return (
    <div>
      {timeline.map((entry) => (
        <div
          key={entry.evidence_id}
          className="py-2 border-b border-dotted border-[color:var(--rule-hair)] flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1"
        >
          <div className="min-w-0">
            <p className="mono !text-[12px] text-[color:var(--ink)]">{entry.retrieved_at}</p>
            <p className="t-12 text-[color:var(--ink-muted)] pt-0.5">
              来源 {entry.source_id} · 适配器 v{entry.adapter_version} · sha256{" "}
              <span className="mono">{entry.payload_sha256.slice(0, 20)}…</span>
            </p>
          </div>
          <span className="mono text-[color:var(--ink-faint)]">{entry.evidence_id}</span>
        </div>
      ))}
      {observations.length > 0 ? (
        <div className="pt-5">
          <p className="t-12 label-track text-[color:var(--ink-faint)] pb-2">
            原始观测并列 · raw_value 原样保留
          </p>
          <div className="max-h-80 overflow-y-auto border border-[color:var(--rule)]">
            {observations.map((entry) => (
              <div
                key={entry.evidence_id}
                className="px-3 py-2 border-b border-dotted border-[color:var(--rule-hair)] last:border-b-0 bg-[color:var(--paper-raised)]"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="mono text-[color:var(--ink-soft)]">{entry.source_id}</p>
                  <EvidenceTag evidenceId={entry.evidence_id} />
                </div>
                <pre className="mt-1 mono text-[color:var(--ink-muted)] whitespace-pre-wrap break-all max-h-28 overflow-y-auto">
                  {entry.raw_value}
                </pre>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function VisibilityList({ events }: { events: VisibilityEvent[] }) {
  if (events.length === 0) {
    return <EmptyState text="本档案暂无可见性事件记录（离线样例不含历史事件）。" />;
  }
  return (
    <div>
      {events.map((event) => (
        <div
          key={event.event_id}
          className="py-2 border-b border-dotted border-[color:var(--rule-hair)]"
        >
          <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
            <div className="min-w-0">
              <p className="t-13 text-[color:var(--ink)]">
                {event.source_id} · <span className="mono">{event.event_type}</span>
              </p>
              <p className="t-12 text-[color:var(--ink-muted)] pt-0.5">{event.note}</p>
            </div>
            <EvidenceTag evidenceId={event.evidence_id} />
          </div>
          <p className="mono text-[color:var(--ink-faint)] pt-0.5">
            {event.recorded_at} · {event.event_id}
          </p>
        </div>
      ))}
    </div>
  );
}
