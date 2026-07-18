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


if __name__ == "__main__":
    unittest.main()
