"""Build a minimal, explicit context without filling gaps by assumption."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Iterable, Mapping


_FOOD_TERMS = re.compile(
    r"(ご飯|ごはん|献立|料理|食事|食べ|レシピ|食材|在庫|冷蔵庫|冷凍庫|"
    r"作り置き|買い物|下ごしらえ|朝食|昼食|夕食|夕飯|晩ごはん|晩ご飯|おかず|弁当|"
    r"調理|何作|作れる|味付け|主菜|副菜|汁物|離乳食|ミルク|辛い物)"
)

# Short messages such as "今日どうしよう" are common meal consultations in
# the LINE flow. Keep the pattern deliberately narrow so a general "どうしよう"
# is not misclassified and persisted as a meal conversation.
_VAGUE_MEAL_CONSULTATION = re.compile(
    r"^(?:今日|今夜|今晩|晩|夜)[、,\s]*(?:の)?"
    r"(?:ご飯|ごはん|献立|夕飯|晩ご飯|晩ごはん)?(?:は|を)?"
    r"(?:どうしよう|どうしよ|どうする|何にしよう|何しよう)[？?。！!]*$"
)
_MEAL_CONDITION_OR_FOLLOWUP = re.compile(
    r"(ボリュームがある|がっつり|あっさり|\d+\s*人分(?:がいい|にして)|"
    r"半分の量|倍量|肉を使いたい|野菜を多め|買い足し(?:なし|を減ら)|"
    r"もっと簡単にして|電子レンジで作れる|味を薄め|子ども向け)"
)


def is_food_related(message: str, stocks: Iterable[str] | None = None) -> bool:
    """Conservatively classify content that may be stored in ``meal_logs``.

    General relationship, emotion, and health conversations must not be persisted
    in the food-purpose table. A stock item match is accepted only when the item
    came from the user's confirmed stock list.
    """

    normalized = str(message or "").strip()
    if not normalized:
        return False
    if (
        _FOOD_TERMS.search(normalized)
        or _VAGUE_MEAL_CONSULTATION.search(normalized)
        or _MEAL_CONDITION_OR_FOLLOWUP.search(normalized)
    ):
        return True

    for stock in stocks or []:
        item_name = re.split(r"\s|\d", str(stock or "").strip(), maxsplit=1)[0]
        if item_name and item_name in normalized:
            return True
    return False


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"", "なし", "特になし", "未設定", "unknown"}
    return bool(value)


def _explicit_time_minutes(message: str) -> int | None:
    minute = re.search(r"(?:あと|使える|時間は|余裕は)?\s*(\d{1,3})\s*分", message)
    if minute:
        return int(minute.group(1))
    hour = re.search(r"(?:あと|使える|時間は|余裕は)?\s*(\d{1,2}(?:\.\d+)?)\s*時間", message)
    if hour:
        return round(float(hour.group(1)) * 60)
    return None


def _current_state(message: str) -> dict[str, Any]:
    physical_energy = "unknown"
    mental_energy = "unknown"
    stated_emotion = None

    if re.search(r"(疲れた|へとへと|ぐったり|体力がない)", message):
        physical_energy = "low"
        stated_emotion = "fatigue"
    if re.search(
        r"(考えられない|気力がない|余力がない|しんどい|限界|今日は無理|"
        r"何もしたくない|手抜きしたい|とにかく簡単に|すぐ食べたい|"
        r"洗い物を減らしたい)",
        message,
    ):
        mental_energy = "low"
        stated_emotion = stated_emotion or "low_capacity"
    elif "疲れた" in message:
        mental_energy = "low"
    if re.search(r"(罪悪感|悪い気がする|申し訳ない)", message):
        stated_emotion = "guilt"
    elif re.search(r"(不安|心配)", message):
        stated_emotion = "concern"

    urgency = "low"
    if re.search(r"(今すぐ|緊急|至急)", message):
        urgency = "high"

    return {
        "time_available_min": _explicit_time_minutes(message),
        "physical_energy": physical_energy,
        "mental_energy": mental_energy,
        "urgency": urgency,
        "stated_emotion": stated_emotion,
    }


def _profile_to_context(profile: Mapping[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    profile = profile if isinstance(profile, Mapping) else {}
    members: list[dict[str, str]] = []
    confirmed: list[dict[str, Any]] = []

    if _present(profile.get("family_size")):
        value = str(profile["family_size"]).strip()
        members.append({"role": "household_adults", "age_or_months": "unknown", "details": value})
        confirmed.append({"type": "family_composition", "value": value})
    if _present(profile.get("children_info")):
        value = str(profile["children_info"]).strip()
        members.append({"role": "child", "age_or_months": value, "details": value})
        confirmed.append({"type": "child_age_or_months", "value": value})

    restrictions = []
    if _present(profile.get("allergies")):
        restrictions.append({"type": "allergy_or_explicit_restriction", "value": str(profile["allergies"]).strip()})
        confirmed.append({"type": "allergy_or_explicit_restriction", "value": str(profile["allergies"]).strip()})

    preferences = []
    if _present(profile.get("dislikes")):
        preferences.append({"type": "dislike", "value": str(profile["dislikes"]).strip()})

    tools = []
    if _present(profile.get("tools")):
        tools.append(str(profile["tools"]).strip())

    family_profile = {
        "members": members,
        "dietary_restrictions": restrictions,
        "stable_preferences": preferences,
        "tools_and_services": tools,
    }
    return family_profile, confirmed


def _conflicts(message: str, profile: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not re.search(r"(前は|以前は|この前は).*(今は|最近は)", message):
        return []
    previous_preferences = []
    if isinstance(profile, Mapping) and _present(profile.get("dislikes")):
        previous_preferences.append(str(profile["dislikes"]).strip())
    return [{
        "type": "preference_change",
        "latest_explicit_statement": message.strip(),
        "previous_confirmed_values": previous_preferences,
        "resolution": "unresolved_until_user_confirms_memory_update",
    }]


def _recent_confirmed_history(logs: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    result = []
    for row in list(logs or [])[:3]:
        if not isinstance(row, Mapping):
            continue
        item = {
            "user_message": row.get("message"),
            "assistant_message": row.get("suggestions"),
            "selected_menu": row.get("selected_menu"),
            "created_at": row.get("created_at"),
        }
        if any(_present(value) for value in item.values()):
            result.append(item)
    return result


class ContextBuilder:
    def __init__(self, book0_version: str = "1.1", book7_version: str = "1.0") -> None:
        self.book0_version = book0_version
        self.book7_version = book7_version

    def build(
        self,
        user_message: str,
        *,
        channel: str = "line",
        profile: Mapping[str, Any] | None = None,
        food_stock: Iterable[str] | None = None,
        frozen_stock: Iterable[str] | None = None,
        recent_logs: Iterable[Mapping[str, Any]] | None = None,
        timestamp: str | None = None,
        additional_resources: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        family_profile, confirmed = _profile_to_context(profile)
        stock_list = list(food_stock or [])
        food_related = is_food_related(user_message, stock_list)
        state = _current_state(user_message)

        temporary = []
        for key in ("time_available_min", "physical_energy", "mental_energy", "stated_emotion"):
            value = state.get(key)
            if value not in {None, "unknown"}:
                temporary.append({"type": key, "value": value, "source": "current_user_message"})

        resources = {
            "food_stock": stock_list if food_related else [],
            "frozen_stock": list(frozen_stock or []) if food_related else [],
            "calendar_constraints": [],
            "available_helpers": [],
        }
        for key, value in (additional_resources or {}).items():
            if key in resources and isinstance(value, list):
                resources[key] = value

        return {
            "request": {
                "user_message": user_message,
                "channel": channel if channel in {"line", "app", "web"} else "app",
                "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
            },
            "family_profile": family_profile,
            "current_state": state,
            "resources": resources,
            "memory": {
                "confirmed": confirmed,
                "temporary": temporary,
                "conflicting": _conflicts(user_message, profile),
            },
            "recent_confirmed_context": _recent_confirmed_history(recent_logs) if food_related else [],
            "policy": {
                "book0_version": self.book0_version,
                "book7_version": self.book7_version,
            },
        }
