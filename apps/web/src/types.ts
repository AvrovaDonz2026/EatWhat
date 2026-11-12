/** 静态 JSON 契约类型（与 /v1/ 产物一一对应）。 */

export type AccessMode = "OPEN_BULK" | "OPEN_API" | "ACCESS_PENDING" | "SOURCE_UNAVAILABLE";

export type CoverageState =
  | "QUERIED_MATCHED"
  | "QUERIED_NO_MATCH"
  | "QUERY_FAILED"
  | "NOT_QUERIED"
  | "ACCESS_PENDING"
  | "SOURCE_UNAVAILABLE";

export type LinkMode = "EXACT_IDENTIFIER_MATCH" | "MANUAL_CONFIRM";

export type VisibilityEventType =
  | "CURRENTLY_VISIBLE"
  | "HISTORICALLY_OBSERVED"
  | "REMOVED";

export interface SourceManifestEntry {
  source_id: string;
  display_name: string;
  authority: string;
  jurisdiction: string;
  access_mode: AccessMode;
  policy_url: string;
  legal_basis: string;
  query_identifier_types: string[];
  rate_limit: {
    requests_per_minute: number;
    min_interval_ms: number;
    max_concurrency: number;
  };
  notes: string;
}

export interface CoverageEntry {
  source_id: string;
  display_name: string;
  authority: string;
  access_mode: AccessMode;
  coverage_state: CoverageState;
  policy_url: string;
  note: string;
}

export interface Manifest {
  schema_version: string;
  build_id: string;
  built_at: string;
  registry_version: string;
  tool_version: string;
  adapter_versions: Record<string, string>;
  sources: SourceManifestEntry[];
  coverage_summary: Record<string, number>;
  query_index: {
    gtin: string;
    entity_id: string;
    paths: { gtin: string; product: string; ingredients: string };
    candidate_count: number;
  }[];
  no_final_truth_field: boolean;
  no_auto_merge: boolean;
  zero_inference: boolean;
}

export interface MatchCandidate {
  candidate_id: string;
  source_id: string;
  evidence_id: string;
  retrieved_at: string;
  identifier_type: string;
  identifier_value: string;
  record_id: string;
  display_name: string;
  net_content: string;
  pack_type: string;
  link_mode: LinkMode;
  link_reason: string;
}

export interface GtinRecord {
  schema_version: string;
  build_id: string;
  built_at: string;
  gtin: string;
  entity_id: string;
  query_status: "QUERIED_MATCHED" | "QUERIED_NO_MATCH";
  query_status_note: string;
  match_candidates: MatchCandidate[];
  coverage_manifest: CoverageEntry[];
  exact_match_count: number;
  manual_confirm_count: number;
  no_auto_merge_notice: string;
}

export interface DossierField {
  label: string;
  value: unknown;
  raw_value: unknown;
  source_id: string;
  evidence_id: string;
  retrieved_at: string;
}

export interface DossierItem {
  label: string;
  source_id?: string;
  evidence_id?: string;
  retrieved_at?: string;
  raw_value?: unknown;
  [key: string]: unknown;
}

export interface DossierBlock {
  block_id: string;
  title: string;
  state: string;
  note: string;
  fields: DossierField[];
  items: DossierItem[];
}

export interface Observation {
  source_id: string;
  evidence_id: string;
  retrieved_at: string;
  raw_value: string;
  payload_sha256: string;
  adapter_version: string;
}

export interface TimelineEntry {
  evidence_id: string;
  source_id: string;
  retrieved_at: string;
  payload_sha256: string;
  adapter_version: string;
}

export interface VisibilityEvent {
  event_id: string;
  entity_id: string;
  source_id: string;
  evidence_id: string;
  event_type: VisibilityEventType;
  recorded_at: string;
  note: string;
}

export interface ProductDossier {
  schema_version: string;
  build_id: string;
  built_at: string;
  entity_id: string;
  gtin: string;
  observation_policy: {
    no_final_truth_field: boolean;
    no_auto_merge: boolean;
    observations_bound_to: string[];
  };
  coverage_manifest: CoverageEntry[];
  blocks: DossierBlock[];
  block_order: string[];
  observations: Observation[];
  history_timeline: TimelineEntry[];
  source_visibility_events: VisibilityEvent[];
}

export interface IngredientEntry {
  order: number;
  raw_name: string;
  search_keys: string[];
  source_id: string;
  evidence_id: string;
  retrieved_at: string;
  raw_value: string;
  record_id?: string;
}

export interface NutritionEntry {
  nutrient: string;
  raw_nutrient: string;
  raw_value: string;
  evidence_id: string;
  source_id: string;
  retrieved_at: string;
}

export interface AllergenEntry {
  raw_text: string;
  standard_ref: string;
  search_keys: string[];
  source_id: string;
  evidence_id: string;
  retrieved_at: string;
}

export interface UnsuitableGroupEntry {
  raw_text: string;
  source_id: string;
  evidence_id: string;
  retrieved_at: string;
}

export interface IngredientsDocument {
  schema_version: string;
  build_id: string;
  built_at: string;
  entity_id: string;
  gtin: string;
  ingredients: IngredientEntry[];
  nutrition_facts: NutritionEntry[];
  explicit_allergens: AllergenEntry[];
  explicit_unsuitable_groups: UnsuitableGroupEntry[];
  label_standard_refs: {
    standard_no: string;
    source_id: string;
    evidence_id: string;
    retrieved_at: string;
  }[];
  disclaimer: string;
}

/** 端侧离线兜底样例（与 /v1/ 契约同构） */
export interface DemoEntry {
  gtin: string;
  entity_id: string;
  query_status: "QUERIED_MATCHED";
  match_candidates: MatchCandidate[];
  coverage_manifest: CoverageEntry[];
  blocks: DossierBlock[];
  ingredients: IngredientsDocument;
}

export interface DemoIndex {
  schema_version: string;
  build_id: string;
  built_at: string;
  note: string;
  manifest_sources: SourceManifestEntry[];
  entries: DemoEntry[];
}
