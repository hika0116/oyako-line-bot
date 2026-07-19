import unittest

from family_os.memory import filter_memory_candidates, save_memory_candidates
from family_os.meal_plan import MealPlan
from family_os.router import ResponseMode, SafetyLevel
from family_os.schema import (
    STRUCTURED_OUTPUT_SCHEMA,
    MemoryCandidate,
    StructuredResponse,
    SuggestedAction,
)


class SchemaAndMemoryTests(unittest.TestCase):
    def test_reasoning_tags_are_not_in_user_message(self):
        response = StructuredResponse(
            response_mode="PROPOSE",
            safety_level="none",
            message="表示する文面",
            reasoning_tags=["internal_only"],
        )
        self.assertEqual(response.user_message(), "表示する文面")
        self.assertNotIn("internal_only", response.user_message())

    def test_router_mode_is_authoritative_and_low_capacity_has_one_action(self):
        response = StructuredResponse.from_dict(
            {
                "response_mode": "ACT",
                "safety_level": "none",
                "message": "短い応答",
                "suggested_actions": [
                    {"label": "1", "effort": "minimum", "action": "休む"},
                    {"label": "2", "effort": "low", "action": "作る"},
                ],
                "clarification_question": None,
                "memory_candidates": [],
                "reasoning_tags": [],
                "prompt_version": "model-value",
            },
            forced_mode=ResponseMode.LISTEN,
            minimum_safety=SafetyLevel.NONE,
            prompt_version="1.0",
            low_capacity=True,
        )
        self.assertEqual(response.response_mode, "LISTEN")
        self.assertEqual(len(response.suggested_actions), 1)
        self.assertEqual(response.prompt_version, "1.0")

    def test_sensitive_inferences_are_removed_and_nothing_is_saved(self):
        candidates = [
            MemoryCandidate("add", "temporary_emotion", "今日は怒っている", 0.9, True, "current"),
            MemoryCandidate("update", "stable_preference", "辛さ控えめ", 0.9, False, "explicit change"),
            MemoryCandidate("add", "health_inference", "病気かもしれない", 0.3, True, "guess"),
        ]
        filtered = filter_memory_candidates(candidates)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].type, "stable_preference")
        self.assertTrue(filtered[0].needs_confirmation)
        with self.assertRaises(RuntimeError):
            save_memory_candidates(filtered)

    def test_suggested_actions_are_rendered_for_line(self):
        response = StructuredResponse(
            response_mode="PROPOSE",
            safety_level="none",
            message="候補です。",
            suggested_actions=[
                SuggestedAction("1", "low", "親子丼"),
                SuggestedAction("2", "low", "卵うどん"),
            ],
        )
        rendered = response.user_message()
        self.assertIn("1. 親子丼", rendered)
        self.assertIn("2. 卵うどん", rendered)

    def test_non_numbered_action_is_rendered_naturally(self):
        response = StructuredResponse(
            response_mode="LISTEN",
            safety_level="none",
            message="今日は余力が少なそうですね。",
            suggested_actions=[SuggestedAction("最小案", "minimum", "惣菜を使う")],
        )
        self.assertIn("今できること：最小案：惣菜を使う", response.user_message())

    def test_structured_schema_and_parser_keep_whole_meal_data(self):
        action_schema = STRUCTURED_OUTPUT_SCHEMA["properties"]["suggested_actions"]["items"]
        self.assertIn("meal_plan", action_schema["required"])

        plan = MealPlan(
            title="親子丼セット",
            meal_type="丼",
            staple="ごはん",
            main="親子丼",
            soup="豆腐のみそ汁",
            estimated_minutes=20,
            ingredients=["鶏肉", "卵", "米", "豆腐"],
            used_stock_items=["鶏肉", "卵", "豆腐"],
            component_minutes={"staple": 45, "main": 15, "soup": 10, "side": 0},
            rice_cooker_used=True,
        )
        parsed = StructuredResponse.from_dict(
            {
                "message": "候補です。",
                "suggested_actions": [{
                    "label": "1",
                    "effort": "low",
                    "action": "model value",
                    "meal_plan": plan.to_dict(),
                }],
            },
            forced_mode=ResponseMode.PROPOSE,
            minimum_safety=SafetyLevel.NONE,
            prompt_version="1.0",
        )

        self.assertEqual(parsed.suggested_actions[0].meal_plan.title, "親子丼セット")
        self.assertEqual(parsed.suggested_actions[0].action, plan.compact_action())


if __name__ == "__main__":
    unittest.main()
