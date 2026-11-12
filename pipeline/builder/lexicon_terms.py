"""跨源机械词条词典。

只做「词条 → 检索键」的静态映射，不含任何关于商品本身的判断。
用于生成 /v1/ingredients/*.json 的 search_keys，供端侧本地忌口匹配使用。
"""
from __future__ import annotations

from typing import Dict, List, Tuple

#: 配料原文关键词 → 检索键（纯词条映射）
INGREDIENT_TERM_KEYS: Dict[str, Tuple[str, ...]] = {
    "全脂乳粉": ("乳粉", "乳制品", "乳糖", "乳", "牛乳"),
    "脱脂乳粉": ("乳粉", "乳制品", "乳糖", "乳", "牛乳"),
    "乳清蛋白粉": ("乳清", "乳制品", "乳糖", "乳", "牛乳"),
    "生牛乳": ("牛乳", "乳制品", "乳糖", "乳"),
    "乳糖": ("乳糖", "乳制品", "乳"),
    "奶油": ("乳制品", "乳", "乳脂"),
    "黄油": ("乳制品", "乳", "乳脂"),
    "干酪": ("乳制品", "乳", "奶酪"),
    "磷脂": ("大豆磷脂", "磷脂", "大豆"),
    "大豆磷脂": ("大豆", "大豆制品", "磷脂"),
    "大豆分离蛋白": ("大豆", "大豆制品", "豆类"),
    "大豆油": ("大豆", "大豆制品", "豆类"),
    "燕麦": ("燕麦", "麸质", "谷物", "含麸质谷物"),
    "小麦粉": ("小麦", "麸质", "谷物", "含麸质谷物"),
    "大麦": ("大麦", "麸质", "谷物"),
    "黑麦": ("黑麦", "麸质", "谷物"),
    "花生": ("花生", "花生制品", "坚果"),
    "核桃": ("核桃", "坚果", "树坚果"),
    "杏仁": ("杏仁", "坚果", "树坚果"),
    "腰果": ("腰果", "坚果", "树坚果"),
    "榛子": ("榛子", "坚果", "树坚果"),
    "芝麻": ("芝麻", "芝麻制品"),
    "鸡蛋": ("蛋", "蛋制品", "鸡蛋"),
    "蛋清粉": ("蛋", "蛋制品", "鸡蛋"),
    "鱼类": ("鱼", "鱼类", "鱼肉"),
    "虾": ("甲壳类", "虾", "水产"),
    "蟹": ("甲壳类", "蟹", "水产"),
    "亚硫酸盐": ("亚硫酸盐", "二氧化硫", "防腐剂"),
    "碳酸钙": ("钙", "营养强化剂"),
    "食用盐": ("食盐", "盐", "钠"),
    "白砂糖": ("糖", "蔗糖", "白砂糖"),
    "植物油": ("植物油", "油脂"),
}

#: 致敏原原文关键词 → 检索键
ALLERGEN_TERM_KEYS: Dict[str, Tuple[str, ...]] = {
    "乳": ("乳", "乳制品", "乳糖", "乳蛋白"),
    "乳制品": ("乳", "乳制品", "乳糖", "乳蛋白"),
    "乳糖": ("乳糖", "乳", "乳制品"),
    "麸质": ("麸质", "含麸质谷物", "谷物"),
    "燕麦": ("燕麦", "麸质", "含麸质谷物"),
    "大豆": ("大豆", "大豆制品", "豆类"),
    "花生": ("花生", "花生制品"),
    "坚果": ("坚果", "树坚果"),
    "蛋": ("蛋", "蛋制品"),
    "鱼": ("鱼", "鱼类"),
    "甲壳类": ("甲壳类", "虾", "蟹"),
    "亚硫酸盐": ("亚硫酸盐", "二氧化硫"),
    "芝麻": ("芝麻", "芝麻制品"),
}

#: 营养素 → 规范名与单位（仅归一化原文，不做换算结论）
NUTRIENT_CANONICAL: Dict[str, str] = {
    "能量": "能量",
    "蛋白质": "蛋白质",
    "脂肪": "脂肪",
    "饱和脂肪": "饱和脂肪",
    "反式脂肪酸": "反式脂肪酸",
    "碳水化合物": "碳水化合物",
    "糖": "糖",
    "膳食纤维": "膳食纤维",
    "钠": "钠",
    "钙": "钙",
    "铁": "铁",
    "锌": "锌",
    "维生素A": "维生素A",
    "维生素D": "维生素D",
    "维生素C": "维生素C",
}


def search_keys_for_ingredient(raw_name: str) -> List[str]:
    """机械展开：命中词典则给出检索键，否则给出去括号后的原文本身。"""
    name = raw_name.strip()
    keys = list(INGREDIENT_TERM_KEYS.get(name, ()))
    if not keys:
        for term, aliases in INGREDIENT_TERM_KEYS.items():
            if term and term in name:
                keys.extend(aliases)
                break
    if name not in keys:
        keys.insert(0, name)
    return list(dict.fromkeys(keys))


def search_keys_for_allergen(raw_text: str) -> List[str]:
    keys: List[str] = []
    for term, aliases in ALLERGEN_TERM_KEYS.items():
        if term in raw_text:
            keys.extend(aliases)
    return list(dict.fromkeys(keys))


def canonical_nutrient(raw_name: str) -> str:
    return NUTRIENT_CANONICAL.get(raw_name.strip(), raw_name.strip())


__all__ = [
    "ALLERGEN_TERM_KEYS",
    "INGREDIENT_TERM_KEYS",
    "NUTRIENT_CANONICAL",
    "canonical_nutrient",
    "search_keys_for_allergen",
    "search_keys_for_ingredient",
]
