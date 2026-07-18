"""Book 7 response pipeline: safety, routing, generation, validation, memory policy."""

from __future__ import annotations

import json
import logging
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
from .schema import STRUCTURED_OUTPUT_SCHEMA, StructuredResponse


logger = logging.getLogger(__name__)


def _is_low_capacity(context: Mapping[str, Any]) -> bool:
    state = context.get("current_state") or {}
    return state.get("physical_energy") == "low" or state.get("mental_energy") == "low"


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

        runtime_contract = {
            "required_response_mode": mode.value,
            "preflight_safety_level": safety.level.value,
            "constraints": [
                "Return one primary response mode only.",
                "Ask at most one question. If clarification_question is set, do not add another question to message.",
                "When capacity is low, give one suggestion only and keep the response short.",
                "Use at most three suggested_actions; these actions will be rendered after message on LINE.",
                "For selectable meal candidates only, use sequential suggested_action labels 1, 2, and 3; put the dish name in action.",
                "Do not use numeric suggested_action labels for listening, clarification, reflection, or safety responses.",
                "Do not infer missing family facts. Unknown values stay unknown.",
                "reasoning_tags are short classification labels, never chain-of-thought.",
                "memory_candidates are review candidates only and are never already saved.",
            ],
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
