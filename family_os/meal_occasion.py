"""Deterministic meal-occasion classification and pending-request context."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping

from .recipe_catalog import MEAL_OCCASIONS


MEAL_OCCASION_LABELS = {
    "breakfast": "朝食",
    "lunch": "昼食",
    "bento": "お弁当",
    "dinner": "夕食",
    "otsumami": "おつまみ",
}
MEAL_OCCASION_NUMBERS = {
    "1": "breakfast",
    "2": "lunch",
    "3": "bento",
    "4": "dinner",
    "5": "otsumami",
}
_OCCASION_PATTERNS = {
    "breakfast": re.compile(r"(朝食|朝ごはん|朝ご飯|モーニング)"),
    "lunch": re.compile(r"(昼食|昼ごはん|昼ご飯|ランチ)"),
    "bento": re.compile(r"(お?弁当|明日の弁当)"),
    "dinner": re.compile(r"(夕食|夕飯|晩ごはん|晩ご飯|夜ごはん|夜ご飯|ディナー)"),
    "otsumami": re.compile(r"(お?つまみ|酒のつまみ|ビールに合う)"),
}
_LOW_CAPACITY = re.compile(
    r"(疲れた|しんどい|今日は無理|余力がない|何もしたくない|手抜きしたい|"
    r"とにかく簡単に|すぐ食べたい|洗い物を減らしたい)"
)


def detect_meal_occasion(message: str, *, allow_number: bool = False) -> str | None:
    text = unicodedata.normalize("NFKC", str(message or "")).strip()
    if allow_number and text in MEAL_OCCASION_NUMBERS:
        return MEAL_OCCASION_NUMBERS[text]
    for occasion, pattern in _OCCASION_PATTERNS.items():
        if pattern.search(text):
            return occasion
    return None


def meal_occasion_prompt() -> str:
    return (
        "どのごはんを考えますか？\n\n"
        "1. 朝食\n"
        "2. 昼食\n"
        "3. お弁当\n"
        "4. 夕食\n"
        "5. おつまみ"
    )


def pending_conditions(message: str) -> dict[str, Any]:
    text = unicodedata.normalize("NFKC", str(message or "")).strip()
    minute = re.search(r"(\d{1,3})\s*分以内", text)
    servings = re.search(r"(\d{1,2})\s*人分", text)
    conditions: dict[str, Any] = {
        "original_message": text,
        "low_capacity": bool(_LOW_CAPACITY.search(text)),
        "max_minutes": int(minute.group(1)) if minute else None,
        "servings": int(servings.group(1)) if servings else None,
        "preferences": [],
    }
    for term in ("がっつり", "ボリューム", "あっさり", "野菜を多め", "肉を使いたい", "買い足しなし"):
        if term in text:
            conditions["preferences"].append(term)
    return conditions


def merge_pending_conditions(
    previous: Mapping[str, Any] | None,
    message: str,
) -> dict[str, Any]:
    merged = dict(previous or {})
    latest = pending_conditions(message)
    merged["original_message"] = " ".join(
        item for item in (str(merged.get("original_message") or ""), latest["original_message"])
        if item
    )
    merged["low_capacity"] = bool(merged.get("low_capacity") or latest["low_capacity"])
    if latest.get("max_minutes") is not None:
        merged["max_minutes"] = latest["max_minutes"]
    if latest.get("servings") is not None:
        merged["servings"] = latest["servings"]
    merged["preferences"] = list(dict.fromkeys([
        *(merged.get("preferences") or []),
        *(latest.get("preferences") or []),
    ]))
    return merged


def valid_meal_occasion(value: str | None) -> bool:
    return bool(value in MEAL_OCCASIONS)
