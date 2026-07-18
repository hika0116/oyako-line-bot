"""Safety-first routing for the Minimum Working Family OS."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping


class ResponseMode(str, Enum):
    LISTEN = "LISTEN"
    CLARIFY = "CLARIFY"
    PROPOSE = "PROPOSE"
    PLAN = "PLAN"
    ACT = "ACT"
    REFLECT = "REFLECT"
    SAFETY = "SAFETY"


class SafetyLevel(str, Enum):
    NONE = "none"
    CAUTION = "caution"
    URGENT = "urgent"
    EMERGENCY = "emergency"


@dataclass(frozen=True)
class SafetyAssessment:
    level: SafetyLevel
    reason_tag: str | None = None
    recommended_action: str | None = None


_EMERGENCY_PATTERNS = (
    re.compile(r"(赤ちゃん|乳児|子ども).*(ぐったり|反応が(弱い|ない)|意識がない)"),
    re.compile(r"(ぐったり|反応が(弱い|ない)|意識がない).*(赤ちゃん|乳児|子ども)"),
    re.compile(r"(呼吸|息).*(していない|止まった|極端に弱い)"),
    re.compile(r"(大量に|たくさん).*(薬|洗剤|毒).*(飲んだ|誤飲)"),
)

_URGENT_PATTERNS = (
    re.compile(r"(けいれん|痙攣).*(続いて|止まら)"),
    re.compile(r"(唇|くちびる).*(紫|青)"),
    re.compile(r"(強い出血|血が止まらない)"),
)


def detect_safety(message: str, context: Mapping[str, Any] | None = None) -> SafetyAssessment:
    """Run before normal intent inference. It does not diagnose a condition."""

    normalized = message.strip()
    for pattern in _EMERGENCY_PATTERNS:
        if pattern.search(normalized):
            return SafetyAssessment(
                level=SafetyLevel.EMERGENCY,
                reason_tag="possible_life_threatening_symptoms",
                recommended_action=(
                    "今すぐ119（日本以外なら地域の救急番号）へ連絡し、"
                    "通信指令員の指示に従ってください。"
                ),
            )
    for pattern in _URGENT_PATTERNS:
        if pattern.search(normalized):
            return SafetyAssessment(
                level=SafetyLevel.URGENT,
                reason_tag="urgent_symptom_signal",
                recommended_action=(
                    "すぐに119（日本以外なら地域の救急番号）へ連絡してください。"
                ),
            )

    urgency = ((context or {}).get("current_state") or {}).get("urgency")
    if urgency == "emergency":
        return SafetyAssessment(
            level=SafetyLevel.EMERGENCY,
            reason_tag="explicit_emergency_context",
            recommended_action="今すぐ地域の緊急窓口へ連絡してください。",
        )
    if urgency == "high":
        return SafetyAssessment(
            level=SafetyLevel.CAUTION,
            reason_tag="explicit_high_urgency",
        )
    return SafetyAssessment(level=SafetyLevel.NONE)


def route_response_mode(
    message: str,
    context: Mapping[str, Any],
    safety: SafetyAssessment,
) -> ResponseMode:
    if safety.level in {SafetyLevel.URGENT, SafetyLevel.EMERGENCY}:
        return ResponseMode.SAFETY

    text = message.strip()
    if not text or re.fullmatch(r"(どうしたらいい|どうすればいい|どうしよう)[？?。！!]*", text):
        return ResponseMode.CLARIFY

    if re.search(r"(夫|妻|パートナー).*(意見が合わない|話が合わない|考えが違う)", text):
        return ResponseMode.CLARIFY

    if re.search(r"(冷凍庫|作り置き).*(入ってた|入っていた|残ってた|残っていた)", text):
        return ResponseMode.REFLECT

    if re.search(r"(手順|段取り|順番|計画|スケジュール)", text):
        return ResponseMode.PLAN

    if re.search(r"(考えて|作って|書いて|まとめて|一覧にして|献立を|買い物リスト)", text):
        return ResponseMode.ACT

    if re.search(r"(正解[？?]|どっち|どちら|案|選択肢|どうしよう)", text):
        return ResponseMode.PROPOSE

    # "辛い物" means spicy food and must not be treated as emotional distress.
    if re.search(r"(つらい|しんどい|悲しい|疲れた|限界|気持ちが辛い)", text):
        return ResponseMode.LISTEN

    if re.search(r"(気づいた|かもしれない|だったんだ|思った)", text):
        return ResponseMode.REFLECT

    return ResponseMode.PROPOSE
