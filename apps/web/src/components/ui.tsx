/**
 * 档案文献风格的基础构件。
 *
 * 状态一律用「文字标签 + 边框形态」表达，不使用任何红/绿/黄的语义配色，
 * 也不使用暗示评分的渐变。层高只有一档（`.raised`），其余靠发丝线分隔。
 */
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
  children: ReactNode;
  className?: string;
  id?: string;
}

/**
 * 带编号规则线的章节。桌面端为「正文 + 窄边注」两列，窄屏折叠为单列。
 */
export function DocSection({
  index,
  title,
  subtitle,
  right,
  marginalia,
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
        <div className="archive-grid pt-3">
          <div className="min-w-0">{children}</div>
          <aside className="marginalia" aria-label="来源与证据边注">
            {marginalia}
          </aside>
        </div>
      ) : (
        <div className="pt-3">{children}</div>
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

export function EvidenceTag({ sourceId, evidenceId }: { sourceId?: string; evidenceId?: string }) {
  if (!sourceId && !evidenceId) return null;
  return (
    <span className="evidence shrink-0" title="来源与证据标识">
      {sourceId ? <span className="evidence-chip">{sourceId}</span> : null}
      {evidenceId ? <span className="evidence-chip">{evidenceId}</span> : null}
    </span>
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
      <span className={`field-value t-13 break-all ${mono ? "mono" : ""}`}>{value || "—"}</span>
    </div>
  );
}
