"""Query planning for template search: topic words, generic words and synonyms.

Two problems this solves, both seen on a real request ("辣子鸡 封面图"):

* **Generic words drown the topic.** Words like 封面 / 模板 / poster describe the
  deliverable, not the subject, and occur in many unrelated records (a UI skeleton's
  content mentions 封面). When every *topic* word of a query misses, ranking on the
  generic words alone returns confident nonsense. So a query is split into topic terms
  and generic terms, and a record must match at least one topic term (or a synonym of
  one) to be returned. A query made only of generic words is not gated.
* **Cross-language recall is zero.** Most worked cases are written in English, users
  ask in Chinese. A small curated table of synonym groups expands a query word into its
  equivalents ("美食" also searches food / dish / cuisine). Expansions score lower than
  the user's own words, and a hit on one counts as a hit on the word it came from.

The tables are deliberately small and hand-kept: every entry is a word people actually
use to ask for a template. Keys are compared after :func:`normalize`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from cfgpu_mcp.template_library.tokenize import is_cjk, normalize, query_terms

#: Weight of a term that only comes from a synonym expansion.
EXPANSION_WEIGHT = 0.8

#: Words that name the deliverable or the request itself, never the subject. A
#: single CJK character is never a topic term anyway, so none is listed here.
GENERIC_WORDS: frozenset[str] = frozenset(
    {
        # deliverable / request
        "模板", "模版", "示例", "案例", "例子", "范例", "参考", "素材", "提示词",
        "风格", "效果", "设计", "制作", "生成", "好看", "高级", "有什么", "什么",
        "我要", "想要", "帮我", "一张", "一份", "一个", "一套",
        "封面", "封面图", "缩略图", "海报", "图片", "图像", "照片", "配图", "主图",
        "视频", "短视频", "横版", "竖版",
        "template", "templates", "example", "examples", "sample", "style", "styles",
        "cover", "thumbnail", "poster", "image", "images", "picture", "photo",
        "design", "video", "prompt",
    }
)

#: Groups of interchangeable words. Every word in a group expands to the others.
SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    # deliverables (generic, expanded so English cases are reachable)
    ("封面", "封面图", "缩略图", "cover", "thumbnail"),
    ("海报", "poster"),
    # food and drink
    ("美食", "食物", "菜品", "菜肴", "料理", "餐饮", "food", "dish", "cuisine", "meal"),
    ("烹饪", "做菜", "菜谱", "食谱", "cooking", "recipe"),
    ("饮品", "饮料", "drink", "beverage"),
    ("咖啡", "coffee"),
    ("茶饮", "奶茶", "tea"),
    ("甜品", "甜点", "dessert"),
    ("蛋糕", "cake"),
    # commerce and brand
    ("产品", "商品", "product"),
    ("电商", "ecommerce", "commerce"),
    ("包装", "packaging", "package"),
    ("品牌", "brand", "branding"),
    ("标志", "logo"),
    ("美妆", "化妆品", "beauty", "cosmetics"),
    ("时尚", "服装", "穿搭", "fashion", "outfit"),
    # subjects and genres
    ("人像", "肖像", "portrait"),
    ("风景", "景观", "landscape", "scenery"),
    ("建筑", "architecture"),
    ("室内", "interior"),
    ("旅行", "旅游", "travel"),
    ("汽车", "car", "automotive"),
    ("宠物", "pet", "pets"),
    ("游戏", "game", "gaming"),
    ("节日", "festival", "holiday"),
    ("婚礼", "wedding"),
    ("摄影", "photography"),
    ("插画", "illustration"),
    ("信息图", "infographic"),
    ("动漫", "二次元", "anime", "manga"),
    ("科普", "science", "explainer"),
    ("教育", "教学", "education", "educational"),
    ("小红书", "xiaohongshu", "rednote"),
    ("国风", "中国风", "中式", "chinese"),
    ("水墨", "ink"),
    ("剪纸", "papercut"),
    ("纸艺", "papercraft"),
    ("赛博朋克", "cyberpunk"),
    ("复古", "retro", "vintage"),
    ("极简", "minimal", "minimalist"),
)


def _build_synonyms() -> dict[str, tuple[str, ...]]:
    table: dict[str, tuple[str, ...]] = {}
    for group in SYNONYM_GROUPS:
        words = tuple(normalize(w) for w in group)
        for word in words:
            others = tuple(w for w in words if w != word)
            table[word] = tuple(dict.fromkeys(table.get(word, ()) + others))
    return table


SYNONYMS = _build_synonyms()
_GENERIC = frozenset(normalize(w) for w in GENERIC_WORDS)


def _occurs(word: str, text: str) -> bool:
    if word.isascii():
        return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text) is not None
    return word in text


def _remove(word: str, text: str) -> str:
    if word.isascii():
        return re.sub(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", " ", text)
    return text.replace(word, " ")


def _strong(terms: list[str]) -> set[str]:
    """A lone CJK character says too little to count as a topic hit."""
    return {t for t in terms if not (len(t) == 1 and is_cjk(t))}


@dataclass
class QueryPlan:
    #: The user's own terms, in order.
    original: list[str]
    #: Every term to score: the user's own plus expansions, each with its weight.
    weights: dict[str, float] = field(default_factory=dict)
    #: Expansion term -> the user's terms it stands for (a hit credits those).
    stands_for: dict[str, set[str]] = field(default_factory=dict)
    #: Terms (own or expanded) of which a result must match one; empty = no gate.
    topic: set[str] = field(default_factory=set)

    @property
    def terms(self) -> list[str]:
        return list(self.weights)

    def credited(self, matched: set[str]) -> set[str]:
        """The user's own terms a set of matched terms counts as hitting."""
        own = {t for t in matched if t in self.original}
        for term in matched:
            own |= self.stands_for.get(term, set())
        return own


def plan_query(query: str) -> QueryPlan:
    text = normalize(query.strip())
    original = query_terms(text)
    plan = QueryPlan(original=original, weights={t: 1.0 for t in original})

    # Topic = what is left once the generic words are taken out (longest first, so
    # 封面图 goes before 封面).
    core = text
    for word in sorted(_GENERIC, key=len, reverse=True):
        if _occurs(word, core):
            core = _remove(word, core)
    plan.topic = _strong(query_terms(core))

    for word in sorted(SYNONYMS, key=len, reverse=True):
        if not _occurs(word, text):
            continue
        own = set(query_terms(word))
        is_topic = word not in _GENERIC
        for synonym in SYNONYMS[word]:
            for term in query_terms(synonym):
                if term not in plan.weights:
                    plan.weights[term] = EXPANSION_WEIGHT
                if term not in original:
                    plan.stands_for.setdefault(term, set()).update(own)
                if is_topic and not (len(term) == 1 and is_cjk(term)):
                    plan.topic.add(term)
    return plan
