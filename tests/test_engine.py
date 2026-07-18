import json
import unittest
from types import SimpleNamespace

from family_os.context_builder import ContextBuilder
from family_os.engine import FamilyOSEngine


class FakeResponses:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(status="completed", output_text=json.dumps(self.payload, ensure_ascii=False))


class FakeClient:
    def __init__(self, payload):
        self.responses = FakeResponses(payload)


def valid_payload(**overrides):
    payload = {
        "response_mode": "PROPOSE",
        "safety_level": "none",
        "message": "今日は冷凍を使って、休む時間を残しましょう。",
        "suggested_actions": [
            {"label": "最小案", "effort": "minimum", "action": "冷凍を使う"}
        ],
        "clarification_question": None,
        "memory_candidates": [],
        "reasoning_tags": ["fatigue", "minimum_effort"],
        "prompt_version": "1.0",
    }
    payload.update(overrides)
    return payload


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.builder = ContextBuilder()

    def test_structured_output_uses_external_prompt_and_privacy_setting(self):
        fake = FakeClient(valid_payload())
        engine = FamilyOSEngine(client=fake)
        context = self.builder.build("今日は疲れた。ご飯どうしよう。")
        result = engine.respond(context)

        self.assertEqual(result.response_mode, "PROPOSE")
        self.assertEqual(result.prompt_version, "1.0")
        self.assertEqual(len(fake.responses.calls), 1)
        call = fake.responses.calls[0]
        self.assertFalse(call["store"])
        self.assertEqual(call["text"]["format"]["type"], "json_schema")
        self.assertIn("Family OS System Prompt v1.0", call["input"][0]["content"])
        self.assertIn("Meal Assistant Domain Prompt v1.0", call["input"][1]["content"])

    def test_emergency_response_bypasses_normal_model_generation(self):
        fake = FakeClient(valid_payload())
        engine = FamilyOSEngine(client=fake)
        context = self.builder.build("赤ちゃんがぐったりして反応が弱い。")
        result = engine.respond(context)

        self.assertEqual(result.response_mode, "SAFETY")
        self.assertEqual(result.safety_level, "emergency")
        self.assertIn("今すぐ119", result.message)
        self.assertEqual(fake.responses.calls, [])

    def test_memory_candidates_are_filtered_not_saved(self):
        fake = FakeClient(valid_payload(memory_candidates=[
            {
                "operation": "add",
                "type": "relationship_assessment",
                "value": "夫婦関係が悪い",
                "confidence": 0.8,
                "needs_confirmation": True,
                "reason": "inference",
            },
            {
                "operation": "update",
                "type": "stable_preference",
                "value": "辛さ控えめ",
                "confidence": 0.9,
                "needs_confirmation": True,
                "reason": "explicit update",
            },
        ]))
        engine = FamilyOSEngine(client=fake)
        context = self.builder.build("以前は辛い物が好きだったけど、今は控えている。")
        result = engine.respond(context)
        self.assertEqual(len(result.memory_candidates), 1)
        self.assertEqual(result.memory_candidates[0].type, "stable_preference")

    def test_inventory_is_required_in_every_meal_candidate(self):
        fake = FakeClient(valid_payload(
            message="在庫を使う候補です。",
            suggested_actions=[
                {
                    "label": "1",
                    "effort": "low",
                    "action": "卵焼き（卵を使用。買い足し：なし）",
                },
                {
                    "label": "2",
                    "effort": "low",
                    "action": "豆腐の照り焼き（豆腐を使用。買い足し：なし）",
                },
                {
                    "label": "3",
                    "effort": "low",
                    "action": "卵と豆腐のスープ（卵・豆腐を使用。買い足し：なし）",
                },
            ],
        ))
        engine = FamilyOSEngine(client=fake)
        context = self.builder.build(
            "今日どうしよう",
            food_stock=["卵 14個", "豆腐 2丁"],
        )

        result = engine.respond(context)

        self.assertEqual(len(result.suggested_actions), 3)
        for action in result.suggested_actions:
            self.assertTrue("卵" in action.action or "豆腐" in action.action)
            self.assertIn("買い足し", action.action)
            self.assertIn(f"{action.label}. {action.action}", result.user_message())
        self.assertIn("inventory_policy_validated", result.reasoning_tags)

        runtime_contract = json.loads(fake.responses.calls[0]["input"][2]["content"])
        self.assertEqual(
            runtime_contract["context"]["resources"]["food_stock"],
            ["卵 14個", "豆腐 2丁"],
        )
        self.assertTrue(any(
            "highest-priority" in constraint
            for constraint in runtime_contract["constraints"]
        ))

    def test_unregistered_only_candidates_are_replaced_before_display(self):
        fake = FakeClient(valid_payload(
            message="冷凍餃子かパスタが簡単です。",
            suggested_actions=[
                {
                    "label": "1",
                    "effort": "minimum",
                    "action": "卵スープと冷凍餃子（卵を使用。買い足し：なし）",
                },
                {
                    "label": "2",
                    "effort": "low",
                    "action": "パスタに市販ソースをかける（買い足し：なし）",
                },
            ],
        ))
        engine = FamilyOSEngine(client=fake)
        context = self.builder.build(
            "今日どうしよう",
            food_stock=["卵 14個", "豆腐 2丁"],
        )

        result = engine.respond(context)
        rendered = result.user_message()

        self.assertEqual(len(result.suggested_actions), 3)
        self.assertIn("inventory_policy_fallback", result.reasoning_tags)
        for action in result.suggested_actions:
            self.assertTrue("卵" in action.action or "豆腐" in action.action)
        self.assertNotIn("冷凍餃子", rendered)
        self.assertNotIn("パスタ", rendered)
        self.assertNotIn("市販ソース", rendered)

    def test_empty_inventory_does_not_force_inventory_candidates(self):
        fake = FakeClient(valid_payload(
            message="在庫が空なら、冷凍餃子も一般的な簡単案です。",
            suggested_actions=[
                {
                    "label": "1",
                    "effort": "minimum",
                    "action": "冷凍餃子を用意する",
                },
            ],
        ))
        engine = FamilyOSEngine(client=fake)
        context = self.builder.build("今日どうしよう", food_stock=[])

        result = engine.respond(context)

        self.assertIn("冷凍餃子", result.user_message())
        self.assertNotIn("inventory_policy_fallback", result.reasoning_tags)


if __name__ == "__main__":
    unittest.main()
