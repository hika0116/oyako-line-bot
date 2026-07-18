"""Memory-candidate policy. This module never writes user memory."""

from __future__ import annotations

from typing import Iterable

from .schema import MemoryCandidate


ALLOWED_MEMORY_TYPES = {
    "family_composition",
    "child_age_or_months",
    "allergy",
    "allergy_or_explicit_restriction",
    "explicit_restriction",
    "stable_preference",
    "preference",
    "tools_and_services",
    "household_tool",
    "confirmed_experience",
}

PROHIBITED_MEMORY_TYPES = {
    "temporary_emotion",
    "emotion",
    "relationship_assessment",
    "marital_assessment",
    "personality",
    "parenting_ability",
    "health_inference",
    "diagnosis",
    "psychological_state",
}


def filter_memory_candidates(
    candidates: Iterable[MemoryCandidate],
) -> list[MemoryCandidate]:
    """Return reviewable candidates only; do not persist them.

    Minimum Working Family OS deliberately errs on the side of not remembering.
    Every surviving candidate still requires user confirmation.
    """

    filtered: list[MemoryCandidate] = []
    for candidate in candidates:
        candidate_type = candidate.type.strip().lower()
        if candidate_type in PROHIBITED_MEMORY_TYPES:
            continue
        if candidate_type not in ALLOWED_MEMORY_TYPES:
            continue
        if not candidate.value.strip():
            continue
        candidate.needs_confirmation = True
        filtered.append(candidate)
    return filtered[:3]


def save_memory_candidates(*_args: object, **_kwargs: object) -> None:
    """There is intentionally no automatic save path in v1."""

    raise RuntimeError(
        "Memory candidates are review-only. A separate confirmed save flow is required."
    )
