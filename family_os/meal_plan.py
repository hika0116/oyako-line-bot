"""Structured whole-meal candidates and deterministic elapsed-time estimates."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Mapping


MEAL_TYPES = ("定食", "丼", "麺", "鍋", "ワンプレート", "その他")
COMPONENT_KEYS = ("staple", "main", "soup", "side")
READY_RICE_TERMS = (
    "炊いたごはん",
    "炊いたご飯",
    "冷凍ごはん",
    "冷凍ご飯",
    "残りごはん",
    "残りご飯",
    "パックごはん",
    "パックご飯",
    "レトルトごはん",
    "レトルトご飯",
)
PANTRY_STAPLES = ("米", "ごはん", "ご飯", "水")
BASIC_SEASONINGS = (
    "塩",
    "こしょう",
    "砂糖",
    "しょうゆ",
    "醤油",
    "みそ",
    "味噌",
    "酢",
    "みりん",
    "料理酒",
    "サラダ油",
    "ごま油",
    "油",
    "マヨネーズ",
    "ケチャップ",
    "和風だし",
    "だし",
    "コンソメ",
    "鶏がらスープの素",
    "にんにく",
    "しょうが",
    "生姜",
)
SPECIAL_SEASONINGS = (
    "ナンプラー",
    "オイスターソース",
    "コチュジャン",
    "豆板醤",
    "バルサミコ酢",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any, limit: int = 20) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:limit]:
        text = _text(item)
        if text and text not in result:
            result.append(text)
    return result


def _minutes(value: Any) -> int:
    try:
        return max(0, min(120, int(value or 0)))
    except (TypeError, ValueError):
        return 0


def _matches_name(value: str, candidates: list[str] | tuple[str, ...]) -> bool:
    text = _text(value)
    for candidate in candidates:
        if candidate == "油":
            if text == "油" or text.startswith("油 "):
                return True
            continue
        if candidate in text or text in candidate:
            return True
    return False


def _contains_named_term(value: str, candidates: list[str] | tuple[str, ...]) -> bool:
    text = _text(value)
    return any(candidate in text for candidate in candidates)


def is_basic_seasoning(
    ingredient: str,
    non_stocked_seasonings: list[str] | tuple[str, ...] | None = None,
) -> bool:
    """Return whether an ingredient may use the default pantry assumption.

    ``non_stocked_seasonings`` is intentionally an input instead of a database
    dependency so a future profile field can override only selected defaults.
    """

    non_stocked = list(non_stocked_seasonings or [])
    return (
        not _matches_name(ingredient, SPECIAL_SEASONINGS)
        and _matches_name(ingredient, BASIC_SEASONINGS)
        and not _contains_named_term(
            ingredient,
            non_stocked,
        )
    )


def is_pantry_ingredient(
    ingredient: str,
    non_stocked_seasonings: list[str] | tuple[str, ...] | None = None,
) -> bool:
    text = _text(ingredient)
    pantry_staple = any(
        text == item or text.startswith(item)
        for item in PANTRY_STAPLES
    )
    return pantry_staple or is_basic_seasoning(
        ingredient,
        non_stocked_seasonings,
    )


def normalize_shopping_additions(
    additions: list[str],
    *,
    stock_items: list[str] | tuple[str, ...] | None = None,
    non_stocked_seasonings: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    """Remove stock and default seasonings while retaining special purchases."""

    result = []
    stock = list(stock_items or [])
    for addition in additions:
        text = _text(addition)
        if not text or _matches_name(text, stock):
            continue
        if is_basic_seasoning(text, non_stocked_seasonings):
            continue
        if text not in result:
            result.append(text)
    return result


def reconcile_rice_preparation(plan: "MealPlan", stock_items: list[str]) -> None:
    """Synchronize ready-rice versus new-cooking state for display and timing."""

    rice_in_plan = _contains_named_term(
        plan.staple,
        (*READY_RICE_TERMS, "ごはん", "ご飯", "米"),
    )
    if not rice_in_plan:
        plan.rice_cooker_used = False
        plan.ready_rice_used = False
        return

    explicit_ready = next(
        (
            item for item in [plan.staple, *plan.used_stock_items]
            if _contains_named_term(item, READY_RICE_TERMS)
        ),
        None,
    )
    stocked_ready = next(
        (item for item in stock_items if _contains_named_term(item, READY_RICE_TERMS)),
        None,
    )
    ready = explicit_ready or stocked_ready
    if ready:
        plan.ready_rice_used = True
        plan.rice_cooker_used = False
        if plan.staple in {"ごはん", "ご飯", "米"}:
            plan.staple = ready
        if ready not in plan.ingredients:
            plan.ingredients.append(ready)
        if stocked_ready and ready not in plan.used_stock_items:
            plan.used_stock_items.append(ready)
        current_minutes = plan.component_minutes.get("staple", 0)
        plan.component_minutes["staple"] = min(current_minutes, 5) if current_minutes else 5
    else:
        plan.ready_rice_used = False
        plan.rice_cooker_used = True


@dataclass
class MealPlan:
    title: str
    meal_type: str
    staple: str = ""
    main: str = ""
    soup: str = ""
    side: str = ""
    estimated_minutes: int = 0
    shopping_additions: list[str] = field(default_factory=list)
    low_capacity: bool = False
    servings: int | None = None
    ingredients: list[str] = field(default_factory=list)
    used_stock_items: list[str] = field(default_factory=list)
    component_minutes: dict[str, int] = field(default_factory=dict)
    rice_cooker_used: bool = False
    ready_rice_used: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "MealPlan | None":
        if not isinstance(value, Mapping):
            return None
        meal_type = _text(value.get("meal_type"))
        if meal_type not in MEAL_TYPES:
            meal_type = "その他"
        servings = value.get("servings")
        try:
            servings = int(servings) if servings is not None else None
        except (TypeError, ValueError):
            servings = None
        raw_components = value.get("component_minutes") or {}
        components = {
            key: _minutes(raw_components.get(key)) if isinstance(raw_components, Mapping) else 0
            for key in COMPONENT_KEYS
        }
        return cls(
            title=_text(value.get("title")),
            meal_type=meal_type,
            staple=_text(value.get("staple")),
            main=_text(value.get("main")),
            soup=_text(value.get("soup")),
            side=_text(value.get("side")),
            estimated_minutes=_minutes(value.get("estimated_minutes")),
            shopping_additions=_string_list(value.get("shopping_additions"), 10),
            low_capacity=bool(value.get("low_capacity")),
            servings=servings,
            ingredients=_string_list(value.get("ingredients")),
            used_stock_items=_string_list(value.get("used_stock_items"), 10),
            component_minutes=components,
            rice_cooker_used=bool(value.get("rice_cooker_used")),
            ready_rice_used=bool(value.get("ready_rice_used")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def compact_action(self) -> str:
        shopping = "買い足しなし" if not self.shopping_additions else "買い足しあり"
        return f"{self.title}\n   約{self.estimated_minutes}分｜{shopping}"

    def summary(self) -> str:
        lines = [self.title]
        if self.meal_type in {"丼", "麺", "ワンプレート"} and self.main:
            labels = (("主食兼主菜", self.title), ("汁物", self.soup), ("副菜", self.side))
        else:
            labels = (("主食", self.staple), ("主菜", self.main), ("汁物", self.soup), ("副菜", self.side))
        lines.extend(f"{label}：{value}" for label, value in labels if value)
        lines.append(f"目安時間：約{self.estimated_minutes}分")
        additions = "なし" if not self.shopping_additions else "、".join(self.shopping_additions)
        lines.append(f"買い足し：{additions}")
        if self.ready_rice_used:
            lines.append("※炊いたごはん、冷凍ごはん等を使用する場合の時間です。")
        elif self.rice_cooker_used and self.staple:
            lines.append("※炊飯時間は含みません。炊いたごはんがない場合は、別途炊飯時間が必要です。")
        return "\n".join(lines)


def cooking_level_kind(value: str | None) -> str:
    text = _text(value)
    if "初心者" in text:
        return "beginner"
    if "作り置き" in text or "下味冷凍" in text or "かなり得意" in text:
        return "experienced"
    return "standard"


def estimate_elapsed_minutes(plan: MealPlan, cooking_level: str | None = None) -> int:
    """Estimate elapsed table-ready time, not the sum of parallel components."""

    component_times = []
    for key in COMPONENT_KEYS:
        if not getattr(plan, key):
            continue
        minutes = _minutes(plan.component_minutes.get(key))
        if key == "staple" and plan.rice_cooker_used and not plan.ready_rice_used:
            minutes = 0
        if minutes:
            component_times.append(minutes)

    if component_times:
        base = max(component_times)
        if len(component_times) >= 3:
            base += 5
    else:
        base = plan.estimated_minutes or 20

    level = cooking_level_kind(cooking_level)
    if level == "beginner":
        base += 5
    elif level == "experienced" and base >= 15:
        base -= 5

    return max(5, min(120, int(math.ceil(base / 5.0) * 5)))


MEAL_PLAN_SCHEMA: dict[str, Any] = {
    "anyOf": [
        {"type": "null"},
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "meal_type": {"type": "string", "enum": list(MEAL_TYPES)},
                "staple": {"type": "string"},
                "main": {"type": "string"},
                "soup": {"type": "string"},
                "side": {"type": "string"},
                "estimated_minutes": {"type": "integer", "minimum": 0, "maximum": 120},
                "shopping_additions": {
                    "type": "array",
                    "maxItems": 10,
                    "items": {"type": "string"},
                },
                "low_capacity": {"type": "boolean"},
                "servings": {"type": ["integer", "null"], "minimum": 1, "maximum": 20},
                "ingredients": {
                    "type": "array",
                    "maxItems": 20,
                    "items": {"type": "string"},
                },
                "used_stock_items": {
                    "type": "array",
                    "maxItems": 10,
                    "items": {"type": "string"},
                },
                "component_minutes": {
                    "type": "object",
                    "properties": {
                        key: {"type": "integer", "minimum": 0, "maximum": 120}
                        for key in COMPONENT_KEYS
                    },
                    "required": list(COMPONENT_KEYS),
                    "additionalProperties": False,
                },
                "rice_cooker_used": {"type": "boolean"},
                "ready_rice_used": {"type": "boolean"},
            },
            "required": [
                "title",
                "meal_type",
                "staple",
                "main",
                "soup",
                "side",
                "estimated_minutes",
                "shopping_additions",
                "low_capacity",
                "servings",
                "ingredients",
                "used_stock_items",
                "component_minutes",
                "rice_cooker_used",
                "ready_rice_used",
            ],
            "additionalProperties": False,
        },
    ]
}
