/**
 * 端侧机械词条对照引擎。
 *
 * 只做：全半角归一化 → 去空格 → 小写折叠 → 纯子串/别名匹配。
 * 不输出任何风险评分、真伪判断、购买建议或功效评价。
 */
import type { DietRule } from "@/local_store/db";
import type { AllergenEntry, IngredientEntry } from "@/types";

export const MATCH_DISCLAIMER =
    "本提示仅作配料词条机械对照，不构成医疗、营养或购买建议，请以商品实物标签为准。";

/** 全角 → 半角 + 去空白 + 小写折叠。 */
export function normalizeText(input: string): string {
  return input
    .normalize("NFKC")
    .replace(/\s+/g, "")
    .toLowerCase();
}

export interface IngredientHit {
    order: number;
    ingredientRawName: string;
    matchedTerm: string;
    matchedAlias: string;
    matchedNeedle: string;
    evidenceId: string;
    sourceId: string;
}

export interface AllergenHit {
    rawText: string;
    matchedTerm: string;
    matchedNeedle: string;
    evidenceId: string;
    sourceId: string;
    standardRef: string;
}

export interface DietMatchResult {
    ingredientHits: IngredientHit[];
    allergenHits: AllergenHit[];
    disclaimer: string;
}

function needlesFor(rule: DietRule): { term: string; alias: string; needle: string }[] {
    const out: { term: string; alias: string; needle: string }[] = [];
    const term = normalizeText(rule.term);
    if (term) out.push({ term: rule.term, alias: rule.term, needle: term });
    for (const alias of rule.aliases) {
        const normalized = normalizeText(alias);
        if (normalized) out.push({ term: rule.term, alias, needle: normalized });
    }
    return out;
}

/**
 * 对配料与致敏原做纯机械子串/别名匹配。
 * haystack 为配料原文 + search_keys（由构建管线预生成的客观词条）。
 */
export function matchIngredients(
    ingredients: IngredientEntry[],
    allergens: AllergenEntry[],
    rules: DietRule[],
): DietMatchResult {
    const ingredientHits: IngredientHit[] = [];
    const allergenHits: AllergenHit[] = [];

    for (const ingredient of ingredients) {
        const haystacks = [ingredient.raw_name, ...(ingredient.search_keys ?? [])].map(normalizeText);
        for (const rule of rules) {
            for (const needle of needlesFor(rule)) {
                const matched = haystacks.find((hay) => hay.includes(needle.needle));
                if (matched) {
                    ingredientHits.push({
                        order: ingredient.order,
                        ingredientRawName: ingredient.raw_name,
                        matchedTerm: rule.term,
                        matchedAlias: needle.alias,
                        matchedNeedle: matched,
                        evidenceId: ingredient.evidence_id,
                        sourceId: ingredient.source_id,
                    });
                    break; // 同一配料对同一条词条只记一次
                }
            }
        }
    }

    for (const allergen of allergens) {
        const haystacks = [allergen.raw_text, ...(allergen.search_keys ?? [])].map(normalizeText);
        for (const rule of rules) {
            for (const needle of needlesFor(rule)) {
                const matched = haystacks.find((hay) => hay.includes(needle.needle));
                if (matched) {
                    allergenHits.push({
                        rawText: allergen.raw_text,
                        matchedTerm: rule.term,
                        matchedNeedle: matched,
                        evidenceId: allergen.evidence_id,
                        sourceId: allergen.source_id,
                        standardRef: allergen.standard_ref,
                    });
                    break;
                }
            }
        }
    }

    return { ingredientHits, allergenHits, disclaimer: MATCH_DISCLAIMER };
}

/** 对端侧 OCR 提取的配料文本做同一套机械对照（文本分行/分项后逐项匹配）。 */
export function matchOcrText(
    ocrText: string,
    rules: DietRule[],
): { line: string; lineIndex: number; matchedTerm: string; matchedNeedle: string }[] {
    const hits: { line: string; lineIndex: number; matchedTerm: string; matchedNeedle: string }[] = [];
    const lines = ocrText
        .split(/[\n,，、;；。]+/)
        .map((line) => line.trim())
        .filter(Boolean);
    lines.forEach((line, index) => {
        const hay = normalizeText(line);
        if (!hay) return;
        for (const rule of rules) {
            for (const needle of needlesFor(rule)) {
                if (hay.includes(needle.needle)) {
                    hits.push({
                        line,
                        lineIndex: index + 1,
                        matchedTerm: rule.term,
                        matchedNeedle: needle.needle,
                    });
                    break;
                }
            }
        }
    });
    return hits;
}

export function formatHitNotice(hit: IngredientHit): string {
    return `配料表第 ${hit.order} 项「${hit.ingredientRawName}」命中您的本地忌口词条「${hit.matchedTerm}」`;
}
