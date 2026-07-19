import unittest
from unittest.mock import patch

import app as line_app
from family_os.meal_occasion import detect_meal_occasion


class MealOccasionFlowTests(unittest.TestCase):
    def setUp(self):
        line_app.last_suggestions.clear()
        line_app.last_recipes.clear()
        line_app.pending_meal_requests.clear()
        line_app.recent_recipe_history.clear()
        self.profile = {"cooking_level": "簡単な家庭料理ならできる"}
        self.stocks = [
            "豚肉 300g", "鶏肉 300g", "白菜 1玉", "じゃがいも 4個",
            "卵 8個", "豆腐 2丁", "冷凍ごはん 2食", "冷凍うどん 2玉",
        ]

    def _patches(self):
        return (
            patch.object(line_app, "get_profile", return_value=self.profile),
            patch.object(line_app, "get_stocks", return_value=self.stocks),
            patch.object(line_app, "save_meal_log"),
            patch.object(line_app, "generate_structured_reply"),
        )

    def test_occasion_aliases_and_numbers(self):
        self.assertEqual(detect_meal_occasion("朝ごはんどうしよう"), "breakfast")
        self.assertEqual(detect_meal_occasion("明日のお弁当"), "bento")
        self.assertEqual(detect_meal_occasion("ビールに合うもの"), "otsumami")
        self.assertIsNone(detect_meal_occasion("4"))
        self.assertEqual(detect_meal_occasion("4", allow_number=True), "dinner")

    def test_vague_request_asks_once_and_does_not_call_ai(self):
        patches = self._patches()
        with patches[0], patches[1], patches[2], patches[3] as generate:
            reply = line_app.handle_normal_message("u1", "今日どうしよ")
        self.assertIn("どのごはん", reply)
        self.assertNotIn("約", reply)
        self.assertTrue(line_app._get_valid_pending_meal_request("u1"))
        generate.assert_not_called()

    def test_non_food_message_exits_pending_flow(self):
        patches = self._patches()
        response = line_app.StructuredResponse(
            response_mode="LISTEN",
            safety_level="none",
            message="その相談を受け止めます。",
            prompt_version="1.0",
        )
        with patches[0], patches[1], patches[2], \
             patch.object(line_app, "generate_structured_reply", return_value=response) as generate:
            line_app.handle_normal_message("u1", "今日どうしよ")
            reply = line_app.handle_normal_message("u1", "夫と意見が合わなくてつらい")
        self.assertIn("受け止め", reply)
        self.assertNotIn("u1", line_app.pending_meal_requests)
        generate.assert_called_once()

    def test_number_selects_dinner_and_keeps_pending_conditions(self):
        patches = self._patches()
        with patches[0], patches[1], patches[2], patches[3] as generate:
            line_app.handle_normal_message("u1", "がっつり、20分以内でごはんどうしよう")
            reply = line_app.handle_normal_message("u1", "4")
        state = line_app._get_valid_meal_suggestions("u1")
        self.assertEqual(state["meal_occasion"], "dinner")
        self.assertIn("約", reply)
        generate.assert_not_called()

    def test_explicit_breakfast_skips_question(self):
        patches = self._patches()
        with patches[0], patches[1], patches[2], patches[3] as generate:
            reply = line_app.handle_normal_message("u1", "朝ごはんどうしよう")
        self.assertNotIn("どのごはん", reply)
        self.assertEqual(line_app.last_suggestions["u1"]["meal_occasion"], "breakfast")
        generate.assert_not_called()

    def test_bento_has_no_soup_and_uses_suitable_recipes(self):
        patches = self._patches()
        with patches[0], patches[1], patches[2], patches[3]:
            line_app.handle_normal_message("u1", "明日のお弁当を考えて")
        plans = [
            line_app.MealPlan.from_mapping(value)
            for value in line_app.last_suggestions["u1"]["meal_plans"].values()
        ]
        self.assertTrue(plans)
        self.assertTrue(all(plan and not plan.soup for plan in plans))

    def test_otsumami_has_no_automatic_staple(self):
        patches = self._patches()
        with patches[0], patches[1], patches[2], patches[3]:
            line_app.handle_normal_message("u1", "ビールのつまみがほしい")
        plans = [
            line_app.MealPlan.from_mapping(value)
            for value in line_app.last_suggestions["u1"]["meal_plans"].values()
        ]
        self.assertTrue(all(plan and not plan.staple for plan in plans))

    def test_fatigue_survives_question_and_limits_to_one(self):
        patches = self._patches()
        with patches[0], patches[1], patches[2], patches[3] as generate:
            line_app.handle_normal_message("u1", "今日は疲れた。ごはんどうしよう")
            reply = line_app.handle_normal_message("u1", "夕食")
        self.assertEqual(len(line_app.last_suggestions["u1"]["candidates"]), 1)
        self.assertEqual(reply.count("\n1. "), 1)
        self.assertNotIn("\n2. ", reply)
        generate.assert_not_called()

    def test_selection_detail_and_serving_change_do_not_call_ai(self):
        patches = self._patches()
        with patches[0], patches[1], patches[2] as save, patches[3] as generate:
            line_app.handle_normal_message("u1", "夕食どうしよう")
            detail = line_app.handle_recipe_selection("u1", "1", "1")
            adjusted = line_app.handle_normal_message("u1", "1人分にして")
        self.assertIn("材料（", detail)
        self.assertIn("参考：", detail)
        self.assertIn("材料（1人分）", adjusted)
        self.assertNotIn("u1", line_app.last_suggestions)
        self.assertEqual(line_app.last_recipes["u1"]["servings"], 1)
        generate.assert_not_called()
        self.assertGreaterEqual(save.call_count, 3)

    def test_soup_removal_updates_recipe_ids_and_body(self):
        patches = self._patches()
        with patches[0], patches[1], patches[2], patches[3] as generate:
            line_app.handle_normal_message("u1", "夕食どうしよう")
            selected_number = next(
                number for number, value in line_app.last_suggestions["u1"]["meal_plans"].items()
                if line_app.MealPlan.from_mapping(value).soup
            )
            line_app.handle_recipe_selection("u1", selected_number, selected_number)
            before = line_app.MealPlan.from_mapping(line_app.last_recipes["u1"]["meal_plan"])
            removed_title = before.soup
            reply = line_app.handle_normal_message("u1", "汁物はいらない")
        after = line_app.last_recipes["u1"]["meal_plan"]
        self.assertEqual(after["soup"], "")
        self.assertNotIn("soup", after["recipe_ids"])
        self.assertNotIn(removed_title, reply)
        generate.assert_not_called()

    def test_empty_catalog_is_safe_and_does_not_invent(self):
        patches = self._patches()
        with patches[0], patches[1], patches[2], patches[3] as generate, \
             patch.object(line_app, "get_recipe_catalog", return_value=line_app.RecipeCatalog()):
            reply = line_app.handle_normal_message("u1", "夕食どうしよう")
        self.assertIn("登録レシピがまだありません", reply)
        self.assertNotIn("u1", line_app.last_suggestions)
        generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
