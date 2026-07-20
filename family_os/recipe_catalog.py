"""Deterministic recipe catalog, meal composition, scaling, and rendering.

The catalog is the source of truth for names, ingredients, quantities, steps,
equipment, time, and sources.  The application may rank or combine published
recipes, but it must not invent a missing recipe to fill a result slot.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from .meal_plan import READY_RICE_TERMS, MealPlan, cooking_level_kind, is_pantry_ingredient


MEAL_OCCASIONS = ("breakfast", "lunch", "bento", "dinner", "otsumami")
DISH_ROLES = ("staple", "main", "side", "soup", "one_dish", "staple_and_main")
DEFAULT_SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "recipes_seed_v1.json"
RECENT_PROPOSAL_PENALTY_SECONDS = 30 * 24 * 60 * 60
RECENT_SELECTION_PENALTY_SECONDS = 7 * 24 * 60 * 60
STRONG_RECENT_PROPOSAL_COUNT = 3


@dataclass(frozen=True)
class RecipeIngredient:
    ingredient_name: str
    normalized_name: str
    quantity: float | None
    unit: str
    scaling_mode: str = "linear"
    rounding_increment: float | None = None
    minimum_quantity: float | None = None
    optional: bool = False
    basic_seasoning: bool = False
    substitutes: tuple[str, ...] = ()
    sort_order: int = 0

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RecipeIngredient":
        return cls(
            ingredient_name=str(value.get("ingredient_name") or "").strip(),
            normalized_name=str(value.get("normalized_name") or value.get("ingredient_name") or "").strip(),
            quantity=_number_or_none(value.get("quantity")),
            unit=str(value.get("unit") or "").strip(),
            scaling_mode=str(value.get("scaling_mode") or "linear").strip(),
            rounding_increment=_number_or_none(value.get("rounding_increment")),
            minimum_quantity=_number_or_none(value.get("minimum_quantity")),
            optional=bool(value.get("optional")),
            basic_seasoning=bool(value.get("basic_seasoning")),
            substitutes=tuple(_string_list(value.get("substitutes"))),
            sort_order=int(value.get("sort_order") or 0),
        )


@dataclass(frozen=True)
class RecipeStep:
    step_number: int
    instruction: str
    duration_minutes: int = 0
    equipment: tuple[str, ...] = ()
    parallel_group: str = ""
    depends_on_steps: tuple[int, ...] = ()
    can_parallelize: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RecipeStep":
        return cls(
            step_number=int(value.get("step_number") or 0),
            instruction=str(value.get("instruction") or "").strip(),
            duration_minutes=max(0, int(value.get("duration_minutes") or 0)),
            equipment=tuple(_string_list(value.get("equipment"))),
            parallel_group=str(value.get("parallel_group") or "").strip(),
            depends_on_steps=tuple(int(item) for item in (value.get("depends_on_steps") or [])),
            can_parallelize=bool(value.get("can_parallelize")),
        )


@dataclass(frozen=True)
class RecipeSource:
    source_name: str
    source_url: str = ""
    source_type: str = "internal"
    license_or_usage_note: str = ""
    checked_at: str = ""
    source_role: str = "reference"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RecipeSource":
        return cls(
            source_name=str(value.get("source_name") or "").strip(),
            source_url=str(value.get("source_url") or "").strip(),
            source_type=str(value.get("source_type") or "internal").strip(),
            license_or_usage_note=str(value.get("license_or_usage_note") or "").strip(),
            checked_at=str(value.get("checked_at") or "").strip(),
            source_role=str(value.get("source_role") or "reference").strip(),
        )


@dataclass(frozen=True)
class Recipe:
    id: str
    title: str
    summary: str
    base_servings: int
    dish_roles: tuple[str, ...]
    meal_occasions: tuple[str, ...]
    cuisine: str
    cooking_method: tuple[str, ...]
    total_minutes: int
    active_minutes: int
    difficulty: str
    low_energy: bool
    bento_suitable: bool
    leak_risk: str
    make_ahead_possible: bool
    season_months: tuple[int, ...]
    tags: tuple[str, ...]
    content_status: str
    source_type: str
    ingredients: tuple[RecipeIngredient, ...] = ()
    steps: tuple[RecipeStep, ...] = ()
    sources: tuple[RecipeSource, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Recipe":
        return cls(
            id=str(value.get("id") or value.get("recipe_id") or "").strip(),
            title=str(value.get("title") or "").strip(),
            summary=str(value.get("summary") or "").strip(),
            base_servings=max(1, int(value.get("base_servings") or 2)),
            dish_roles=tuple(item for item in _string_list(value.get("dish_roles")) if item in DISH_ROLES),
            meal_occasions=tuple(item for item in _string_list(value.get("meal_occasions")) if item in MEAL_OCCASIONS),
            cuisine=str(value.get("cuisine") or "").strip(),
            cooking_method=tuple(_string_list(value.get("cooking_method"))),
            total_minutes=max(1, int(value.get("total_minutes") or 1)),
            active_minutes=max(0, int(value.get("active_minutes") or 0)),
            difficulty=str(value.get("difficulty") or "standard").strip(),
            low_energy=bool(value.get("low_energy")),
            bento_suitable=bool(value.get("bento_suitable")),
            leak_risk=str(value.get("leak_risk") or "low").strip(),
            make_ahead_possible=bool(value.get("make_ahead_possible")),
            season_months=tuple(int(item) for item in (value.get("season_months") or []) if 1 <= int(item) <= 12),
            tags=tuple(_string_list(value.get("tags"))),
            content_status=str(value.get("content_status") or "draft").strip(),
            source_type=str(value.get("source_type") or "internal").strip(),
            ingredients=tuple(sorted(
                (
                    RecipeIngredient.from_mapping(item)
                    for item in (
                        value.get("recipe_ingredients")
                        or value.get("ingredients")
                        or []
                    )
                    if isinstance(item, Mapping)
                ),
                key=lambda item: item.sort_order,
            )),
            steps=tuple(sorted(
                (
                    RecipeStep.from_mapping(item)
                    for item in (
                        value.get("recipe_steps")
                        or value.get("steps")
                        or []
                    )
                    if isinstance(item, Mapping)
                ),
                key=lambda item: item.step_number,
            )),
            sources=tuple(
                RecipeSource.from_mapping(item)
                for item in (value.get("recipe_sources") or value.get("sources") or [])
                if isinstance(item, Mapping)
            ),
        )

    def to_component(self) -> dict[str, Any]:
        return {
            "recipe_id": self.id,
            "title": self.title,
            "summary": self.summary,
            "base_servings": self.base_servings,
            "dish_roles": list(self.dish_roles),
            "meal_occasions": list(self.meal_occasions),
            "cuisine": self.cuisine,
            "total_minutes": self.total_minutes,
            "active_minutes": self.active_minutes,
            "cooking_method": list(self.cooking_method),
            "difficulty": self.difficulty,
            "low_energy": self.low_energy,
            "bento_suitable": self.bento_suitable,
            "leak_risk": self.leak_risk,
            "make_ahead_possible": self.make_ahead_possible,
            "season_months": list(self.season_months),
            "tags": list(self.tags),
            "content_status": self.content_status,
            "source_type": self.source_type,
            "ingredients": [asdict(item) for item in self.ingredients],
            "steps": [asdict(item) for item in self.steps],
            "sources": [asdict(item) for item in self.sources],
        }


class RecipeCatalog:
    def __init__(self, recipes: Iterable[Recipe] = ()) -> None:
        self.recipes = tuple(recipe for recipe in recipes if recipe.id and recipe.title)
        self.by_id = {recipe.id: recipe for recipe in self.recipes}

    @classmethod
    def from_json(cls, path: str | Path = DEFAULT_SEED_PATH) -> "RecipeCatalog":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        values = raw.get("recipes") if isinstance(raw, Mapping) else raw
        return cls(
            Recipe.from_mapping(item)
            for item in (values or [])
            if isinstance(item, Mapping)
        )

    @classmethod
    def from_supabase(cls, supabase: Any) -> "RecipeCatalog":
        if supabase is None:
            return cls()
        result = (
            supabase.table("recipes")
            .select("*,recipe_ingredients(*),recipe_steps(*),recipe_sources(*)")
            .eq("content_status", "published")
            .limit(250)
            .execute()
        )
        return cls(
            Recipe.from_mapping(item)
            for item in (getattr(result, "data", None) or [])
            if isinstance(item, Mapping)
        )

    def published(self, meal_occasion: str | None = None) -> list[Recipe]:
        return [
            recipe for recipe in self.recipes
            if recipe.content_status == "published"
            and (not meal_occasion or meal_occasion in recipe.meal_occasions)
        ]


def _number_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def normalize_ingredient_name(value: str) -> str:
    text = re.sub(r"[\s　()（）・]", "", str(value or "")).lower()
    aliases = (
        (r"(?:鶏|とり)(?:もも|むね|胸)?肉", "鶏肉"),
        (r"豚(?:こま|小間|薄切り|バラ)?肉", "豚肉"),
        (r"たまねぎ", "玉ねぎ"),
        (r"ナス", "なす"),
        (r"ご飯", "ごはん"),
    )
    for pattern, replacement in aliases:
        text = re.sub(pattern, replacement, text)
    return text


def stock_item_names(stocks: Iterable[str]) -> list[str]:
    result = []
    for stock in stocks:
        name = re.split(r"\s|\d", str(stock or "").strip(), maxsplit=1)[0].rstrip(":：")
        if name and name not in result:
            result.append(name)
    return result


def ingredient_matches_stock(ingredient: str, stocks: Sequence[str]) -> bool:
    name = normalize_ingredient_name(ingredient)
    return any(
        name in normalize_ingredient_name(stock) or normalize_ingredient_name(stock) in name
        for stock in stocks
        if normalize_ingredient_name(stock)
    )


def scale_quantity(ingredient: RecipeIngredient, base_servings: int, servings: int) -> float | None:
    """Scale one quantity with a deterministic, testable mode."""

    if ingredient.quantity is None:
        return None
    ratio = max(1, servings) / max(1, base_servings)
    mode = ingredient.scaling_mode
    if mode == "count":
        scaled = ingredient.quantity * ratio
    elif mode == "seasoning":
        scaled = ingredient.quantity * (ratio ** 0.8)
    elif mode == "mostly_fixed":
        scaled = ingredient.quantity * (0.8 + 0.2 * ratio)
    elif mode == "optional":
        scaled = ingredient.quantity
    else:
        scaled = ingredient.quantity * ratio

    increment = ingredient.rounding_increment
    if increment and increment > 0:
        scaled = math.ceil((scaled - 1e-9) / increment) * increment
    if ingredient.minimum_quantity is not None:
        scaled = max(scaled, ingredient.minimum_quantity)
    return round(scaled, 4)


def format_quantity(value: float | None, unit: str) -> str:
    if value is None:
        return "適量" if not unit else unit
    if unit in {"g", "ml"} and value >= 20:
        value = round(value / 5) * 5
    elif unit in {"大さじ", "小さじ"}:
        value = round(value * 4) / 4
    elif unit in {"個", "丁", "玉", "枚", "本", "袋", "束", "切れ"}:
        value = round(value * 2) / 2
    else:
        value = round(value, 1)
    if float(value).is_integer():
        shown = str(int(value))
    elif value == 0.25:
        shown = "1/4"
    elif value == 0.5:
        shown = "1/2"
    elif value == 0.75:
        shown = "3/4"
    else:
        shown = f"{value:g}"
    return f"{shown}{unit}"


def scaled_ingredients(recipe: Recipe, servings: int) -> list[dict[str, Any]]:
    return [
        {
            "name": item.ingredient_name,
            "normalized_name": item.normalized_name,
            "quantity": scale_quantity(item, recipe.base_servings, servings),
            "unit": item.unit,
            "display_quantity": format_quantity(
                scale_quantity(item, recipe.base_servings, servings), item.unit
            ),
            "optional": item.optional,
            "basic_seasoning": item.basic_seasoning,
            "scaling_mode": item.scaling_mode,
        }
        for item in recipe.ingredients
    ]


def _excluded(recipe: Recipe, exclusions: Sequence[str]) -> bool:
    text = " ".join([recipe.title, *(item.ingredient_name for item in recipe.ingredients)])
    return any(term and term in text for term in exclusions)


def _available_and_additions(
    recipes: Sequence[Recipe],
    stocks: Sequence[str],
    non_stocked_seasonings: Sequence[str] = (),
) -> tuple[list[str], list[str]]:
    used: list[str] = []
    additions: list[str] = []
    for recipe in recipes:
        for item in recipe.ingredients:
            if item.optional:
                continue
            if ingredient_matches_stock(item.normalized_name, stocks):
                stock = next(
                    stock for stock in stocks
                    if ingredient_matches_stock(item.normalized_name, [stock])
                )
                if stock not in used:
                    used.append(stock)
                continue
            if item.basic_seasoning and is_pantry_ingredient(
                item.normalized_name, list(non_stocked_seasonings)
            ):
                continue
            if is_pantry_ingredient(item.normalized_name, list(non_stocked_seasonings)):
                continue
            if item.ingredient_name not in additions:
                additions.append(item.ingredient_name)
    return used, additions


def _recipe_score(
    recipe: Recipe,
    *,
    stocks: Sequence[str],
    low_capacity: bool,
    month: int,
    max_minutes: int | None,
    recent_history: Sequence[Mapping[str, Any]],
    preference_terms: Sequence[str],
) -> float:
    major = [item for item in recipe.ingredients if not item.basic_seasoning and not item.optional]
    matches = sum(ingredient_matches_stock(item.normalized_name, stocks) for item in major)
    score = matches * 18 - max(0, len(major) - matches) * 5
    score += max(0, 25 - recipe.total_minutes) * 0.3
    if month in recipe.season_months:
        score += 4
    if low_capacity:
        score += 25 if recipe.low_energy else -30
        if recipe.total_minutes > 20:
            score -= 30
    if max_minutes is not None and recipe.total_minutes > max_minutes:
        score -= 60
    text = " ".join([
        recipe.title,
        *recipe.tags,
        *recipe.cooking_method,
        *(item.normalized_name for item in recipe.ingredients),
    ])
    if any(term in preference_terms for term in ("がっつり", "ボリューム", "肉を使いたい")):
        if re.search(r"(豚肉|鶏肉|牛肉|魚|鮭|卵|豆腐)", text):
            score += 10
    if "あっさり" in preference_terms and re.search(r"(蒸す|煮る|和える)", text):
        score += 8
    if "野菜を多め" in preference_terms:
        score += sum(
            bool(re.search(r"(白菜|なす|ズッキーニ|じゃがいも|玉ねぎ|野菜)", item.normalized_name))
            for item in recipe.ingredients
        ) * 3
    now = datetime.now(timezone.utc).timestamp()
    recent_proposals = 0
    for item in recent_history:
        if str(item.get("recipe_id") or "") != recipe.id:
            continue
        proposed_at = _timestamp(item.get("proposed_at"))
        selected_at = _timestamp(item.get("selected_at"))
        if proposed_at and now - proposed_at <= RECENT_PROPOSAL_PENALTY_SECONDS:
            recent_proposals += 1
        if selected_at and now - selected_at <= RECENT_SELECTION_PENALTY_SECONDS:
            score -= 20
    if recent_proposals:
        score -= 30 if recent_proposals >= STRONG_RECENT_PROPOSAL_COUNT else 12 * recent_proposals
    return score


def _timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _ranked(
    catalog: RecipeCatalog,
    occasion: str,
    *,
    roles: Sequence[str],
    stocks: Sequence[str],
    exclusions: Sequence[str],
    low_capacity: bool,
    month: int,
    max_minutes: int | None,
    recent_history: Sequence[Mapping[str, Any]],
    preference_terms: Sequence[str] = (),
) -> list[Recipe]:
    recipes = []
    for recipe in catalog.published(occasion):
        if not any(role in recipe.dish_roles for role in roles) or _excluded(recipe, exclusions):
            continue
        if occasion == "bento" and (not recipe.bento_suitable or recipe.leak_risk == "high"):
            continue
        if low_capacity and (not recipe.low_energy or recipe.total_minutes > 20):
            continue
        if max_minutes is not None and recipe.total_minutes > max_minutes:
            continue
        if stocks and not any(
            ingredient_matches_stock(item.normalized_name, stocks)
            for item in recipe.ingredients
            if not item.basic_seasoning
        ):
            continue
        recipes.append(recipe)
    return sorted(
        recipes,
        key=lambda recipe: (
            -_recipe_score(
                recipe,
                stocks=stocks,
                low_capacity=low_capacity,
                month=month,
                max_minutes=max_minutes,
                recent_history=recent_history,
                preference_terms=preference_terms,
            ),
            recipe.total_minutes,
            recipe.title,
        ),
    )


def _role_for_recipe(recipe: Recipe, preferred: str) -> str:
    if preferred in recipe.dish_roles:
        return preferred
    if "one_dish" in recipe.dish_roles or "staple_and_main" in recipe.dish_roles:
        return "one_dish"
    return next(iter(recipe.dish_roles), preferred)


def _meal_type(occasion: str, recipe: Recipe, component_count: int) -> str:
    if "麺" in recipe.tags or any("麺" in item for item in recipe.cooking_method) or re.search(r"(うどん|パスタ|そば|麺)", recipe.title):
        return "麺"
    if "丼" in recipe.tags or "丼" in recipe.title:
        return "丼"
    if occasion == "bento":
        return "その他"
    if component_count >= 2:
        return "定食"
    return "ワンプレート" if "one_dish" in recipe.dish_roles else "その他"


def _elapsed(recipes: Sequence[Recipe], cooking_level: str | None) -> int:
    base = max((recipe.total_minutes for recipe in recipes), default=10)
    if len(recipes) >= 3:
        base += 5
    level = cooking_level_kind(cooking_level)
    if level == "beginner":
        base += 5
    elif level == "experienced" and base >= 15:
        base -= 5
    return max(5, int(math.ceil(base / 5) * 5))


def _uses_rice_cooker(recipe: Recipe) -> bool:
    return (
        "staple" in recipe.dish_roles
        and "米" in {item.normalized_name for item in recipe.ingredients}
        and "炊く" in recipe.cooking_method
    )


def _elapsed_for_components(
    components: Mapping[str, Recipe], cooking_level: str | None
) -> int:
    """Exclude passive rice-cooker time from the displayed meal duration."""

    timed_recipes = [
        recipe
        for role, recipe in components.items()
        if not (role == "staple" and _uses_rice_cooker(recipe))
    ]
    return _elapsed(timed_recipes, cooking_level)


def compose_meal_plans(
    catalog: RecipeCatalog,
    *,
    meal_occasion: str,
    stocks: Sequence[str] = (),
    exclusions: Sequence[str] = (),
    low_capacity: bool = False,
    cooking_level: str | None = None,
    max_minutes: int | None = None,
    recent_history: Sequence[Mapping[str, Any]] = (),
    month: int | None = None,
    non_stocked_seasonings: Sequence[str] = (),
    preference_terms: Sequence[str] = (),
    limit: int = 3,
) -> list[MealPlan]:
    """Build diverse plans from published recipes without generating recipes."""

    if meal_occasion not in MEAL_OCCASIONS:
        return []
    month = month or datetime.now(timezone.utc).month
    if max_minutes is None:
        max_minutes = {
            "breakfast": 15,
            "lunch": 20,
            "bento": 20,
            "otsumami": 15,
        }.get(meal_occasion)
    stock_names = stock_item_names(stocks)
    if meal_occasion in {"breakfast", "lunch"}:
        primary_roles = ("one_dish", "staple_and_main", "staple")
    elif meal_occasion == "bento":
        primary_roles = ("main", "one_dish", "staple_and_main")
    elif meal_occasion == "otsumami":
        primary_roles = ("main", "side")
    else:
        primary_roles = ("one_dish", "staple_and_main", "main")

    primaries = _ranked(
        catalog,
        meal_occasion,
        roles=primary_roles,
        stocks=stock_names,
        exclusions=exclusions,
        low_capacity=low_capacity,
        month=month,
        max_minutes=max_minutes,
        recent_history=recent_history,
        preference_terms=preference_terms,
    )
    if low_capacity:
        limit = 1
    plans: list[MealPlan] = []
    used_methods: set[str] = set()
    used_titles: set[str] = set()
    remaining = list(primaries)
    while remaining:
        primary = min(
            remaining,
            key=lambda recipe: (
                bool(set(recipe.cooking_method) & used_methods),
                primaries.index(recipe),
            ),
        )
        remaining.remove(primary)
        if primary.title in used_titles:
            continue
        components: dict[str, Recipe] = {}
        role = _role_for_recipe(primary, "main")
        components[role] = primary

        if meal_occasion == "dinner" and role not in {"one_dish", "staple_and_main"} and not low_capacity:
            staple_candidates = [
                item for item in catalog.published("dinner")
                if "staple" in item.dish_roles and not _excluded(item, exclusions)
            ]
            ready_rice = next(
                (item for item in stock_names if ingredient_matches_stock(item, READY_RICE_TERMS)),
                None,
            )
            staple = next(
                (
                    item for item in staple_candidates
                    if ready_rice and any(
                        ingredient_matches_stock(ingredient.normalized_name, [ready_rice])
                        for ingredient in item.ingredients
                    )
                ),
                None,
            ) or next(
                (
                    item for item in staple_candidates
                    if not any(
                        ingredient_matches_stock(ingredient.normalized_name, READY_RICE_TERMS)
                        for ingredient in item.ingredients
                    )
                ),
                None,
            )
            if staple:
                components["staple"] = staple
            extras = _ranked(
                catalog,
                "dinner",
                roles=("side", "soup"),
                stocks=stock_names,
                exclusions=exclusions,
                low_capacity=False,
                month=month,
                max_minutes=max_minutes,
                recent_history=recent_history,
                preference_terms=preference_terms,
            )
            for extra in extras:
                extra_role = "side" if "side" in extra.dish_roles else "soup"
                if extra_role not in components and extra.id != primary.id:
                    components[extra_role] = extra
                if len(components) >= 3:
                    break
        elif meal_occasion == "bento" and role == "main":
            side = next(
                (item for item in _ranked(
                    catalog,
                    "bento",
                    roles=("side",),
                    stocks=stock_names,
                    exclusions=exclusions,
                    low_capacity=False,
                    month=month,
                    max_minutes=max_minutes,
                    recent_history=recent_history,
                    preference_terms=preference_terms,
                ) if item.id != primary.id),
                None,
            )
            if side:
                components["side"] = side

        component_recipes = list(components.values())
        used_stock, additions = _available_and_additions(
            component_recipes, stock_names, non_stocked_seasonings
        )
        if stock_names and not used_stock:
            continue
        if meal_occasion == "dinner" and len(components) > 1:
            title = f"{primary.title}定食"
        elif meal_occasion == "bento":
            title = primary.title if "弁当" in primary.title else f"{primary.title}弁当"
        else:
            title = primary.title
        servings = max(recipe.base_servings for recipe in component_recipes)
        staple_title = components["staple"].title if "staple" in components else (
            primary.title if role in {"one_dish", "staple_and_main"} else ""
        )
        plan = MealPlan(
            title=title,
            meal_type=_meal_type(meal_occasion, primary, len(components)),
            staple=staple_title,
            main=primary.title if role in {"main", "one_dish", "staple_and_main"} else "",
            soup=components["soup"].title if "soup" in components else "",
            side=components["side"].title if "side" in components else "",
            estimated_minutes=_elapsed_for_components(components, cooking_level),
            shopping_additions=additions,
            low_capacity=low_capacity,
            servings=servings,
            ingredients=list(dict.fromkeys(item.ingredient_name for recipe in component_recipes for item in recipe.ingredients)),
            used_stock_items=used_stock,
            component_minutes={
                "staple": components.get("staple").total_minutes if components.get("staple") else 0,
                "main": primary.total_minutes,
                "soup": components.get("soup").total_minutes if components.get("soup") else 0,
                "side": components.get("side").total_minutes if components.get("side") else 0,
            },
            rice_cooker_used=bool(
                "staple" in components and _uses_rice_cooker(components["staple"])
            ),
            ready_rice_used=bool(
                "staple" in components
                and any(
                    ingredient_matches_stock(item.normalized_name, READY_RICE_TERMS)
                    for item in components["staple"].ingredients
                )
            ),
            meal_occasion=meal_occasion,
            recipe_ids={key: recipe.id for key, recipe in components.items()},
            recipe_components={key: recipe.to_component() for key, recipe in components.items()},
            source_references=[
                {"component": key, **asdict(source)}
                for key, recipe in components.items()
                for source in recipe.sources
            ],
        )
        plans.append(plan)
        used_titles.add(primary.title)
        used_methods.update(primary.cooking_method)
        if len(plans) >= limit:
            break
    return plans


def validate_plan_components(plan: MealPlan) -> bool:
    if not plan.recipe_components:
        return False
    for role, recipe_id in plan.recipe_ids.items():
        component = plan.recipe_components.get(role)
        if not component or component.get("recipe_id") != recipe_id:
            return False
    expected_titles = {
        "staple": plan.staple,
        "main": plan.main,
        "soup": plan.soup,
        "side": plan.side,
    }
    for role, title in expected_titles.items():
        if title and role in plan.recipe_components and plan.recipe_components[role].get("title") != title:
            return False
        if not title and role in plan.recipe_components:
            return False
    for component in plan.recipe_components.values():
        title = str(component.get("title") or "")
        steps = " ".join(str(item.get("instruction") or "") for item in component.get("steps") or [])
        if "あんかけ" in title and not re.search(r"(片栗粉|とろみ)", steps):
            return False
    return True


def render_recipe_detail(plan: MealPlan, servings: int | None = None) -> str:
    """Render only registered component data; no model call is required."""

    if not validate_plan_components(plan):
        return "登録レシピの構成を確認できないため、詳細を表示できませんでした。"
    servings = max(1, int(servings or plan.servings or 2))
    lines = [plan.summary(), "", f"材料（{servings}人分）"]
    role_labels = {
        "one_dish": "主食兼主菜",
        "staple_and_main": "主食兼主菜",
        "staple": "主食",
        "main": "主菜",
        "soup": "汁物",
        "side": "副菜",
    }
    ordered_roles = [role for role in ("one_dish", "staple_and_main", "staple", "main", "soup", "side") if role in plan.recipe_components]
    for role in ordered_roles:
        recipe = Recipe.from_mapping(plan.recipe_components[role])
        lines.append(f"[{role_labels[role]}：{recipe.title}]")
        for item in scaled_ingredients(recipe, servings):
            suffix = "（好みで）" if item["optional"] else ""
            lines.append(f"・{item['name']} {item['display_quantity']}{suffix}")

    lines.extend(["", "作り方"])
    step_number = 1
    prior_parallel = False
    for role in sorted(
        ordered_roles,
        key=lambda key: (
            0 if key == "staple" and plan.rice_cooker_used else 1,
            -int(plan.recipe_components[key].get("total_minutes") or 0),
        ),
    ):
        recipe = Recipe.from_mapping(plan.recipe_components[role])
        for index, step in enumerate(recipe.steps):
            prefix = "その間に、" if index == 0 and prior_parallel and step.can_parallelize else ""
            lines.append(f"{step_number}. [{recipe.title}] {prefix}{step.instruction}")
            prior_parallel = prior_parallel or step.can_parallelize
            step_number += 1

    sources = []
    for role in ordered_roles:
        recipe = Recipe.from_mapping(plan.recipe_components[role])
        for source in recipe.sources:
            if source.source_url:
                sources.append(f"{role_labels[role]}：{source.source_name}\n{source.source_url}")
            elif source.source_type == "internal":
                sources.append(f"{role_labels[role]}：{source.source_name or 'おやこ時間ごはんAI 内部レシピ'}")
    if sources:
        lines.extend(["", "参考：", *sources])
    return "\n".join(lines)


def plan_recipe_ids(plan: MealPlan) -> list[str]:
    return list(dict.fromkeys(plan.recipe_ids.values()))


def refresh_plan_from_components(
    plan: MealPlan,
    *,
    stocks: Sequence[str],
    cooking_level: str | None = None,
    non_stocked_seasonings: Sequence[str] = (),
) -> MealPlan:
    """Recompute every derived field from the currently referenced recipes."""

    recipes_by_role = {
        role: Recipe.from_mapping(component)
        for role, component in plan.recipe_components.items()
    }
    recipes = list(recipes_by_role.values())
    stock_names = stock_item_names(stocks)
    used, additions = _available_and_additions(
        recipes, stock_names, non_stocked_seasonings
    )
    plan.recipe_ids = {
        role: recipe.id for role, recipe in recipes_by_role.items()
    }
    plan.ingredients = list(dict.fromkeys(
        item.ingredient_name for recipe in recipes for item in recipe.ingredients
    ))
    plan.used_stock_items = used
    plan.shopping_additions = additions
    primary = (
        recipes_by_role.get("main")
        or recipes_by_role.get("one_dish")
        or recipes_by_role.get("staple_and_main")
    )
    plan.component_minutes = {
        "staple": recipes_by_role["staple"].total_minutes if "staple" in recipes_by_role else 0,
        "main": primary.total_minutes if primary else 0,
        "soup": recipes_by_role["soup"].total_minutes if "soup" in recipes_by_role else 0,
        "side": recipes_by_role["side"].total_minutes if "side" in recipes_by_role else 0,
    }
    plan.estimated_minutes = _elapsed_for_components(recipes_by_role, cooking_level)
    plan.source_references = [
        {"component": role, **asdict(source)}
        for role, recipe in recipes_by_role.items()
        for source in recipe.sources
    ]
    return plan


def recipe_uses_any_stock(recipe: Recipe, stocks: Sequence[str]) -> bool:
    names = stock_item_names(stocks)
    return any(
        ingredient_matches_stock(item.normalized_name, names)
        for item in recipe.ingredients
        if not item.basic_seasoning
    )
