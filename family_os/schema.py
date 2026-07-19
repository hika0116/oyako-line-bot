"""Internal response contract and validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from .meal_plan import MEAL_PLAN_SCHEMA, MealPlan
from .router import ResponseMode, SafetyLevel


@dataclass
class SuggestedAction:
    label: str
    effort: str
    action: str
    meal_plan: MealPlan | None = None

    def __post_init__(self) -> None:
        if self.meal_plan and self.meal_plan.title:
            self.action = self.meal_plan.compact_action()


@dataclass
class MemoryCandidate:
    operation: str
    type: str
    value: str
    confidence: float
    needs_confirmation: bool
    reason: str


@dataclass
class StructuredResponse:
    response_mode: str
    safety_level: str
    message: str
    suggested_actions: list[SuggestedAction] = field(default_factory=list)
    clarification_question: str | None = None
    memory_candidates: list[MemoryCandidate] = field(default_factory=list)
    reasoning_tags: list[str] = field(default_factory=list)
    prompt_version: str = "unknown"

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        forced_mode: ResponseMode,
        minimum_safety: SafetyLevel,
        prompt_version: str,
        low_capacity: bool = False,
    ) -> "StructuredResponse":
        # The deterministic router owns the primary mode. Model output cannot
        # silently reroute the request after the safety/intent preflight.
        mode = forced_mode.value

        safety = str(data.get("safety_level") or minimum_safety.value)
        rank = {"none": 0, "caution": 1, "urgent": 2, "emergency": 3}
        if safety not in rank or rank[safety] < rank[minimum_safety.value]:
            safety = minimum_safety.value

        actions = []
        for item in list(data.get("suggested_actions") or [])[:3]:
            if not isinstance(item, Mapping):
                continue
            effort = str(item.get("effort") or "low")
            if effort not in {"minimum", "low", "medium"}:
                effort = "low"
            actions.append(SuggestedAction(
                label=str(item.get("label") or "小さな一歩"),
                effort=effort,
                action=str(item.get("action") or ""),
                meal_plan=MealPlan.from_mapping(item.get("meal_plan")),
            ))
        if low_capacity:
            actions = actions[:1]

        candidates = []
        for item in list(data.get("memory_candidates") or [])[:3]:
            if not isinstance(item, Mapping):
                continue
            operation = str(item.get("operation") or "add")
            if operation not in {"add", "update", "remove"}:
                operation = "add"
            candidates.append(MemoryCandidate(
                operation=operation,
                type=str(item.get("type") or "other"),
                value=str(item.get("value") or ""),
                confidence=max(0.0, min(1.0, float(item.get("confidence") or 0.0))),
                needs_confirmation=bool(item.get("needs_confirmation", True)),
                reason=str(item.get("reason") or "future_support"),
            ))

        question = data.get("clarification_question")
        if question is not None:
            question = str(question).strip() or None

        return cls(
            response_mode=mode,
            safety_level=safety,
            message=str(data.get("message") or "").strip(),
            suggested_actions=actions,
            clarification_question=question,
            memory_candidates=candidates,
            reasoning_tags=[str(tag) for tag in list(data.get("reasoning_tags") or [])[:8]],
            prompt_version=prompt_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def user_message(self) -> str:
        """Render every user-facing field while keeping internal tags private.

        ``suggested_actions`` is part of the user contract, so LINE must not drop
        it. Number labels are preserved for explicit meal-choice flows; all other
        actions are rendered as short natural Japanese.
        """

        message = self.message.strip()
        parts = [message]
        actions = [item for item in self.suggested_actions if item.action.strip()]
        numeric_labels = [item.label.strip().rstrip(".．、:：)") for item in actions]
        numbered = bool(actions) and numeric_labels == [str(i) for i in range(1, len(actions) + 1)]

        if numbered:
            lines = [f"{label}. {item.action.strip()}" for label, item in zip(numeric_labels, actions)]
            missing_lines = [line for line in lines if line not in message]
            if missing_lines:
                parts.append("\n".join(missing_lines))
        else:
            rendered = []
            for item in actions:
                action = item.action.strip()
                label = item.label.strip()
                text = action if not label or label in action else f"{label}：{action}"
                if action not in message and text not in rendered:
                    rendered.append(text)
            if len(rendered) == 1:
                parts.append(f"今できること：{rendered[0]}")
            elif rendered:
                parts.append("できること：\n" + "\n".join(f"・{item}" for item in rendered))

        if self.clarification_question:
            parts.append(self.clarification_question.strip())
        return "\n\n".join(part for part in parts if part)


STRUCTURED_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "response_mode": {
            "type": "string",
            "enum": [item.value for item in ResponseMode],
        },
        "safety_level": {
            "type": "string",
            "enum": [item.value for item in SafetyLevel],
        },
        "message": {"type": "string"},
        "suggested_actions": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "effort": {"type": "string", "enum": ["minimum", "low", "medium"]},
                    "action": {"type": "string"},
                    "meal_plan": MEAL_PLAN_SCHEMA,
                },
                "required": ["label", "effort", "action", "meal_plan"],
                "additionalProperties": False,
            },
        },
        "clarification_question": {"type": ["string", "null"]},
        "memory_candidates": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": ["add", "update", "remove"]},
                    "type": {"type": "string"},
                    "value": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "needs_confirmation": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": [
                    "operation",
                    "type",
                    "value",
                    "confidence",
                    "needs_confirmation",
                    "reason",
                ],
                "additionalProperties": False,
            },
        },
        "reasoning_tags": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string"},
        },
        "prompt_version": {"type": "string"},
    },
    "required": [
        "response_mode",
        "safety_level",
        "message",
        "suggested_actions",
        "clarification_question",
        "memory_candidates",
        "reasoning_tags",
        "prompt_version",
    ],
    "additionalProperties": False,
}
