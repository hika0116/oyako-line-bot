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


def meal_plan_payload(
    title,
    *,
    meal_type="定食",
    staple="ごはん",
    main="豚肉と白菜の重ね蒸し",
    soup="白菜のみそ汁",
    side="",
    ingredients=None,
    used_stock_items=None,
    shopping_additions=None,
    low_capacity=False,
    component_minutes=None,
    rice_cooker_used=True,
    ready_rice_used=False,
):
    return {
        "title": title,
        "meal_type": meal_type,
        "staple": staple,
        "main": main,
        "soup": soup,
        "side": side,
        "estimated_minutes": 20,
        "shopping_additions": shopping_additions or [],
        "low_capacity": low_capacity,
        "servings": None,
        "ingredients": ingredients or ["豚肉", "白菜", "米"],
        "used_stock_items": used_stock_items or ["豚肉", "白菜"],
        "component_minutes": component_minutes or {
            "staple": 45,
            "main": 20,
            "soup": 10,
            "side": 0,
        },
        "rice_cooker_used": rice_cooker_used,
        "ready_rice_used": ready_rice_used,
    }


def meal_action(label, plan):
    return {
        "label": str(label),
        "effort": "minimum" if plan["low_capacity"] else "low",
        "action": plan["title"],
        "meal_plan": plan,
    }


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
        self.assertIn("whole_meal_policy_fallback", result.reasoning_tags)

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
        self.assertIn("whole_meal_policy_fallback", result.reasoning_tags)
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

        self.assertEqual(len(result.suggested_actions), 3)
        self.assertTrue(all(action.meal_plan for action in result.suggested_actions))
        self.assertTrue(all(action.meal_plan.shopping_additions for action in result.suggested_actions))
        self.assertNotIn("inventory_policy_fallback", result.reasoning_tags)

    def test_vague_consultation_is_not_narrowed_to_one_inventory_candidate(self):
        fake = FakeClient(valid_payload(
            message="今日はお疲れかもしれませんね。",
            suggested_actions=[
                {
                    "label": "1",
                    "effort": "minimum",
                    "action": "豚肉炒め（豚肉を使用。買い足し：なし）",
                },
            ],
        ))
        engine = FamilyOSEngine(client=fake)
        context = self.builder.build(
            "今日どうしよ",
            food_stock=["豚肉 300g", "鶏肉 300g", "じゃがいも 4個"],
        )

        result = engine.respond(context)

        self.assertEqual(len(result.suggested_actions), 3)
        self.assertNotIn("お疲れ", result.message)
        self.assertNotIn("余力が少な", result.message)
        for action in result.suggested_actions:
            self.assertTrue(any(
                stock in action.action
                for stock in ("豚肉", "鶏肉", "じゃがいも")
            ))

    def test_explicit_fatigue_limits_inventory_candidates_to_one(self):
        fake = FakeClient(valid_payload(
            message="候補です。",
            suggested_actions=[
                {
                    "label": "1",
                    "effort": "minimum",
                    "action": "豚肉炒め（豚肉を使用。買い足し：なし）",
                },
                {
                    "label": "2",
                    "effort": "low",
                    "action": "鶏肉煮（鶏肉を使用。買い足し：なし）",
                },
            ],
        ))
        engine = FamilyOSEngine(client=fake)
        context = self.builder.build(
            "今日は疲れた。ごはんどうしよう",
            food_stock=["豚肉 300g", "鶏肉 300g"],
        )

        result = engine.respond(context)

        self.assertEqual(len(result.suggested_actions), 1)
        self.assertIn("豚肉", result.suggested_actions[0].action)
        self.assertIn("一つに絞ります", result.message)

    def test_condition_refinement_keeps_stock_and_rejects_unregistered_beef(self):
        fake = FakeClient(valid_payload(
            message="がっつり候補です。",
            suggested_actions=[
                {
                    "label": "1",
                    "effort": "low",
                    "action": "牛肉とじゃがいもの炒め物（じゃがいもを使用。買い足し：牛肉）",
                },
                {
                    "label": "2",
                    "effort": "low",
                    "action": "牛肉丼（豚肉は副菜に使用。買い足し：牛肉）",
                },
            ],
        ))
        engine = FamilyOSEngine(client=fake)
        stocks = ["豚肉 300g", "鶏肉 300g", "じゃがいも 4個", "白菜 1玉", "ナス 2本"]
        context = self.builder.build("がっつりしたものがいい", food_stock=stocks)

        result = engine.respond(context)

        self.assertEqual(context["resources"]["food_stock"], stocks)
        self.assertEqual(len(result.suggested_actions), 3)
        self.assertNotIn("牛肉", result.user_message())
        for action in result.suggested_actions:
            self.assertTrue(any(
                name in action.action
                for name in ("豚肉", "鶏肉", "じゃがいも", "白菜", "ナス")
            ))

    def test_allergy_is_kept_when_inventory_candidates_are_validated(self):
        fake = FakeClient(valid_payload(
            message="在庫候補です。",
            suggested_actions=[
                {
                    "label": "1",
                    "effort": "low",
                    "action": "牛肉炒め（牛肉を使用。買い足し：なし）",
                },
                {
                    "label": "2",
                    "effort": "low",
                    "action": "豚肉炒め（豚肉を使用。買い足し：なし）",
                },
            ],
        ))
        engine = FamilyOSEngine(client=fake)
        context = self.builder.build(
            "がっつりしたものがいい",
            profile={"allergies": "牛肉アレルギー"},
            food_stock=["牛肉 300g", "豚肉 300g"],
        )

        result = engine.respond(context)

        self.assertNotIn("牛肉", result.user_message())
        self.assertTrue(all("豚肉" in action.action for action in result.suggested_actions))

    def test_valid_whole_meals_keep_compact_initial_display_and_no_shopping(self):
        plans = [
            meal_plan_payload("豚肉と白菜の重ね蒸し定食", side="白菜の浅漬け"),
            meal_plan_payload(
                "豚肉丼セット",
                meal_type="丼",
                main="豚肉丼",
                soup="白菜のみそ汁",
            ),
            meal_plan_payload(
                "豚肉と白菜のワンプレート",
                meal_type="ワンプレート",
                main="豚肉と白菜の一皿",
                soup="",
            ),
        ]
        fake = FakeClient(valid_payload(
            message="長い材料説明は表示しない候補です。",
            suggested_actions=[meal_action(index, plan) for index, plan in enumerate(plans, 1)],
        ))
        engine = FamilyOSEngine(client=fake)
        context = self.builder.build(
            "今日どうしよ",
            food_stock=["豚肉 300g", "白菜 1玉"],
        )

        result = engine.respond(context)
        rendered = result.user_message()

        self.assertEqual(len(result.suggested_actions), 3)
        self.assertIn("whole_meal_policy_validated", result.reasoning_tags)
        self.assertNotIn("300g", rendered)
        self.assertNotIn("作り方", rendered)
        for action in result.suggested_actions:
            self.assertIsNotNone(action.meal_plan)
            self.assertIn("約", action.action)
            self.assertIn("買い足しなし", action.action)

    def test_low_capacity_ready_rice_prefers_one_low_burden_bowl(self):
        fake = FakeClient(valid_payload())
        engine = FamilyOSEngine(client=fake)
        context = self.builder.build(
            "今日は疲れた。ごはんどうしよう",
            food_stock=["冷凍ごはん 2食", "豚肉 200g"],
        )

        result = engine.respond(context)
        plan = result.suggested_actions[0].meal_plan

        self.assertEqual(len(result.suggested_actions), 1)
        self.assertEqual(plan.meal_type, "丼")
        self.assertIn("冷凍ごはん", plan.staple)
        self.assertTrue(plan.ready_rice_used)
        self.assertLessEqual(plan.estimated_minutes, 20)
        self.assertLessEqual(sum(bool(x) for x in (plan.staple, plan.main, plan.soup, plan.side)), 2)

    def test_low_capacity_without_ready_rice_prefers_stocked_noodles(self):
        fake = FakeClient(valid_payload())
        engine = FamilyOSEngine(client=fake)
        context = self.builder.build(
            "今日は無理。とにかく簡単に食べたい",
            food_stock=["冷凍うどん 2玉", "白菜 1/4玉"],
        )

        result = engine.respond(context)
        plan = result.suggested_actions[0].meal_plan

        self.assertEqual(plan.meal_type, "麺")
        self.assertIn("冷凍うどん", plan.staple)
        self.assertLessEqual(plan.estimated_minutes, 20)

    def test_low_capacity_without_ready_staple_does_not_assume_cooking_rice(self):
        fake = FakeClient(valid_payload())
        engine = FamilyOSEngine(client=fake)
        context = self.builder.build(
            "今日は疲れた。何もしたくない。ごはんどうしよう",
            food_stock=["豚肉 200g", "白菜 1/4玉"],
        )

        result = engine.respond(context)
        plan = result.suggested_actions[0].meal_plan

        self.assertFalse(plan.rice_cooker_used)
        self.assertNotEqual(plan.staple, "ごはん")
        self.assertLessEqual(plan.estimated_minutes, 20)

    def test_explicit_request_to_cook_rice_overrides_low_capacity_staple_priority(self):
        fake = FakeClient(valid_payload())
        engine = FamilyOSEngine(client=fake)
        context = self.builder.build(
            "疲れたけど、ごはんを炊いて丼にしたい",
            food_stock=["冷凍うどん 2玉", "豚肉 200g"],
        )

        result = engine.respond(context)
        plan = result.suggested_actions[0].meal_plan

        self.assertEqual(plan.staple, "ごはん")
        self.assertTrue(plan.rice_cooker_used)
        self.assertIn("※炊飯時間は含みません。", plan.summary())

    def test_allergy_in_soup_invalidates_the_entire_meal_candidate(self):
        unsafe = meal_plan_payload(
            "豚肉と白菜の定食",
            soup="卵スープ",
            ingredients=["豚肉", "白菜", "卵", "米"],
            used_stock_items=["豚肉", "白菜", "卵"],
        )
        safe = meal_plan_payload("豚肉と白菜の丼", meal_type="丼", soup="")
        fake = FakeClient(valid_payload(
            suggested_actions=[meal_action(1, unsafe), meal_action(2, safe)],
        ))
        engine = FamilyOSEngine(client=fake)
        context = self.builder.build(
            "今日どうしよ",
            profile={"allergies": "卵アレルギー"},
            food_stock=["豚肉 300g", "白菜 1玉", "卵 4個"],
        )

        result = engine.respond(context)

        self.assertNotIn("卵", result.user_message())
        self.assertTrue(all("卵" not in str(action.meal_plan.to_dict()) for action in result.suggested_actions))

    def test_no_shopping_claim_is_rejected_when_whole_meal_needs_unregistered_food(self):
        inconsistent = meal_plan_payload(
            "豚肉と白菜の定食",
            soup="豆腐のみそ汁",
            ingredients=["豚肉", "白菜", "豆腐", "米"],
            used_stock_items=["豚肉", "白菜"],
            shopping_additions=[],
        )
        fake = FakeClient(valid_payload(
            suggested_actions=[
                meal_action(1, inconsistent),
                meal_action(2, inconsistent),
            ],
        ))
        engine = FamilyOSEngine(client=fake)
        context = self.builder.build(
            "今日どうしよ",
            food_stock=["豚肉 300g", "白菜 1玉"],
        )

        result = engine.respond(context)

        self.assertIn("whole_meal_policy_fallback", result.reasoning_tags)
        for action in result.suggested_actions:
            plan = action.meal_plan
            self.assertIsNotNone(plan)
            self.assertNotIn("豆腐", str(plan.to_dict()))
            self.assertIn("買い足しなし", action.action)

    def test_basic_seasonings_are_removed_from_whole_meal_shopping(self):
        plan = meal_plan_payload(
            "豚肉と白菜の定食",
            side="ズッキーニのナムル",
            ingredients=["豚肉", "白菜", "ズッキーニ", "米", "ごま油", "にんにく"],
            used_stock_items=["豚肉", "白菜", "ズッキーニ"],
            shopping_additions=["ごま油", "にんにく"],
        )
        fake = FakeClient(valid_payload(
            suggested_actions=[meal_action(1, plan), meal_action(2, plan)],
        ))
        engine = FamilyOSEngine(client=fake)
        context = self.builder.build(
            "今日どうしよ",
            food_stock=["豚肉 300g", "白菜 1玉", "ズッキーニ 1本"],
        )

        result = engine.respond(context)

        self.assertIn("whole_meal_policy_validated", result.reasoning_tags)
        self.assertTrue(all(not action.meal_plan.shopping_additions for action in result.suggested_actions))
        self.assertTrue(all("買い足しなし" in action.action for action in result.suggested_actions))

    def test_missing_special_seasoning_remains_an_explicit_purchase(self):
        plan = meal_plan_payload(
            "豚肉と白菜の定食",
            ingredients=["豚肉", "白菜", "米", "ナンプラー"],
            used_stock_items=["豚肉", "白菜"],
            shopping_additions=["ナンプラー"],
        )
        fake = FakeClient(valid_payload(
            suggested_actions=[meal_action(1, plan), meal_action(2, plan)],
        ))
        engine = FamilyOSEngine(client=fake)
        context = self.builder.build(
            "今日どうしよ",
            food_stock=["豚肉 300g", "白菜 1玉"],
        )

        result = engine.respond(context)

        self.assertIn("whole_meal_policy_validated", result.reasoning_tags)
        self.assertTrue(all(action.meal_plan.shopping_additions == ["ナンプラー"] for action in result.suggested_actions))
        runtime_contract = json.loads(fake.responses.calls[0]["input"][2]["content"])
        self.assertTrue(any(
            "do not require one only to add a side dish" in constraint
            for constraint in runtime_contract["constraints"]
        ))

    def test_future_non_stocked_seasoning_profile_can_require_basic_seasoning(self):
        plan = meal_plan_payload(
            "豚肉と白菜の定食",
            ingredients=["豚肉", "白菜", "米", "ごま油"],
            used_stock_items=["豚肉", "白菜"],
            shopping_additions=["ごま油"],
        )
        fake = FakeClient(valid_payload(
            suggested_actions=[meal_action(1, plan), meal_action(2, plan)],
        ))
        engine = FamilyOSEngine(client=fake)
        context = self.builder.build(
            "今日どうしよ",
            profile={"non_stocked_seasonings": ["ごま油"]},
            food_stock=["豚肉 300g", "白菜 1玉"],
        )

        result = engine.respond(context)

        self.assertEqual(context["family_profile"]["non_stocked_seasonings"], ["ごま油"])
        self.assertTrue(all(action.meal_plan.shopping_additions == ["ごま油"] for action in result.suggested_actions))

    def test_high_burden_low_capacity_candidate_is_replaced(self):
        heavy = meal_plan_payload(
            "豚肉とじゃがいもの炒め煮定食",
            main="豚肉とじゃがいもの炒め煮",
            soup="白菜のみそ汁",
            side="じゃがいもの副菜",
            ingredients=["豚肉", "じゃがいも", "白菜", "米"],
            used_stock_items=["豚肉", "じゃがいも", "白菜"],
            low_capacity=True,
            component_minutes={"staple": 45, "main": 35, "soup": 10, "side": 10},
        )
        fake = FakeClient(valid_payload(suggested_actions=[meal_action(1, heavy)]))
        engine = FamilyOSEngine(client=fake)
        context = self.builder.build(
            "今日は疲れた。ごはんどうしよう",
            food_stock=["豚肉 300g", "じゃがいも 4個", "白菜 1玉"],
        )

        result = engine.respond(context)
        plan = result.suggested_actions[0].meal_plan

        self.assertEqual(len(result.suggested_actions), 1)
        self.assertLessEqual(plan.estimated_minutes, 20)
        self.assertNotEqual(plan.meal_type, "定食")
        self.assertNotIn("炒め煮", plan.title)


if __name__ == "__main__":
    unittest.main()
