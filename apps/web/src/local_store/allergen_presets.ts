/**
 * GB 7718-2025 致敏原预设包。
 *
 * 仅作为「用户可选添加」的本地词条预设，不含任何商品判断。
 * 词条 = 标准中要求提示的致敏原类别名称（机械词条）。
 */
export interface PresetEntry {
  term: string;
  aliases: string[];
  group: string;
}

export const GB7718_ALLERGEN_PRESETS: PresetEntry[] = [
  { term: "含麸质的谷物", aliases: ["麸质", "小麦", "大麦", "黑麦", "燕麦"], group: "谷物" },
  { term: "甲壳类", aliases: ["虾", "蟹", "甲壳类动物"], group: "水产" },
  { term: "鱼类", aliases: ["鱼", "鱼肉"], group: "水产" },
  { term: "蛋类", aliases: ["蛋", "鸡蛋", "蛋制品"], group: "禽蛋" },
  { term: "花生", aliases: ["花生", "花生制品"], group: "豆类与坚果" },
  { term: "大豆", aliases: ["大豆", "黄豆", "大豆制品"], group: "豆类与坚果" },
  { term: "乳及乳制品", aliases: ["乳", "乳粉", "乳糖", "乳清", "乳制品"], group: "乳" },
  { term: "坚果及籽类", aliases: ["坚果", "核桃", "杏仁", "腰果", "榛子", "芝麻"], group: "豆类与坚果" },
  { term: "亚硫酸盐", aliases: ["亚硫酸盐", "二氧化硫"], group: "添加剂" },
];

export const GB7718_ROWS: { term: string; aliases: string[]; group: string }[] = GB7718_ALLERGEN_PRESETS;
