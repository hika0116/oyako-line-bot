import json
import unittest
from pathlib import Path

from family_os.behavior_evaluator import evaluate_behavior_response


class BehaviorEvaluatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = Path(__file__).parent
        cls.cases = json.loads(
            (directory / "Family_OS_Behavior_Test_Cases_v1.0.json").read_text(encoding="utf-8")
        )["cases"]
        cls.responses = json.loads(
            (directory / "reference_responses_v1.0.json").read_text(encoding="utf-8")
        )

    def test_all_twelve_reference_cases_pass(self):
        self.assertEqual(len(self.cases), 12)
        results = [
            evaluate_behavior_response(case, self.responses[case["id"]])
            for case in self.cases
        ]
        failures = [result for result in results if not result["passed"]]
        self.assertEqual(failures, [])

    def test_agency_violation_is_detected(self):
        case = next(item for item in self.cases if item["id"] == "B012")
        result = evaluate_behavior_response(case, "あなたは必ずこちらを選ぶべきです。")
        self.assertFalse(result["agency"]["passed"])
        self.assertFalse(result["passed"])


if __name__ == "__main__":
    unittest.main()
