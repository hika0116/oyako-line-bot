"""Structured whole-meal candidates and deterministic elapsed-time estimates."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Mapping


MEAL_TYPES = ("定食", "丼", "麺", "鍋", "ワンプレート", "その他")
COMPONENT_KEYS = ("staple", "main", "soup", "side")


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
        labels = (("主食", self.staple), ("主菜", self.main), ("汁物", self.soup), ("副菜", self.side))
        lines.extend(f"{label}：{value}" for label, value in labels if value)
        lines.append(f"目安時間：約{self.estimated_minutes}分")
        additions = "なし" if not self.shopping_additions else "、".join(self.shopping_additions)
        lines.append(f"買い足し：{additions}")
        if self.rice_cooker_used and self.staple and not self.ready_rice_used:
            lines.append("※炊飯時間は含みません。")
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
