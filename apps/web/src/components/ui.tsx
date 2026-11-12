/**
 * 档案文献风格的基础构件。
 *
 * 状态一律用「文字标签 + 边框形态」表达，不使用任何红/绿/黄的语义配色，
 * 也不使用暗示评分的渐变。层高只有一档（`.raised`），其余靠发丝线分隔。
 */
import { useState } from "react";
import type { ReactNode } from "react";

/* ---------------------------------------------------------------- 状态标识 */

export type StatusTone = "filled" | "pending" | "none";

export function StatusTag({ tone = "none", children }: { tone?: StatusTone; children: ReactNode }) {
  const cls =
    tone === "filled"
      ? "status-tag status-tag--filled"
      : tone === "pending"
        ? "status-tag status-tag--pending"
        : "status-tag status-tag--none";
  return <span className={cls}>{children}</span>;
}

/* ---------------------------------------------------------------- 档案章节 */

interface DocSectionProps {
  /** 章节编号，例如 01 / 02；留空则不显示编号。 */
  index?: string | number;
  title: string;
  subtitle?: string;
  /** 标题右侧的操作区 / 状态。 */
  right?: ReactNode;
  /** 右侧边注（来源、覆盖状态、证据提示）。 */
  marginalia?: ReactNode;
  /** 章节末尾的按需溯源披露（Tier 2）。 */
  trace?: ReactNode;
  children: ReactNode;
  className?: string;
  id?: string;
}

/**
 * 带编号规则线的章节。桌面端为「正文 + 窄边注」两列，窄屏折叠为单列。
 *
 * 窄屏（≤720px）边注不再夹在正文中间：它整块移到正文之后，由「本节附注」
 * 的 ≥44px 折叠入口进入（消费者默认只需要正文与一处来源/日期）。
 */
export function DocSection({
  index,
  title,
  subtitle,
  right,
  marginalia,
  trace,
  children,
  className = "",
  id,
}: DocSectionProps) {
  const label = typeof index === "number" ? String(index).padStart(2, "0") : index;
  return (
    <section className={className} id={id}>
      <div className="section-rule">
        {label ? <span className="section-num">{label}</span> : null}
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h2 className="serif t-20 text-[color:var(--teal-deep)] leading-snug">{title}</h2>
          {right}
        </div>
      </div>

      {subtitle ? (
        <p className="t-12 text-[color:var(--ink-muted)] max-w-[68ch] pt-2 leading-relaxed">{subtitle}</p>
      ) : null}

      {marginalia ? (
        <>
          <div className="archive-grid pt-3">
            <div className="min-w-0">
              {children}
              {trace ? <div className="pt-2">{trace}</div> : null}
            </div>
            <aside className="marginalia m-desktop-only" aria-label="来源与证据边注">
              {marginalia}
            </aside>
          </div>
          <div className="m-only">
            <MobileFold summary="本节附注：区块状态、计数与来源收集日期">{marginalia}</MobileFold>
          </div>
        </>
      ) : (
        <div className="pt-3">
          {children}
          {trace ? <div className="pt-2">{trace}</div> : null}
        </div>
      )}
    </section>
  );
}

/** 边注条目：极小标签 + 值。 */
export function MarginNote({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <p className="t-12 label-track text-[color:var(--ink-faint)]">{label}</p>
      <div className="t-12 text-[color:var(--ink-muted)] break-words">{value}</div>
    </div>
  );
}

/* ---------------------------------------------------------------- 证据标记 */

/** 审计视图（16 区块档案页）专用的紧凑标识：source_id / evidence_id 细框。 */
export function EvidenceTag({ sourceId, evidenceId }: { sourceId?: string; evidenceId?: string }) {
  if (!sourceId && !evidenceId) return null;
  return (
    <span className="evidence shrink-0" title="来源与证据标识">
      {sourceId ? <span className="evidence-chip">{sourceId}</span> : null}
      {evidenceId ? <span className="evidence-chip">{evidenceId}</span> : null}
    </span>
  );
}

/* ------------------------------------------------------- 消费视图：来源名称 */

export interface SourceNameEntry {
  source_id?: string;
  display_name?: string;
  authority?: string;
}

export type SourceLabeler = (sourceId?: string | null) => string;

const PARENTHETICAL = /（[^）]*）|\([^)]*\)/g;

/** 去掉 display_name 里给构建机看的括注，保留给人看的机构与名称。 */
function tidyDisplayName(value: string | undefined): string {
  return (value ?? "").replace(PARENTHETICAL, "").replace(/\s+/g, " ").trim();
}

/**
 * 从数据自带的 coverage_manifest 生成 source_id → 可读名称的映射。
 * 不在这里硬编码任何来源名：名称一律来自 JSON 载荷。
 */
export function buildSourceLabeler(entries: SourceNameEntry[] | undefined | null): SourceLabeler {
  const names = new Map<string, string>();
  for (const entry of entries ?? []) {
    const id = entry?.source_id?.trim();
    if (!id) continue;
    const authority = (entry.authority ?? "").trim();
    const display = tidyDisplayName(entry.display_name);
    const combined = authority && display ? `${authority} · ${display}` : authority || display;
    if (combined) names.set(id, combined);
  }
  return (sourceId) => {
    if (!sourceId) return "来源未标注";
    return names.get(sourceId) ?? sourceId;
  };
}

/** retrieved_at 只取日期部分：2026-11-12T04:44:37Z → 2026-11-12。 */
export function shortDate(value: string | undefined | null): string | undefined {
  if (!value) return undefined;
  const match = value.match(/^\d{4}-\d{2}-\d{2}/);
  return match ? match[0] : value;
}

/** 消费视图的一行来源：小号无衬线 + 左侧发丝线，只有名称与取得日期。 */
export function SourceLine({
  name,
  date,
  className = "",
}: {
  name: string;
  date?: string;
  className?: string;
}) {
  return (
    <span className={`source-line ${className}`.trim()}>
      <span className="source-line__name">{name}</span>
      {date ? <span className="source-line__date">{date}</span> : null}
    </span>
  );
}

export interface TraceRecord {
  source_id?: string | null;
  evidence_id?: string | null;
  retrieved_at?: string | null;
  record_id?: string | null;
  payload_sha256?: string | null;
}

/**
 * 同一 evidence 可能被多个展示条目引用；按 (source_id, evidence_id) 去重，
 * 保留首个非空 record_id 与 payload_sha256。N 即「该区块的 distinct 证据条数」。
 */
export function distinctTraceRecords(records: (TraceRecord | null | undefined)[]): TraceRecord[] {
  const out: TraceRecord[] = [];
  const index = new Map<string, number>();
  for (const record of records) {
    if (!record) continue;
    if (!record.source_id && !record.evidence_id) continue;
    const key = [record.source_id ?? "", record.evidence_id ?? ""].join("|");
    const existing = index.get(key);
    if (existing !== undefined) {
      const merged = out[existing];
      if (!merged.record_id && record.record_id) merged.record_id = record.record_id;
      if (!merged.payload_sha256 && record.payload_sha256) merged.payload_sha256 = record.payload_sha256;
      if (!merged.retrieved_at && record.retrieved_at) merged.retrieved_at = record.retrieved_at;
      continue;
    }
    index.set(key, out.length);
    out.push({ ...record });
  }
  return out;
}

/** 把同一来源、同一取得日期的记录合并成一行来源注记。 */
export function SourceNotes({
  records,
  labeler,
  className = "",
}: {
  records: (TraceRecord | null | undefined)[];
  labeler?: SourceLabeler;
  className?: string;
}) {
  const resolve = labeler ?? ((sourceId?: string | null) => sourceId ?? "来源未标注");
  const pairs: { sourceId: string; date?: string }[] = [];
  const seen = new Set<string>();
  for (const record of records) {
    if (!record) continue;
    const sourceId = record.source_id ?? "";
    const date = shortDate(record.retrieved_at);
    const key = `${sourceId}|${date ?? ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    pairs.push({ sourceId, date });
  }
  if (pairs.length === 0) return null;
  return (
    <div className={`source-notes ${className}`.trim()}>
      {pairs.map((pair) => (
        <SourceLine key={`${pair.sourceId}|${pair.date ?? ""}`} name={resolve(pair.sourceId)} date={pair.date} />
      ))}
    </div>
  );
}

/**
 * 消费者视图的来源块（手机专用，`.m-only`）。
 *
 * 手机上不逐条重复来源，改为每个区块末尾出现一次「来源中文名 + 采集日期」。
 * 追溯内容（evidence_id / sha256）仍在 `TraceDisclosure` 里默认折叠。
 * AGENTS.md「证据展示分层」的消费者层级就到这里为止。
 */
export function ConsumerSource({
  records,
  labeler,
  note,
}: {
  records: (TraceRecord | null | undefined)[];
  labeler?: SourceLabeler;
  /** 可选的一句计数说明，例如「本节 3 项配料」。 */
  note?: string;
}) {
  const resolve = labeler ?? ((sourceId?: string | null) => sourceId ?? "来源未标注");
  const pairs: { sourceId: string; date?: string }[] = [];
  const seen = new Set<string>();
  for (const record of records) {
    if (!record) continue;
    const sourceId = record.source_id ?? "";
    if (!sourceId && !record.evidence_id) continue;
    const date = shortDate(record.retrieved_at);
    const key = `${sourceId}|${date ?? ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    pairs.push({ sourceId, date });
  }
  if (pairs.length === 0) return null;
  return (
    <div className="m-only m-consumer-source" data-consumer-source="">
      {/* 一处来源块，内联排列：每个来源仍是独立的一项（并列、不合并），
          但不再每条记录各占一行，手机上「来源文字」不会比商品信息还多。 */}
      <p className="t-13 text-[color:var(--ink-muted)] leading-relaxed">
        <span className="label-track text-[color:var(--ink-faint)]">来源与采集日期：</span>
        {pairs.map((pair, index) => (
          <span key={`${pair.sourceId}|${pair.date ?? ""}`}>
            {index > 0 ? <span className="text-[color:var(--ink-faint)]">；</span> : null}
            <SourceLine name={resolve(pair.sourceId)} date={pair.date} />
          </span>
        ))}
      </p>
      {note ? <p className="t-12 text-[color:var(--ink-faint)] pt-0.5">{note}</p> : null}
    </div>
  );
}

/**
 * 手机上按需展开的长尾区块内容。
 *
 * 默认收起时正文仍在 DOM 里，但被 `.m-collapsed`（仅在 ≤720px 生效）隐藏，因此手机首屏
 * 只留下「这东西是什么」的最小集合；按钮文案必须写明展开后是什么，不用「更多」这类含糊词。
 * 宽屏没有 `.m-collapsed` 规则，内容始终展开——审计视图的完整证据密度不变。
 */
export function MobileFold({
  summary,
  children,
  defaultOpen = false,
}: {
  /** 按钮文案：必须写明「会展开哪些内容」，而不是「更多」。 */
  summary: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="m-fold">
      <button
        type="button"
        className="m-fold__toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        data-fold-toggle={summary}
      >
        <span className="m-fold__toggle-text">
          {open ? "收起" : "展开"}：{summary}
        </span>
        <span className="m-fold__toggle-glyph" aria-hidden="true">
          {open ? "−" : "+"}
        </span>
      </button>
      {/* 内容始终在 DOM 里：宽屏没有 `.m-collapsed` 规则，审计视图完整展开；
          窄屏才由 `.m-collapsed` 收起来。 */}
      <div className={`m-fold__body${open ? "" : " m-collapsed"}`}>{children}</div>
    </div>
  );
}


/**
 * 按需展开的溯源披露（Tier 2）。
 *
 * 默认折叠时正文完全不进入 DOM，直到用户真正展开——这样默认视图里既看不到
 * evidence_id 也看不到 payload_sha256。展开后逐条列出完整绑定字段。
 */
export function TraceDisclosure({
  records,
  label = "查看来源与证据",
}: {
  records: (TraceRecord | null | undefined)[];
  label?: string;
}) {
  const [revealed, setRevealed] = useState(false);
  const rows = distinctTraceRecords(records);
  if (rows.length === 0) return null;
  return (
    <details
      className="trace"
      onToggle={(event) => setRevealed(event.currentTarget.open)}
    >
      <summary className="trace__summary">
        <span className="trace__glyph" aria-hidden="true">
          <span className="trace__glyph-plus">+</span>
          <span className="trace__glyph-minus">−</span>
        </span>
        <span className="trace__text">
          {label} ({rows.length})
        </span>
      </summary>
      {revealed ? (
        <div className="trace__body">
          {rows.map((row, index) => (
            <div className="trace__record" key={`${row.evidence_id ?? "ev"}-${row.record_id ?? ""}-${index}`}>
              <TraceRow label="source_id" value={row.source_id} />
              <TraceRow label="evidence_id" value={row.evidence_id} />
              <TraceRow label="retrieved_at" value={row.retrieved_at} />
              {row.record_id ? <TraceRow label="record_id" value={row.record_id} /> : null}
              {row.payload_sha256 ? <TraceRow label="payload_sha256" value={row.payload_sha256} hash /> : null}
            </div>
          ))}
        </div>
      ) : null}
    </details>
  );
}

function TraceRow({ label, value, hash = false }: { label: string; value?: string | null; hash?: boolean }) {
  if (!value) return null;
  return (
    <div className="trace__row">
      <span className="trace__key">{label}</span>
      <span className={`trace__value${hash ? " trace__value--hash" : ""}`}>{value}</span>
    </div>
  );
}

/* ------------------------------------------------ 演示样例 / 来源焦点 */

/**
 * 「演示样例」标记。
 *
 * 只由调用方按真实 `origin` 字段决定是否渲染（`DEMO_FALLBACK` 才渲染），
 * 不在组件内部硬编码条码清单。形态为发丝线框 + 小号大写字距 label + 一句说明，
 * 不使用彩色徽章 / 胶囊 / 投影。
 */
export function DemoMark({ scope, className = "" }: { scope: string; className?: string }) {
  return (
    <div
      className={`demo-mark ${className}`.trim()}
      data-demo-mark="DEMO_FALLBACK"
      data-demo-scope={scope}
      role="note"
    >
      <span className="demo-mark__label">演示样例</span>
      <p className="demo-mark__note">
        本段数据来自内置离线样例（<span className="mono">v1/demo/index.json</span> 或单文件兜底常量），
        仅用于演示文档结构，<span className="demo-mark__emphasis">不等于已联网的真实商品库分片</span>。
      </p>
    </div>
  );
}

export interface SourceFocusOption {
  value: string;
  label: string;
  /** 该来源当前的机械命中条数，只用于显示，不参与判定。 */
  count: number;
}

/**
 * 并列的来源焦点切换：两个文字 label（原生 radio，无胶囊、无彩色）。
 * 只切换主视图焦点，不隐藏任何来源、不合并任何列表。
 */
export function SourceFocusSwitch({
  legend,
  name,
  value,
  options,
  onChange,
}: {
  legend: string;
  name: string;
  value: string;
  options: SourceFocusOption[];
  onChange: (value: string) => void;
}) {
  return (
    <fieldset className="source-focus">
      <legend className="t-12 label-track text-[color:var(--ink-faint)]">{legend}</legend>
      <div className="source-focus__row">
        {options.map((option) => {
          const active = option.value === value;
          return (
            <label
              key={option.value}
              className={`source-focus__item${active ? " is-active" : ""}`}
              data-focus-option={option.value}
            >
              <input
                type="radio"
                name={name}
                value={option.value}
                checked={active}
                onChange={() => onChange(option.value)}
                className="source-focus__radio"
                data-tap-exempt="visually-hidden-radio"
              />
              <span className="source-focus__label">{option.label}</span>
              <span className="source-focus__count mono">
                {option.count > 0 ? `${option.count} 条命中` : "无命中"}
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

/* ---------------------------------------------------------------- 其他 */

export function EmptyState({ text }: { text: string }) {
  return <div className="empty-note">{text}</div>;
}

/** 右上角的审计标记：等宽小字 + 发丝框，不用彩色胶囊。 */
export function AuditChip({ label, value, title }: { label: string; value: ReactNode; title?: string }) {
  return (
    <span className="evidence-chip mono" title={title}>
      {label}
      <span className="text-[color:var(--ink)]">{value}</span>
    </span>
  );
}

export function MetaPair({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="field-row !py-1.5">
      <span className="field-label">{label}</span>
      <span className={`field-value t-13 ${mono ? "mono" : ""}`}>{value || "—"}</span>
    </div>
  );
}
