import json
import unittest
from pathlib import Path

from family_os.context_builder import ContextBuilder
from family_os.router import ResponseMode, SafetyLevel, detect_safety, route_response_mode


EXPECTED_MODES = {
    "B001": ResponseMode.PROPOSE,
    "B002": ResponseMode.PROPOSE,
    "B003": ResponseMode.PROPOSE,
    "B004": ResponseMode.REFLECT,
    "B005": ResponseMode.PROPOSE,
    "B006": ResponseMode.CLARIFY,
    "B007": ResponseMode.ACT,
    "B008": ResponseMode.CLARIFY,
    "B009": ResponseMode.SAFETY,
    "B010": ResponseMode.PROPOSE,
    "B011": ResponseMode.PROPOSE,
    "B012": ResponseMode.PROPOSE,
}


class RouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).parent / "Family_OS_Behavior_Test_Cases_v1.0.json"
        cls.cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
        cls.builder = ContextBuilder()

    def test_all_twelve_cases_have_one_primary_mode(self):
        self.assertEqual(len(self.cases), 12)
        for case in self.cases:
            with self.subTest(case=case["id"]):
                context = self.builder.build(case["input"], timestamp="2026-07-18T00:00:00+00:00")
                safety = detect_safety(case["input"], context)
                mode = route_response_mode(case["input"], context, safety)
                self.assertEqual(mode, EXPECTED_MODES[case["id"]])

    def test_safety_is_detected_before_routing(self):
        message = "赤ちゃんがぐったりして反応が弱い。"
        context = self.builder.build(message, timestamp="2026-07-18T00:00:00+00:00")
        safety = detect_safety(message, context)
        self.assertEqual(safety.level, SafetyLevel.EMERGENCY)
        self.assertEqual(route_response_mode(message, context, safety), ResponseMode.SAFETY)

    def test_recipe_adjustment_routes_to_act(self):
        message = "今日は1人分にして"
        context = self.builder.build(message)
        safety = detect_safety(message, context)
        self.assertEqual(route_response_mode(message, context, safety), ResponseMode.ACT)

    def test_whole_meal_component_changes_route_to_act(self):
        for message in (
            "副菜を追加して",
            "副菜をつけて",
            "汁物を追加して",
            "汁物を簡単にして",
            "もう一品つけて",
            "うどんにして",
        ):
            with self.subTest(message=message):
                context = self.builder.build(message)
                safety = detect_safety(message, context)
                self.assertEqual(
                    route_response_mode(message, context, safety),
                    ResponseMode.ACT,
                )

    def test_explicit_low_capacity_meal_preference_routes_to_propose(self):
        message = "疲れたけど、ごはんを炊いて丼にしたい"
        context = self.builder.build(message)
        safety = detect_safety(message, context)

        self.assertEqual(
            route_response_mode(message, context, safety),
            ResponseMode.PROPOSE,
        )


if __name__ == "__main__":
    unittest.main()
