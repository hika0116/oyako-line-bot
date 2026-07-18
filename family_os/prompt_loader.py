"""Load and identify the externally managed Family OS system prompt."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re


DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "prompts"
    / "Family_OS_Work_Handoff_System_Prompt_v1.0.md"
)
DEFAULT_DOMAIN_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "prompts"
    / "Meal_Assistant_Domain_Prompt_v1.0.md"
)


@dataclass(frozen=True)
class PromptDocument:
    content: str
    version: str
    digest_sha256: str
    path: Path


def _extract_version(path: Path, content: str) -> str:
    filename_match = re.search(r"(?:^|_)v(\d+(?:\.\d+)*)", path.name, re.IGNORECASE)
    heading_match = re.search(
        r"Family OS System Prompt\s+v(\d+(?:\.\d+)*)",
        content,
        re.IGNORECASE,
    )
    match = filename_match or heading_match
    if not match:
        raise ValueError(f"Prompt version was not found in {path}")
    return match.group(1)


def load_prompt(path: str | Path | None = None) -> PromptDocument:
    prompt_path = Path(path) if path else DEFAULT_PROMPT_PATH
    prompt_path = prompt_path.expanduser().resolve()
    content = prompt_path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"System prompt is empty: {prompt_path}")
    return PromptDocument(
        content=content,
        version=_extract_version(prompt_path, content),
        digest_sha256=sha256(content.encode("utf-8")).hexdigest(),
        path=prompt_path,
    )
