"""Book 7 response pipeline: safety, routing, generation, validation, memory policy."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Mapping

from .meal_plan import (
    READY_RICE_TERMS,
    MealPlan,
    estimate_elapsed_minutes,
    is_pantry_ingredient,
    normalize_shopping_additions,
    reconcile_rice_preparation,
)
from .memory import filter_memory_candidates
from .prompt_loader import DEFAULT_DOMAIN_PROMPT_PATH, PromptDocument, load_prompt
from .router import (
    ResponseMode,
    SafetyAssessment,
    SafetyLevel,
    detect_safety,
    route_response_mode,
)
from .schema import STRUCTURED_OUTPUT_SCHEMA, StructuredResponse, SuggestedAction


logger = logging.getLogger(__name__)


_RECIPE_DETAIL_TERMS = re.compile(r"(詳しい作り方|作り方を教えて|レシピ詳細)")
_NON_CANDIDATE_MEAL_REQUESTS = re.compile(
    r"(在庫.*(?:確認|見せ|教えて|一覧)|買い物リスト|在庫登録|買い物した|使った食材)"
)
_PROTEIN_TERMS = (
    "鶏肉",
    "豚肉",
    "牛肉",
    "ひき肉",
    "魚",
    "鮭",
    "さば",
    "サバ",
    "ツナ",
    "ベーコン",
    "ハム",
    "ウインナー",
    "卵",
    "豆腐",
    "納豆",
)
_NOODLE_TERMS = ("冷凍うどん", "うどん", "パスタ", "そうめん", "中華麺", "焼きそば麺", "ラーメン", "そば")
_QUICK_STAPLE_TERMS = ("パン", "餅", "即席", "冷凍食品", "惣菜", "総菜")
_COMMON_MAJOR_INGREDIENTS = (*_PROTEIN_TERMS, "じゃがいも", "玉ねぎ", "たまねぎ", "白菜", "キャベツ", "ナス", "なす", "長ネギ", "ねぎ", "小松菜", "にんじん", "大根")


def _is_low_capacity(context: Mapping[str, Any]) -> bool:
    state = context.get("current_state") or {}
    return state.get("physical_energy") == "low" or state.get("mental_energy") == "low"


def _stock_item_names(context: Mapping[str, Any]) -> list[str]:
    resources = context.get("resources") or {}
    names = []
    for stock in resources.get("food_stock") or []:
        name = re.split(r"\s|\d", str(stock or "").strip(), maxsplit=1)[0]
        name = name.rstrip(":：")
        if name and name not in names:
            names.append(name)
    return names


def _food_exclusion_terms(context: Mapping[str, Any]) -> list[str]:
    profile = context.get("family_profile") or {}
    entries = [
        *(profile.get("dietary_restrictions") or []),
        *(profile.get("stable_preferences") or []),
    ]
    terms = []
    raw_values = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        value = str(entry.get("value") or "").strip()
        if not value or value in {"なし", "特になし", "未設定"}:
            continue
        raw_values.append(value)
        for part in re.split(r"[、,・/]", value):
            term = re.sub(
                r"(アレルギー|アレルギ|が苦手|苦手|が嫌い|嫌い|避けたい|控えたい)",
                "",
                part,
            ).strip(" はをがの")
            if term and term not in terms:
                terms.append(term)
    for stock_name in _stock_item_names(context):
        if any(stock_name in value for value in raw_values) and stock_name not in terms:
            terms.append(stock_name)
    return terms


def _is_recipe_detail_request(
    user_message: str,
    additional_instructions: str | None,
) -> bool:
    instructions = str(additional_instructions or "")
    return bool(
        _RECIPE_DETAIL_TERMS.search(user_message)
        or "レシピ詳細" in instructions
        or "新しい番号選択候補は提示しない" in instructions
    )


def _needs_inventory_candidates(
    mode: ResponseMode,
    context: Mapping[str, Any],
    user_message: str,
    additional_instructions: str | None,
) -> bool:
    return bool(
        _stock_item_names(context)
        and mode in {ResponseMode.PROPOSE, ResponseMode.ACT}
        and not _is_recipe_detail_request(user_message, additional_instructions)
        and not _NON_CANDIDATE_MEAL_REQUESTS.search(user_message)
    )


def _needs_meal_candidates(
    mode: ResponseMode,
    context: Mapping[str, Any],
    user_message: str,
    additional_instructions: str | None,
) -> bool:
    request = context.get("request") or {}
    return bool(
        request.get("is_food_related")
        and mode in {ResponseMode.PROPOSE, ResponseMode.ACT}
        and not _is_recipe_detail_request(user_message, additional_instructions)
        and not _NON_CANDIDATE_MEAL_REQUESTS.search(user_message)
    )


def _cooking_level(context: Mapping[str, Any]) -> str:
    profile = context.get("family_profile") or {}
    return str(profile.get("cooking_level") or "unknown")


def _non_stocked_seasonings(context: Mapping[str, Any]) -> list[str]:
    profile = context.get("family_profile") or {}
    value = profile.get("non_stocked_seasonings") or []
    return [str(item).strip() for item in value if str(item).strip()]


def _term_is_registered(term: str, stock_names: list[str]) -> bool:
    return any(term in stock_name or stock_name in term for stock_name in stock_names)


def _matches_any(value: str, terms: list[str] | tuple[str, ...]) -> bool:
    return any(term in value or value in term for term in terms)


def _plan_text(plan: MealPlan) -> str:
    return " ".join([
        plan.title,
        plan.staple,
        plan.main,
        plan.soup,
        plan.side,
        *plan.ingredients,
        *plan.shopping_additions,
    ])


def _normalize_meal_actions(
    actions: list[SuggestedAction],
    context: Mapping[str, Any],
) -> None:
    stock_names = _stock_item_names(context)
    non_stocked = _non_stocked_seasonings(context)
    for action in actions:
        if not action.meal_plan:
            continue
        reconcile_rice_preparation(action.meal_plan, stock_names)
        action.meal_plan.shopping_additions = normalize_shopping_additions(
            action.meal_plan.shopping_additions,
            stock_items=stock_names,
            non_stocked_seasonings=non_stocked,
        )
        action.meal_plan.estimated_minutes = estimate_elapsed_minutes(
            action.meal_plan,
            _cooking_level(context),
        )
        action.action = action.meal_plan.compact_action()


def _meal_plan_is_valid(
    plan: MealPlan | None,
    *,
    stock_names: list[str],
    exclusion_terms: list[str],
    low_capacity: bool,
    user_message: str,
    non_stocked_seasonings: list[str],
) -> bool:
    if not plan or not plan.title or plan.estimated_minutes <= 0:
        return False
    if any(term in _plan_text(plan) for term in exclusion_terms):
        return False

    components = [plan.staple, plan.main, plan.soup, plan.side]
    component_count = sum(bool(item) for item in components)
    if low_capacity:
        if (
            not plan.low_capacity
            or plan.estimated_minutes > 20
            or component_count > 2
            or plan.meal_type == "定食"
            or re.search(r"(炒め煮|揚げ物|煮込み)", plan.title)
        ):
            return False
    elif plan.meal_type == "定食" and (not plan.main or not plan.staple or not (plan.soup or plan.side)):
        return False
    elif component_count == 0:
        return False

    if stock_names:
        if not plan.used_stock_items:
            return False
        if any(not _matches_any(item, stock_names) for item in plan.used_stock_items):
            return False
        if not any(_matches_any(item, stock_names) for item in plan.ingredients):
            return False

    for ingredient in plan.ingredients:
        if is_pantry_ingredient(ingredient, non_stocked_seasonings):
            continue
        if _matches_any(ingredient, stock_names):
            continue
        if _matches_any(ingredient, plan.shopping_additions):
            continue
        return False

    for ingredient in _COMMON_MAJOR_INGREDIENTS:
        if ingredient not in _plan_text(plan):
            continue
        if _matches_any(ingredient, stock_names) or _matches_any(ingredient, plan.shopping_additions):
            continue
        return False

    if any(_matches_any(protein, stock_names) for protein in _PROTEIN_TERMS):
        for protein in _PROTEIN_TERMS:
            if protein in _plan_text(plan) and not _term_is_registered(protein, stock_names):
                return False

    if low_capacity and "ごはんを炊" not in user_message and "ご飯を炊" not in user_message:
        ready_rice = next((item for item in stock_names if _matches_any(item, READY_RICE_TERMS)), None)
        noodle = next((item for item in stock_names if _matches_any(item, _NOODLE_TERMS)), None)
        if ready_rice and not _matches_any(plan.staple, READY_RICE_TERMS):
            return False
        if not ready_rice and noodle and not _matches_any(plan.staple, _NOODLE_TERMS):
            return False
        if not ready_rice and not noodle and plan.rice_cooker_used:
            return False
    return True


def _fallback_plan(
    *,
    title: str,
    meal_type: str,
    staple: str,
    main: str,
    soup: str = "",
    side: str = "",
    stock_items: list[str],
    ingredients: list[str],
    additions: list[str] | None = None,
    low_capacity: bool = False,
    component_minutes: Mapping[str, int] | None = None,
    rice_cooker_used: bool = False,
    ready_rice_used: bool = False,
    cooking_level: str = "unknown",
) -> MealPlan:
    plan = MealPlan(
        title=title,
        meal_type=meal_type,
        staple=staple,
        main=main,
        soup=soup,
        side=side,
        shopping_additions=list(additions or []),
        low_capacity=low_capacity,
        ingredients=ingredients,
        used_stock_items=stock_items,
        component_minutes={
            key: int((component_minutes or {}).get(key, 0))
            for key in ("staple", "main", "soup", "side")
        },
        rice_cooker_used=rice_cooker_used,
        ready_rice_used=ready_rice_used,
    )
    plan.estimated_minutes = estimate_elapsed_minutes(plan, cooking_level)
    return plan


def _low_capacity_fallback_plan(
    stock_names: list[str],
    *,
    user_message: str,
    cooking_level: str,
) -> MealPlan:
    ready_rice = next((item for item in stock_names if _matches_any(item, READY_RICE_TERMS)), None)
    noodle = next((item for item in stock_names if _matches_any(item, _NOODLE_TERMS)), None)
    quick_staple = next((item for item in stock_names if _matches_any(item, _QUICK_STAPLE_TERMS)), None)
    other = next((item for item in stock_names if item not in {ready_rice, noodle, quick_staple}), None)

    if "ごはんを炊" in user_message or "ご飯を炊" in user_message:
        item = other or (stock_names[0] if stock_names else "卵")
        return _fallback_plan(
            title=f"{item}の最小丼",
            meal_type="丼",
            staple="ごはん",
            main=f"{item}の簡単加熱",
            stock_items=[item] if stock_names else [],
            ingredients=[item, "米"],
            additions=[] if stock_names else [item],
            low_capacity=True,
            component_minutes={"staple": 45, "main": 15},
            rice_cooker_used=True,
            cooking_level=cooking_level,
        )
    if ready_rice:
        topping = other or ready_rice
        return _fallback_plan(
            title=f"{topping}のせ最小丼",
            meal_type="丼",
            staple=ready_rice,
            main=f"{topping}の簡単加熱",
            stock_items=list(dict.fromkeys([ready_rice, topping])),
            ingredients=list(dict.fromkeys([ready_rice, topping])),
            low_capacity=True,
            component_minutes={"staple": 5, "main": 10},
            ready_rice_used=True,
            cooking_level=cooking_level,
        )
    if noodle:
        return _fallback_plan(
            title=f"{noodle}の一品完結メニュー",
            meal_type="麺",
            staple=noodle,
            main=f"{noodle}を使う鍋ひとつ料理",
            stock_items=[noodle],
            ingredients=[noodle],
            low_capacity=True,
            component_minutes={"staple": 15},
            cooking_level=cooking_level,
        )
    if quick_staple:
        return _fallback_plan(
            title=f"{quick_staple}で済ませるワンプレート",
            meal_type="ワンプレート",
            staple=quick_staple,
            main=f"{quick_staple}を温めて盛る",
            stock_items=[quick_staple],
            ingredients=[quick_staple],
            low_capacity=True,
            component_minutes={"staple": 10},
            cooking_level=cooking_level,
        )
    if stock_names:
        item = other or stock_names[0]
        return _fallback_plan(
            title=f"{item}のレンジ・鍋ひとつメニュー",
            meal_type="その他",
            staple="",
            main=f"{item}の簡単加熱",
            stock_items=[item],
            ingredients=[item],
            low_capacity=True,
            component_minutes={"main": 15},
            cooking_level=cooking_level,
        )
    return _fallback_plan(
        title="パンと即席スープの最小セット",
        meal_type="ワンプレート",
        staple="パン",
        main="即席スープ",
        ingredients=["パン", "即席スープ"],
        additions=["パン", "即席スープ"],
        stock_items=[],
        low_capacity=True,
        component_minutes={"staple": 5, "main": 5},
        cooking_level=cooking_level,
    )


def _fallback_meal_actions(
    stock_names: list[str],
    *,
    low_capacity: bool,
    user_message: str,
    cooking_level: str,
) -> list[SuggestedAction]:
    if low_capacity:
        plans = [_low_capacity_fallback_plan(
            stock_names,
            user_message=user_message,
            cooking_level=cooking_level,
        )]
    else:
        first = next((item for item in stock_names if _matches_any(item, _PROTEIN_TERMS)), None)
        first = first or (stock_names[0] if stock_names else "鶏肉")
        second = next(
            (item for item in stock_names if item != first and not _matches_any(item, _PROTEIN_TERMS)),
            None,
        )
        second = second or next((item for item in stock_names if item != first), first)
        additions = [] if stock_names else ["鶏肉"]
        used = list(dict.fromkeys([first, second])) if stock_names else []
        ingredients = [first, second, "米"] if stock_names else ["鶏肉", "米"]
        plans = [
            _fallback_plan(
                title=f"{first}と{second}のフライパン蒸し定食",
                meal_type="定食",
                staple="ごはん",
                main=f"{first}と{second}のフライパン蒸し",
                soup=f"{second}の簡単スープ",
                stock_items=used,
                ingredients=ingredients,
                additions=additions,
                component_minutes={"staple": 45, "main": 20, "soup": 10},
                rice_cooker_used=True,
                cooking_level=cooking_level,
            ),
            _fallback_plan(
                title=f"{first}の簡単丼セット",
                meal_type="丼",
                staple="ごはん",
                main=f"{first}の簡単丼",
                side=f"{second}のさっと副菜",
                stock_items=used,
                ingredients=ingredients,
                additions=additions,
                component_minutes={"staple": 45, "main": 15, "side": 5},
                rice_cooker_used=True,
                cooking_level=cooking_level,
            ),
            _fallback_plan(
                title=f"{second}と{first}のワンプレート",
                meal_type="ワンプレート",
                staple="ごはん",
                main=f"{second}と{first}の一皿",
                stock_items=used,
                ingredients=ingredients,
                additions=additions,
                component_minutes={"staple": 45, "main": 20},
                rice_cooker_used=True,
                cooking_level=cooking_level,
            ),
        ]
    return [
        SuggestedAction(
            label=str(index),
            effort="minimum" if low_capacity else "low",
            action="",
            meal_plan=plan,
        )
        for index, plan in enumerate(plans, start=1)
    ]


def _candidate_actions_are_valid_meals(
    actions: list[SuggestedAction],
    context: Mapping[str, Any],
    *,
    stock_names: list[str],
    exclusion_terms: list[str],
    user_message: str,
) -> bool:
    low_capacity = _is_low_capacity(context)
    if not actions or (low_capacity and len(actions) != 1) or (not low_capacity and len(actions) < 2):
        return False
    labels = [item.label.strip().rstrip(".．、:：)") for item in actions]
    if labels != [str(index) for index in range(1, len(actions) + 1)]:
        return False
    return all(
        _meal_plan_is_valid(
            item.meal_plan,
            stock_names=stock_names,
            exclusion_terms=exclusion_terms,
            low_capacity=low_capacity,
            user_message=user_message,
            non_stocked_seasonings=_non_stocked_seasonings(context),
        )
        for item in actions
    )


def _enforce_meal_candidates(
    result: StructuredResponse,
    context: Mapping[str, Any],
    user_message: str,
) -> StructuredResponse:
    registered_stock_names = _stock_item_names(context)
    exclusion_terms = _food_exclusion_terms(context)
    stock_names = [
        name for name in registered_stock_names
        if not any(term in name or name in term for term in exclusion_terms)
    ]
    if registered_stock_names and not stock_names:
        result.suggested_actions = []
        result.message = "登録在庫には、アレルギーまたは苦手食材として確認済みのものが含まれています。"
        result.clarification_question = "使ってよい食材を一つ教えてください。"
        result.reasoning_tags = [*result.reasoning_tags, "inventory_restriction_blocked"][-8:]
        return result

    _normalize_meal_actions(result.suggested_actions, context)
    if not _candidate_actions_are_valid_meals(
        result.suggested_actions,
        context,
        stock_names=stock_names,
        exclusion_terms=exclusion_terms,
        user_message=user_message,
    ):
        result.suggested_actions = _fallback_meal_actions(
            stock_names,
            low_capacity=_is_low_capacity(context),
            user_message=user_message,
            cooking_level=_cooking_level(context),
        )
        if not _candidate_actions_are_valid_meals(
            result.suggested_actions,
            context,
            stock_names=stock_names,
            exclusion_terms=exclusion_terms,
            user_message=user_message,
        ):
            result.suggested_actions = []
            result.message = "確認済みのアレルギー・苦手食材を避けるため、食材を一つ確認させてください。"
            result.clarification_question = "今回使ってよい主な食材を一つ教えてください。"
            result.reasoning_tags = [*result.reasoning_tags, "whole_meal_fallback_blocked"][-8:]
            return result
        policy_tag = "whole_meal_policy_fallback"
    else:
        policy_tag = "whole_meal_policy_validated"
    result.reasoning_tags = [*result.reasoning_tags, policy_tag][-8:]

    if _is_low_capacity(context):
        result.message = "今日は負担を増やさない一食を、一つに絞ります。"
    elif stock_names:
        result.message = "登録在庫を優先すると、この一食候補です。"
    else:
        result.message = "一食全体で考えると、この候補です。"
    result.clarification_question = None
    return result


def _safety_response(
    safety: SafetyAssessment,
    prompt: PromptDocument,
) -> StructuredResponse:
    action = safety.recommended_action or "今すぐ地域の緊急窓口へ連絡してください。"
    message = f"緊急性があります。{action}"
    if safety.level == SafetyLevel.EMERGENCY:
        message += "呼吸が弱い、または反応がない場合は、電話をスピーカーにして指示に従ってください。"
    return StructuredResponse(
        response_mode=ResponseMode.SAFETY.value,
        safety_level=safety.level.value,
        message=message,
        suggested_actions=[],
        clarification_question=None,
        memory_candidates=[],
        reasoning_tags=[safety.reason_tag or "safety_preflight"],
        prompt_version=prompt.version,
    )


def _fallback_response(
    mode: ResponseMode,
    safety: SafetyAssessment,
    prompt: PromptDocument,
) -> StructuredResponse:
    return StructuredResponse(
        response_mode=mode.value,
        safety_level=safety.level.value,
        message="ごめんなさい。少し調子が悪いようです。もう一度送ってください。",
        suggested_actions=[],
        clarification_question=None,
        memory_candidates=[],
        reasoning_tags=["generation_error"],
        prompt_version=prompt.version,
    )


class FamilyOSEngine:
    def __init__(
        self,
        *,
        client: Any,
        model: str = "gpt-4.1-mini",
        prompt_path: str | None = None,
        domain_prompt_path: str | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.prompt = load_prompt(prompt_path)
        self.domain_prompt = load_prompt(domain_prompt_path or DEFAULT_DOMAIN_PROMPT_PATH)

    def respond(
        self,
        context: Mapping[str, Any],
        *,
        additional_instructions: str | None = None,
    ) -> StructuredResponse:
        request = context.get("request") or {}
        user_message = str(request.get("user_message") or "")

        # Book 7 requires this to happen before normal generation.
        safety = detect_safety(user_message, context)
        mode = route_response_mode(user_message, context, safety)
        if safety.level in {SafetyLevel.URGENT, SafetyLevel.EMERGENCY}:
            result = _safety_response(safety, self.prompt)
            self._log_result(result)
            return result

        inventory_candidates_required = _needs_inventory_candidates(
            mode,
            context,
            user_message,
            additional_instructions,
        )
        meal_candidates_required = _needs_meal_candidates(
            mode,
            context,
            user_message,
            additional_instructions,
        )

        constraints = [
            "Return one primary response mode only.",
            "Ask at most one question. If clarification_question is set, do not add another question to message.",
            "When capacity is low, give one suggestion only and keep the response short.",
            "Use at most three suggested_actions; these actions will be rendered after message on LINE.",
            "For selectable meal candidates only, use sequential suggested_action labels 1, 2, and 3; put the dish name in action.",
            "Do not use numeric suggested_action labels for listening, clarification, reflection, or safety responses.",
            "Do not infer missing family facts. Unknown values stay unknown.",
            "reasoning_tags are short classification labels, never chain-of-thought.",
            "memory_candidates are review candidates only and are never already saved.",
        ]
        if _is_low_capacity(context):
            constraints.append(
                "The current user message explicitly signals low capacity; give one suggestion only."
            )
        else:
            constraints.extend([
                "Do not infer fatigue or low capacity from words such as '今日' or from a vague meal consultation.",
                "For a meal proposal without explicit low-capacity language, do not narrow the response to one dish; give two or three candidates when possible.",
            ])
        if inventory_candidates_required:
            constraints.extend([
                "Confirmed food_stock is the highest-priority constraint for this meal consultation.",
                "Every selectable dish in suggested_actions must explicitly use at least one registered food_stock item.",
                "Do not assume unregistered frozen food, prepared food, meat, fish, pasta, or other ingredients are at home.",
                "Rice, water, and ordinary seasonings may be treated as supplemental pantry items.",
                "Every dish action must say either '買い足し：なし' or list its required additions after '買い足し：'.",
                "Put selectable dishes only in suggested_actions; message must be a short introduction and must not list other dishes.",
                "Treat preferences such as hearty, light, more vegetables, or no additional shopping as refinements; keep confirmed food_stock, allergies, and dislikes in force.",
                "If a registered protein can satisfy the request, do not prioritize an unregistered meat or fish as the main ingredient.",
            ])
        if meal_candidates_required:
            constraints.extend([
                "Each selectable candidate represents one complete meal, not only a main dish.",
                "Fill meal_plan for every selectable suggested_action. Keep action itself short; the application renders title, elapsed minutes, and shopping status from meal_plan.",
                "For a set meal, include staple, main, and either soup or side; do not force four handmade dishes.",
                "For bowls, curry, noodles, hot pot, or one-plate meals, use one combined staple/main plus at most one simple soup or side.",
                "estimated_minutes means table-ready elapsed time with parallel work, rounded to about five minutes; do not sum every component.",
                "component_minutes records each component's active/elapsed contribution. Rice-cooker cooking time is excluded when rice_cooker_used is true; reheating ready rice is included.",
                "ingredients lists all major ingredients across the entire meal; shopping_additions lists every unregistered major ingredient needed by the whole meal.",
                "Do not put ordinary seasonings such as salt, pepper, sugar, soy sauce, miso, vinegar, mirin, cooking sake, salad oil, sesame oil, mayonnaise, ketchup, stock powder, garlic, or ginger in shopping_additions unless context explicitly lists them in non_stocked_seasonings.",
                "Special seasonings such as fish sauce, oyster sauce, gochujang, doubanjiang, balsamic vinegar, and uncommon spices require shopping when absent; do not require one only to add a side dish when an ordinary seasoning can replace it.",
                "For rice meals, distinguish ready/cooked/frozen/packed rice from new rice-cooker cooking. Never imply that new rice finishes within the displayed non-rice cooking time.",
                "Initial candidate fields must not contain detailed quantities, recipes, long reasons, or step-by-step instructions.",
            ])
            if _is_low_capacity(context):
                constraints.extend([
                    "The single low-capacity meal must take about 20 minutes or less, use at most two meal components, and minimize steps, dishes, knife use, and concurrent cooking.",
                    "For low capacity, prefer ready rice in stock, otherwise noodles in stock, otherwise bread/mochi/instant/frozen food; do not assume the user will cook rice unless explicitly requested.",
                ])

        runtime_contract = {
            "required_response_mode": mode.value,
            "preflight_safety_level": safety.level.value,
            "constraints": constraints,
            "additional_instructions": additional_instructions,
            "context": context,
        }

        try:
            response = self.client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": self.prompt.content},
                    {"role": "system", "content": self.domain_prompt.content},
                    {
                        "role": "user",
                        "content": json.dumps(runtime_contract, ensure_ascii=False),
                    },
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "family_os_response_v1",
                        "strict": True,
                        "schema": STRUCTURED_OUTPUT_SCHEMA,
                    }
                },
                store=False,
            )
            raw_text = self._extract_output_text(response)
            raw_data = json.loads(raw_text)
            result = StructuredResponse.from_dict(
                raw_data,
                forced_mode=mode,
                minimum_safety=safety.level,
                prompt_version=self.prompt.version,
                low_capacity=_is_low_capacity(context),
            )
            result.memory_candidates = filter_memory_candidates(result.memory_candidates)
            if not result.message:
                raise ValueError("Structured response contained no user message")
        except Exception as exc:
            # Do not include exception text: SDK errors may contain request or
            # endpoint details. The type is enough for operational grouping.
            logger.error(
                "Family OS structured generation failed error_type=%s",
                type(exc).__name__,
            )
            result = _fallback_response(mode, safety, self.prompt)

        if meal_candidates_required:
            result = _enforce_meal_candidates(result, context, user_message)

        if _is_recipe_detail_request(user_message, additional_instructions):
            result.suggested_actions = []

        self._log_result(result)
        return result

    @staticmethod
    def _extract_output_text(response: Any) -> str:
        if getattr(response, "status", None) == "incomplete":
            raise ValueError("OpenAI response was incomplete")
        output_text = getattr(response, "output_text", None)
        if output_text:
            return output_text

        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) != "message":
                continue
            for content in getattr(item, "content", []) or []:
                if getattr(content, "type", None) == "refusal":
                    raise ValueError("OpenAI response was a refusal")
                if getattr(content, "type", None) == "output_text":
                    return str(content.text)
        raise ValueError("OpenAI response contained no output text")

    def _log_result(self, result: StructuredResponse) -> None:
        # Do not log user text, reasoning details, or candidate values.
        logger.info(
            "family_os_response %s",
            json.dumps(
                {
                    "response_mode": result.response_mode,
                    "safety_level": result.safety_level,
                    "memory_candidate_count": len(result.memory_candidates),
                    "response_length": len(result.user_message()),
                    "prompt_version": result.prompt_version,
                    "prompt_sha256": self.prompt.digest_sha256,
                    "domain_prompt_version": self.domain_prompt.version,
                },
                ensure_ascii=False,
            ),
        )
