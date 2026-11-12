/**
 * 端侧离线兜底样例（打包进单文件 HTML）。
 * 结构完全遵循 /v1/ 契约；数值为合成演示数据，不代表任何真实商品。
 */
import type { CoverageEntry, DemoIndex, DossierBlock, IngredientsDocument, MatchCandidate } from "@/types";

const NOW = "2026-01-15T00:00:00Z";

const candidates = (gtin: string): MatchCandidate[] => [
  {
    candidate_id: `cand_lbl_${gtin}`,
    source_id: "label_transcript",
    evidence_id: `ev_demo_lbl_${gtin}`,
    retrieved_at: NOW,
    identifier_type: "GTIN",
    identifier_value: gtin,
    record_id: `LBL-${gtin}-DEMO`,
    display_name: "禾丰全脂乳粉",
    net_content: "400g",
    pack_type: "马口铁罐装",
    link_mode: "EXACT_IDENTIFIER_MATCH",
    link_reason: `GTIN 精确等于查询条码 ${gtin}`,
  },
  {
    candidate_id: `cand_gs1_${gtin}`,
    source_id: "gs1",
    evidence_id: `ev_demo_gs1_${gtin}`,
    retrieved_at: NOW,
    identifier_type: "GTIN",
    identifier_value: gtin,
    record_id: `GS1-${gtin}-DEMO`,
    display_name: "全脂乳粉",
    net_content: "400g",
    pack_type: "马口铁罐装",
    link_mode: "EXACT_IDENTIFIER_MATCH",
    link_reason: `GTIN 精确等于查询条码 ${gtin}`,
  },
  {
    candidate_id: `cand_udi_${gtin}`,
    source_id: "udi",
    evidence_id: `ev_demo_udi_${gtin}`,
    retrieved_at: NOW,
    identifier_type: "UDI_DI",
    identifier_value: `0${gtin}`,
    record_id: `UDI-${gtin}-DEMO`,
    display_name: "禾丰全脂乳粉",
    net_content: "400g/罐",
    pack_type: "",
    link_mode: "EXACT_IDENTIFIER_MATCH",
    link_reason: `UDI_DI 去除前导 0 后精确等于查询条码 ${gtin}`,
  },
];

const coverage: CoverageEntry[] = [
  {
    source_id: "udi",
    display_name: "NMPA UDI / 备案公开记录（演示夹具）",
    authority: "国家药品监督管理局",
    access_mode: "OPEN_API",
    coverage_state: "QUERIED_MATCHED",
    policy_url: "https://www.nmpa.gov.cn/",
    note: "本次查询取得记录（演示夹具）",
  },
  {
    source_id: "gs1",
    display_name: "GS1 商品条码公开信息",
    authority: "GS1 China",
    access_mode: "OPEN_BULK",
    coverage_state: "QUERIED_MATCHED",
    policy_url: "https://www.gs1cn.org/",
    note: "本次查询取得记录（演示夹具）",
  },
  {
    source_id: "label_transcript",
    display_name: "包装标签文字转录（构建机录入）",
    authority: "原始包装标签（人工转录）",
    access_mode: "OPEN_BULK",
    coverage_state: "QUERIED_MATCHED",
    policy_url: "https://example.org/label-transcript-policy",
    note: "本次查询取得记录（演示夹具）",
  },
  {
    source_id: "samr_gov",
    display_name: "市场监管总局 食品生产许可公开信息",
    authority: "国家市场监督管理总局",
    access_mode: "OPEN_API",
    coverage_state: "QUERIED_NO_MATCH",
    policy_url: "https://www.samr.gov.cn/",
    note: "本次查询未取得记录（演示夹具）",
  },
  {
    source_id: "samr_std",
    display_name: "国家标准全文公开系统",
    authority: "国家市场监督管理总局",
    access_mode: "OPEN_BULK",
    coverage_state: "QUERIED_NO_MATCH",
    policy_url: "https://openstd.samr.gov.cn/",
    note: "本次查询未取得记录（演示夹具）",
  },
  {
    source_id: "cnipa",
    display_name: "国家知识产权局 商标公开查询",
    authority: "国家知识产权局",
    access_mode: "ACCESS_PENDING",
    coverage_state: "ACCESS_PENDING",
    policy_url: "https://sbj.cnipa.gov.cn/",
    note: "该数据源接入待授权，本档案不含其记录",
  },
  {
    source_id: "cifer",
    display_name: "海关总署 进口食品化妆品进出口商备案（CIFER）",
    authority: "海关总署",
    access_mode: "SOURCE_UNAVAILABLE",
    coverage_state: "SOURCE_UNAVAILABLE",
    policy_url: "https://cifer.example.customs.gov.cn/",
    note: "该数据源当前不可稳定访问，本档案不含其记录",
  },
];

const ingredientsDoc = (gtin: string, entityId: string): IngredientsDocument => ({
  schema_version: "1.0",
  build_id: "demo-baseline",
  built_at: NOW,
  entity_id: entityId,
  gtin,
  ingredients: [
    {
      order: 1,
      raw_name: "全脂乳粉",
      search_keys: ["全脂乳粉", "乳粉", "乳制品", "乳糖", "乳", "牛乳"],
      source_id: "label_transcript",
      evidence_id: `ev_demo_lbl_${gtin}`,
      retrieved_at: NOW,
      raw_value: "全脂乳粉",
      record_id: `LBL-${gtin}-DEMO`,
    },
    {
      order: 2,
      raw_name: "乳清蛋白粉",
      search_keys: ["乳清蛋白粉", "乳清", "乳制品", "乳糖", "乳", "牛乳"],
      source_id: "label_transcript",
      evidence_id: `ev_demo_lbl_${gtin}`,
      retrieved_at: NOW,
      raw_value: "乳清蛋白粉",
      record_id: `LBL-${gtin}-DEMO`,
    },
    {
      order: 3,
      raw_name: "磷脂",
      search_keys: ["磷脂", "大豆磷脂", "大豆"],
      source_id: "label_transcript",
      evidence_id: `ev_demo_lbl_${gtin}`,
      retrieved_at: NOW,
      raw_value: "磷脂",
      record_id: `LBL-${gtin}-DEMO`,
    },
  ],
  nutrition_facts: [
    { nutrient: "能量", raw_nutrient: "能量", raw_value: "2130千焦/100克", evidence_id: `ev_demo_lbl_${gtin}`, source_id: "label_transcript", retrieved_at: NOW },
    { nutrient: "蛋白质", raw_nutrient: "蛋白质", raw_value: "24.5克/100克", evidence_id: `ev_demo_lbl_${gtin}`, source_id: "label_transcript", retrieved_at: NOW },
    { nutrient: "脂肪", raw_nutrient: "脂肪", raw_value: "26.8克/100克", evidence_id: `ev_demo_lbl_${gtin}`, source_id: "label_transcript", retrieved_at: NOW },
    { nutrient: "碳水化合物", raw_nutrient: "碳水化合物", raw_value: "39.6克/100克", evidence_id: `ev_demo_lbl_${gtin}`, source_id: "label_transcript", retrieved_at: NOW },
    { nutrient: "钠", raw_nutrient: "钠", raw_value: "280毫克/100克", evidence_id: `ev_demo_lbl_${gtin}`, source_id: "label_transcript", retrieved_at: NOW },
    { nutrient: "钙", raw_nutrient: "钙", raw_value: "800毫克/100克", evidence_id: `ev_demo_lbl_${gtin}`, source_id: "label_transcript", retrieved_at: NOW },
  ],
  explicit_allergens: [
    {
      raw_text: "含有乳及乳制品（包括乳糖）",
      standard_ref: "GB 7718-2025",
      search_keys: ["乳", "乳制品", "乳糖", "乳蛋白"],
      source_id: "label_transcript",
      evidence_id: `ev_demo_lbl_${gtin}`,
      retrieved_at: NOW,
    },
  ],
  explicit_unsuitable_groups: [
    {
      raw_text: "对乳蛋白过敏者不宜食用",
      source_id: "label_transcript",
      evidence_id: `ev_demo_lbl_${gtin}`,
      retrieved_at: NOW,
    },
  ],
  label_standard_refs: [
    {
      standard_no: "GB 7718-2025",
      source_id: "label_transcript",
      evidence_id: `ev_demo_lbl_${gtin}`,
      retrieved_at: NOW,
    },
  ],
  disclaimer: "本文件为纯客观配料与营养原文数据，供端侧机械词条对照；不含任何评价或建议",
});

const blocks = (gtin: string): DossierBlock[] => [
  {
    block_id: "identity_basic",
    title: "基本信息（条码/品牌/商品名/净含量/包装）",
    state: "FILLED",
    note: "以下基本信息来自各数据源原始记录，按来源并列列出",
    fields: [
      { label: "商品名称", value: "全脂乳粉", raw_value: "全脂乳粉", source_id: "gs1", evidence_id: `ev_demo_gs1_${gtin}`, retrieved_at: NOW },
      { label: "商品名称", value: "禾丰全脂乳粉", raw_value: "禾丰全脂乳粉", source_id: "udi", evidence_id: `ev_demo_udi_${gtin}`, retrieved_at: NOW },
      { label: "净含量", value: "400g", raw_value: "400g", source_id: "gs1", evidence_id: `ev_demo_gs1_${gtin}`, retrieved_at: NOW },
      { label: "包装形式", value: "马口铁罐装", raw_value: "马口铁罐装", source_id: "gs1", evidence_id: `ev_demo_gs1_${gtin}`, retrieved_at: NOW },
      { label: "条码（GTIN）", value: gtin, raw_value: gtin, source_id: "gs1", evidence_id: `ev_demo_gs1_${gtin}`, retrieved_at: NOW },
    ],
    items: [],
  },
  {
    block_id: "observations",
    title: "多源观测并列",
    state: "FILLED",
    note: "各来源观测并列保存，每条记录绑定 source_id / evidence_id / retrieved_at / raw_value；不设最终真相字段",
    fields: [],
    items: [
      { label: "来源观测", source_id: "gs1", evidence_id: `ev_demo_gs1_${gtin}`, retrieved_at: NOW, raw_value: '{"records":[{"record_id":"GS1-DEMO"}]}' },
      { label: "来源观测", source_id: "udi", evidence_id: `ev_demo_udi_${gtin}`, retrieved_at: NOW, raw_value: '{"records":[{"record_id":"UDI-DEMO"}]}' },
    ],
  },
];

const ENTITY_IDS: Record<string, string> = {
  "6901234567892": "ent_demo_0fdf238c",
  "6905554443331": "ent_demo_f2591797",
};

const demoEntry = (gtin: string) => ({
  gtin,
  entity_id: ENTITY_IDS[gtin] ?? `ent_demo_${gtin.slice(-8)}`,
  query_status: "QUERIED_MATCHED" as const,
  match_candidates: candidates(gtin),
  coverage_manifest: coverage,
  blocks: blocks(gtin),
  ingredients: ingredientsDoc(gtin, ENTITY_IDS[gtin] ?? `ent_demo_${gtin.slice(-8)}`),
});

export const DEMO_FALLBACK: DemoIndex = {
  schema_version: "1.0",
  build_id: "demo-baseline",
  built_at: NOW,
  note: "端侧离线兜底样例：仅用于无静态库时的数据结构演示，全部字段与 /v1/ 契约一致",
  manifest_sources: [],
  entries: [demoEntry("6901234567892"), demoEntry("6905554443331")],
};
