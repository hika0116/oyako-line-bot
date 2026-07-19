"""Pure monthly recipe-collection topic planning (no external search)."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence

from .recipe_catalog import MEAL_OCCASIONS, RecipeCatalog


COOKING_METHODS = ("炒める", "煮る", "蒸す", "電子レンジ", "和える", "焼く")


@dataclass(frozen=True)
class CollectionTopic:
    target_ingredients: tuple[str, ...]
    target_meal_occasions: tuple[str, ...]
    target_cooking_methods: tuple[str, ...]
    target_season_month: int
    target_count: int
    priority_score: float
    priority_reasons: tuple[str, ...]
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["target_ingredients"] = list(self.target_ingredients)
        value["target_meal_occasions"] = list(self.target_meal_occasions)
        value["target_cooking_methods"] = list(self.target_cooking_methods)
        value["priority_reasons"] = list(self.priority_reasons)
        return value


def generate_collection_topics(
    *,
    catalog: RecipeCatalog,
    inventory_aggregates: Iterable[Mapping[str, Any]],
    month: int,
    limit: int = 5,
) -> list[CollectionTopic]:
    """Rank aggregate themes without accepting or returning user identifiers."""

    month = min(12, max(1, int(month)))
    published = catalog.published()
    occasion_counts = Counter(
        occasion for recipe in published for occasion in recipe.meal_occasions
    )
    method_counts = Counter(
        method for recipe in published for method in recipe.cooking_method
    )
    lowest_occasion_count = min((occasion_counts[item] for item in MEAL_OCCASIONS), default=0)
    target_occasions = [
        item for item in MEAL_OCCASIONS
        if occasion_counts[item] == lowest_occasion_count
    ] or ["dinner"]
    lowest_method_count = min((method_counts[item] for item in COOKING_METHODS), default=0)
    target_methods = [
        item for item in COOKING_METHODS
        if method_counts[item] == lowest_method_count
    ] or ["炒める"]

    topics = []
    for row in inventory_aggregates:
        ingredient = str(row.get("ingredient_name") or "").strip()
        if not ingredient:
            continue
        demand = max(0.0, float(row.get("household_count") or row.get("demand_count") or 0))
        stale = max(0.0, float(row.get("stale_count") or 0))
        wanted = max(0.0, float(row.get("wanted_count") or 0))
        existing = sum(
            any(ingredient in item.normalized_name or item.normalized_name in ingredient for item in recipe.ingredients)
            for recipe in published
        )
        seasonal = sum(
            1 for recipe in published
            if month in recipe.season_months
            and any(ingredient in item.normalized_name or item.normalized_name in ingredient for item in recipe.ingredients)
        )
        score = demand * 3 + stale * 4 + wanted * 5 + seasonal * 2
        score += max(0, 5 - lowest_occasion_count) * 3
        score += max(0, 5 - lowest_method_count) * 2
        score -= existing * 2
        reasons = [
            f"在庫需要:{demand:g}",
            f"長期滞留:{stale:g}",
            f"利用希望:{wanted:g}",
            f"食事区分不足:{target_occasions[0]}",
            f"調理法不足:{target_methods[0]}",
            f"既存レシピ:{existing}",
        ]
        topics.append(CollectionTopic(
            target_ingredients=(ingredient,),
            target_meal_occasions=(target_occasions[0],),
            target_cooking_methods=(target_methods[0],),
            target_season_month=month,
            target_count=max(1, min(10, 5 - min(existing, 4))),
            priority_score=round(score, 2),
            priority_reasons=tuple(reasons),
        ))
    return sorted(
        topics,
        key=lambda item: (-item.priority_score, item.target_ingredients),
    )[: max(0, limit)]


def public_topic_payload(topic: CollectionTopic) -> dict[str, Any]:
    """Return only aggregate search-theme fields safe for a future provider."""

    return {
        "target_ingredients": list(topic.target_ingredients),
        "target_meal_occasions": list(topic.target_meal_occasions),
        "target_cooking_methods": list(topic.target_cooking_methods),
        "target_season_month": topic.target_season_month,
        "target_count": topic.target_count,
    }


def contains_personal_identifier(payload: Mapping[str, Any]) -> bool:
    forbidden = {"user_id", "profile", "family", "household_details", "line_user_id"}
    return any(key in forbidden for key in payload)
