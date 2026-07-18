"""Book 7 response pipeline: safety, routing, generation, validation, memory policy."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Mapping

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
_UNREGISTERED_STAPLE_TERMS = (
    "冷凍餃子",
    "餃子",
    "パスタ",
    "市販ソース",
    "総菜",
    "惣菜",
    "鶏肉",
    "豚肉",
    "牛肉",
    "ひき肉",
    "肉",
    "魚",
    "鮭",
    "さば",
    "サバ",
    "ツナ",
    "ベーコン",
    "ハム",
    "ウインナー",
)


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


def _term_is_registered(term: str, stock_names: list[str]) -> bool:
    return any(term in stock_name or stock_name in term for stock_name in stock_names)


def _candidate_actions_are_inventory_based(
    actions: list[SuggestedAction],
    stock_names: list[str],
) -> bool:
    if not actions:
        return False

    labels = [item.label.strip().rstrip(".．、:：)") for item in actions]
    if labels != [str(index) for index in range(1, len(actions) + 1)]:
        return False

    for item in actions:
        action = item.action.strip()
        if not action or not any(name in action for name in stock_names):
            return False
        buy_match = re.search(r"買い足し\s*[：:]\s*([^。．）)\n]+)", action)
        if not buy_match:
            return False

        buy_addition = buy_match.group(1).strip()
        for term in _UNREGISTERED_STAPLE_TERMS:
            if term not in action or _term_is_registered(term, stock_names):
                continue
            if term not in buy_addition:
                return False
    return True


def _fallback_inventory_actions(
    stock_names: list[str],
    *,
    low_capacity: bool,
) -> list[SuggestedAction]:
    candidates = []
    if "卵" in stock_names and "豆腐" in stock_names:
        candidates = [
            "卵と豆腐のとろみスープ（卵・豆腐を使用。買い足し：なし。水・一般的な調味料のみ）",
            "卵のシンプル丼（卵を使用。買い足し：なし。米・水・一般的な調味料のみ）",
            "豆腐の甘辛煮（豆腐を使用。買い足し：なし。水・一般的な調味料のみ）",
        ]
    else:
        if len(stock_names) >= 2:
            first, second = stock_names[:2]
            candidates.append(
                f"{first}と{second}を使う簡単な一品"
                f"（{first}・{second}を使用。買い足し：なし。水・一般的な調味料のみ）"
            )
        for name in stock_names:
            candidates.append(
                f"{name}を使う簡単な一品"
                f"（{name}を使用。買い足し：なし。水・一般的な調味料のみ）"
            )

    limit = 1 if low_capacity else 3
    return [
        SuggestedAction(
            label=str(index),
            effort="minimum" if low_capacity else "low",
            action=action,
        )
        for index, action in enumerate(candidates[:limit], start=1)
    ]


def _enforce_inventory_candidates(
    result: StructuredResponse,
    context: Mapping[str, Any],
) -> StructuredResponse:
    stock_names = _stock_item_names(context)
    low_capacity = _is_low_capacity(context)
    if not _candidate_actions_are_inventory_based(result.suggested_actions, stock_names):
        result.suggested_actions = _fallback_inventory_actions(
            stock_names,
            low_capacity=low_capacity,
        )
        policy_tag = "inventory_policy_fallback"
    else:
        policy_tag = "inventory_policy_validated"
    result.reasoning_tags = [
        *[tag for tag in result.reasoning_tags if tag != policy_tag],
        policy_tag,
    ][-8:]

    stock_summary = "、".join(stock_names[:5])
    if low_capacity:
        result.message = f"今日は余力が少なそうなので、登録在庫（{stock_summary}）から一つに絞ります。"
    else:
        result.message = f"登録在庫（{stock_summary}）を最優先にすると、この候補です。"
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
        if inventory_candidates_required:
            constraints.extend([
                "Confirmed food_stock is the highest-priority constraint for this meal consultation.",
                "Every selectable dish in suggested_actions must explicitly use at least one registered food_stock item.",
                "Do not assume unregistered frozen food, prepared food, meat, fish, pasta, or other ingredients are at home.",
                "Rice, water, and ordinary seasonings may be treated as supplemental pantry items.",
                "Every dish action must say either '買い足し：なし' or list its required additions after '買い足し：'.",
                "Put selectable dishes only in suggested_actions; message must be a short introduction and must not list other dishes.",
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

        if inventory_candidates_required:
            result = _enforce_inventory_candidates(result, context)

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
