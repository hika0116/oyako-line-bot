#!/usr/bin/env python3
"""Run all 12 Family OS behavior cases offline or against the configured model."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from family_os.behavior_evaluator import evaluate_behavior_response  # noqa: E402
from family_os.context_builder import ContextBuilder  # noqa: E402
from family_os.engine import FamilyOSEngine  # noqa: E402


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _render_markdown(report: dict) -> str:
    lines = [
        "# Family OS Behavior Test Results",
        "",
        f"- Run mode: `{report['run_mode']}`",
        f"- Executed at: `{report['executed_at']}`",
        f"- Cases: {report['summary']['passed']}/{report['summary']['total']} passed",
        "",
        "| ID | Result | Agency | Information |",
        "|---|---:|---:|---:|",
    ]
    for result in report["results"]:
        lines.append(
            f"| {result['id']} | {'PASS' if result['passed'] else 'FAIL'} "
            f"| {'PASS' if result['agency']['passed'] else 'FAIL'} "
            f"| {'PASS' if result['information_amount']['passed'] else 'FAIL'} |"
        )
    lines.extend([
        "",
        "Automatic checks are regression guards. Meaning-level fit should also be reviewed in the LINE manual check.",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live",
        action="store_true",
        help="Call the configured OpenAI model. Without this flag, reference responses are checked offline.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "reports"),
        help="Directory for JSON and Markdown reports.",
    )
    args = parser.parse_args()

    cases_document = _load_json(ROOT / "tests" / "Family_OS_Behavior_Test_Cases_v1.0.json")
    reference_responses = _load_json(ROOT / "tests" / "reference_responses_v1.0.json")
    cases = cases_document["cases"]

    engine = None
    builder = ContextBuilder()
    if args.live:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            parser.error("--live requires OPENAI_API_KEY")
        from openai import OpenAI

        engine = FamilyOSEngine(
            client=OpenAI(api_key=api_key),
            model=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
            prompt_path=os.environ.get("FAMILY_OS_PROMPT_PATH"),
            domain_prompt_path=os.environ.get("FAMILY_OS_DOMAIN_PROMPT_PATH"),
        )

    results = []
    for case in cases:
        if engine:
            context = builder.build(case["input"], channel="app")
            structured = engine.respond(context)
            response_text = structured.user_message()
            structured_output = structured.to_dict()
        else:
            response_text = reference_responses[case["id"]]
            structured_output = None
        result = evaluate_behavior_response(case, response_text)
        result["source_case"] = {
            "input": case["input"],
            "must": case["must"],
            "must_not": case["must_not"],
        }
        result["structured_output"] = structured_output
        results.append(result)

    report = {
        "test_version": cases_document["version"],
        "book7_version": cases_document["book7_version"],
        "run_mode": "live_model" if args.live else "offline_reference",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total": len(results),
            "passed": sum(1 for item in results if item["passed"]),
            "failed": sum(1 for item in results if not item["passed"]),
        },
        "results": results,
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "behavior_test_results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "behavior_test_results.md").write_text(
        _render_markdown(report),
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
