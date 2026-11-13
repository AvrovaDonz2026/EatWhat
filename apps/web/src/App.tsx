import { useCallback, useEffect, useMemo, useState } from "react";
import { AuditChip } from "@/components/ui";
import { WeaveTexture } from "@/components/WeaveTexture";
import { ScanPage } from "@/pages/ScanPage";
import { DietPage } from "@/pages/DietPage";
import { DossierPage } from "@/pages/DossierPage";
import { localDataSummary } from "@/local_store/db";
import { loadManifest } from "@/services/static_dossier_client";

type Tab = "scan" | "diet" | "dossier";

/**
 * 页签标题必须保留完整中文名，验收门禁按可见名称定位按钮。
 *
 * 用词纪律（docs/VOICE.md）：消费者主线不用「检索 / 档案 / 区块 / 追溯」。
 * 「原始记录与出处」就是审计层入口——它不再自称「16 区块档案」，
 * 但点进去仍是完整的 16 区块档案，一个字段都没少。
 */
const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: "scan", label: "扫码看成分", hint: "扫条码 / 拍包装 / 对照忌口" },
  { id: "diet", label: "我的忌口与饮食计划", hint: "只存在这台设备上" },
  { id: "dossier", label: "原始记录与出处", hint: "16 个栏目、原文编号与内容摘要" },
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

  // 运行时状态：连续读取 Cookie 长度。用途仅为「现状快照」——Cookie 是允许的本地
  // 存储形态，隐私红线不是「不写 Cookie」，而是「Cookie 不出网」（由门禁 8 守）。
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
          <div className="flex flex-wrap items-start justify-between gap-4 pt-5 pb-4">
            <div className="flex items-start gap-3 min-w-0">
              <span
                aria-hidden="true"
                className="shrink-0 w-9 h-9 border border-[color:var(--teal)] text-[color:var(--teal-deep)] flex items-center justify-center t-16 leading-none"
              >
                食
              </span>
              <div className="min-w-0">
                <h1 className="serif t-28 text-[color:var(--teal-deep)] leading-[1.2] consumer-title">
                  这包东西，公开记录里怎么说
                </h1>
                <p className="t-13 text-[color:var(--ink-muted)] pt-1 leading-relaxed max-w-[56ch]">
                  扫一下条码，配料、营养、谁做的，逐条给你看，每条后面都写着出处。
                </p>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 min-w-0 pt-1">
              <AuditChip
                label="本地存储"
                value="不外发"
                title="拍照原图、识别结果、忌口词条与饮食计划只存在这台设备上；不上传、不写日志、不向任何第三方发送"
              />
              <AuditChip
                label="这台设备上"
                value={`${summary.rules} 词条 / ${summary.logs} 记录`}
                title="忌口词条、饮食计划与扫码历史只保存在本浏览器的本地数据库"
              />
            </div>
          </div>

          <nav
            className="flex gap-6 overflow-x-auto border-t border-[color:var(--rule-hair)] tab-strip"
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
        {/* 审计视图的入口说明：只有站在审计页时才解释「这一页是什么」，
            消费者主线上不再挂一条 01 / 02 的编号横条。 */}
        {tab === "dossier" ? (
          <div className="pb-8">
            <p className="t-14 text-[color:var(--ink)] font-semibold">原始记录与出处 · 审计视图</p>
            <p className="t-13 text-[color:var(--ink-muted)] pt-1 leading-relaxed max-w-[62ch]">
              这一页是给核对用的：16 个栏目、每条记录来自哪个来源、原文编号和内容摘要都在这里。
              要给家里人看「这包东西是什么」，回「{TABS[0].label}」即可。
            </p>
          </div>
        ) : null}

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
          <p className="t-13 text-[color:var(--ink)] pb-3">我们把看到的写下来，判断留给你</p>
          <div className="space-y-2 t-13 text-[color:var(--ink-muted)] leading-relaxed max-w-[72ch]">
            <p>
              这里只把各公开来源的原始记录照抄下来、分别列出，不评价好坏，也不推断它们之间有什么关系。
              没有风险评级、真假判断、合格与否的定性，也没有购买建议或功效说法。
            </p>
            <p>
              拍照原图、识别结果、忌口词条与饮食计划 100% 留在这台设备上；不上传服务器，不写日志，不向任何第三方发送。
            </p>
            {activeTab.id === "dossier" ? (
              <p>
                当前页面是审计视图：每个字段都绑着来源、原文编号与采集时间，不设「最终正确答案」这种字段。
              </p>
            ) : null}
          </div>
          <details className="trace mt-5">
            <summary className="trace__summary">
              <span className="trace__glyph" aria-hidden="true">
                <span className="trace__glyph-plus">+</span>
                <span className="trace__glyph-minus">−</span>
              </span>
              <span className="trace__text">运行状态（核对版本时用）</span>
            </summary>
            <div className="trace__body">
              <div className="flex flex-wrap gap-x-6 gap-y-1 mono text-[color:var(--ink-faint)]">
                <span>build_id={buildId || "—"}</span>
                <span>data_origin={dataOrigin}</span>
                <span>cookie.length={cookieCount}</span>
              </div>
            </div>
          </details>
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
