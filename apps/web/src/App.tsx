import { useCallback, useEffect, useMemo, useState } from "react";
import { AuditChip } from "@/components/ui";
import { WeaveTexture } from "@/components/WeaveTexture";
import { ScanPage } from "@/pages/ScanPage";
import { DietPage } from "@/pages/DietPage";
import { DossierPage } from "@/pages/DossierPage";
import { localDataSummary } from "@/local_store/db";
import { loadManifest } from "@/services/static_dossier_client";

type Tab = "scan" | "diet" | "dossier";

/** 页签标题必须保留完整中文名，验收门禁按可见名称定位按钮。 */
const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: "scan", label: "扫码与成分对照", hint: "条码 / 端侧 OCR / 本地词条机械对照" },
  { id: "diet", label: "我的忌口与饮食计划", hint: "纯本地 · IndexedDB" },
  { id: "dossier", label: "16 区块档案与来源", hint: "多源并列 · 证据追溯" },
];

export interface AppState {
  tab: Tab;
  gtin: string;
  entityId: string;
  buildId: string;
  dataOrigin: "STATIC_LIBRARY" | "DEMO_FALLBACK" | "NONE";
}

export default function App() {
  const [tab, setTab] = useState<Tab>("scan");
  const [gtin, setGtin] = useState("");
  const [entityId, setEntityId] = useState("");
  const [buildId, setBuildId] = useState("");
  const [dataOrigin, setDataOrigin] = useState<AppState["dataOrigin"]>("NONE");
  const [summary, setSummary] = useState({ rules: 0, logs: 0, history: 0 });
  const [cookieCount, setCookieCount] = useState(0);
  const [toast, setToast] = useState<string | null>(null);

  // 运行时状态：连续读取 Cookie 长度，证明零 Cookie
  useEffect(() => {
    const readCookieState = () => setCookieCount(document.cookie.length);
    readCookieState();
    const timer = window.setInterval(readCookieState, 2000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    void (async () => {
      const manifest = await loadManifest();
      if (manifest) setBuildId(manifest.build_id);
    })();
  }, []);

  const refreshSummary = useCallback(async () => {
    setSummary(await localDataSummary());
  }, []);

  useEffect(() => {
    void refreshSummary();
  }, [refreshSummary, tab]);

  const showToast = useCallback((text: string) => {
    setToast(text);
    window.setTimeout(() => setToast(null), 2600);
  }, []);

  const openDossier = useCallback(
    (nextGtin: string, nextEntityId: string, origin: AppState["dataOrigin"]) => {
      setGtin(nextGtin);
      setEntityId(nextEntityId);
      setDataOrigin(origin);
      setTab("dossier");
    },
    [],
  );

  const activeTab = useMemo(() => TABS.find((item) => item.id === tab) ?? TABS[0], [tab]);

  return (
    <div className="min-h-screen">
      <header className="masthead sticky top-0 z-20 relative">
        <WeaveTexture band={150} />

        <div className="relative max-w-5xl mx-auto px-5 sm:px-8">
          {/* 页眉：报头样式 */}
          <div className="flex flex-wrap items-start justify-between gap-4 pt-5 pb-4">
            <div className="flex items-start gap-3 min-w-0">
              <span
                aria-hidden="true"
                className="serif shrink-0 w-9 h-9 border border-[color:var(--teal)] text-[color:var(--teal-deep)] flex items-center justify-center t-16 leading-none"
              >
                档
              </span>
              <div className="min-w-0">
                <p className="t-12 label-track text-[color:var(--ochre)] pb-1">
                  Public Product Dossier · 公开信息档案
                </p>
                <h1 className="serif t-28 text-[color:var(--teal-deep)] leading-[1.2]">
                  商品公开信息展示器
                </h1>
                <p className="t-12 text-[color:var(--ink-muted)] pt-1 leading-relaxed">
                  只做检索 · 取得 · 保存 · 结构化 · 并列 · 对照 · 时间线 · 来源追溯
                </p>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 shrink-0 pt-1">
              <AuditChip
                label="cookie.length="
                value={cookieCount}
                title="运行时连续读取 document.cookie.length；始终为 0 表示未写入任何 Cookie"
              />
              <AuditChip
                label="IndexedDB "
                value={`${summary.rules} 词条 / ${summary.logs} 记录`}
                title="忌口词条、饮食计划与扫码历史仅保存在本浏览器 IndexedDB"
              />
              <span className="t-12 text-[color:var(--ink-muted)]">本地存储 · 不上传</span>
            </div>
          </div>

          {/* 页签：下划线形态 */}
          <nav
            className="flex gap-6 overflow-x-auto border-t border-[color:var(--rule-hair)]"
            aria-label="主导航"
          >
            {TABS.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setTab(item.id)}
                title={item.hint}
                aria-current={tab === item.id ? "page" : undefined}
                className={`tab ${tab === item.id ? "tab--active" : ""}`}
              >
                {item.label}
              </button>
            ))}
          </nav>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-5 sm:px-8 py-8">
        <div className="flex items-baseline justify-between gap-4 pb-5">
          <p className="t-12 label-track text-[color:var(--ink-faint)]">
            {String(TABS.findIndex((item) => item.id === tab) + 1).padStart(2, "0")} · {activeTab.label}
          </p>
          <hr className="rule flex-1" />
        </div>

        {tab === "scan" ? (
          <ScanPage onOpenDossier={openDossier} showToast={showToast} onDataChanged={refreshSummary} />
        ) : null}
        {tab === "diet" ? (
          <DietPage gtin={gtin} showToast={showToast} onDataChanged={refreshSummary} />
        ) : null}
        {tab === "dossier" ? (
          <DossierPage gtin={gtin} entityId={entityId} dataOrigin={dataOrigin} buildId={buildId} />
        ) : null}
      </main>

      <footer className="border-t border-[color:var(--rule)] bg-[color:var(--paper-sunk)]">
        <div className="max-w-5xl mx-auto px-5 sm:px-8 py-8">
          <p className="t-12 label-track text-[color:var(--ink-faint)] pb-3">免责与运行状态</p>
          <div className="space-y-2 t-12 text-[color:var(--ink-muted)] leading-relaxed max-w-[80ch]">
            <p>
              本页面仅展示已接入公开来源的原始记录与并列对照结果，不提供风险评分、真伪判断、合法性定性、关系推断、购买建议或功效评价。
            </p>
            <p>
              拍照原图、端侧 OCR 结果、忌口词条与饮食计划 100% 保存在本浏览器 IndexedDB；不使用 Cookie，不上传服务器，不写入日志。
            </p>
          </div>
          <hr className="hair my-4" />
          <div className="flex flex-wrap gap-x-6 gap-y-1 mono text-[color:var(--ink-faint)]">
            <span>build_id={buildId || "—"}</span>
            <span>data_origin={dataOrigin}</span>
            <span>cookie.length={cookieCount}</span>
          </div>
        </div>
      </footer>

      {toast ? (
        <div
          role="status"
          className="fixed bottom-5 left-1/2 -translate-x-1/2 z-50 raised px-4 py-2.5 t-13 text-[color:var(--ink)] max-w-[min(28rem,calc(100vw-2rem))]"
        >
          {toast}
        </div>
      ) : null}
    </div>
  );
}
