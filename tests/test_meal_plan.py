import unittest

from family_os.meal_plan import MealPlan, estimate_elapsed_minutes
from family_os.schema import StructuredResponse, SuggestedAction


def complete_set_plan() -> MealPlan:
    return MealPlan(
        title="豚肉と白菜の重ね蒸し定食",
        meal_type="定食",
        staple="ごはん",
        main="豚肉と白菜の重ね蒸し",
        soup="白菜のみそ汁",
        side="冷ややっこ",
        shopping_additions=[],
        ingredients=["豚肉", "白菜", "豆腐", "米"],
        used_stock_items=["豚肉", "白菜", "豆腐"],
        component_minutes={"staple": 45, "main": 20, "soup": 10, "side": 3},
        rice_cooker_used=True,
    )


class MealPlanTests(unittest.TestCase):
    def test_initial_candidate_shows_only_title_time_and_shopping_status(self):
        plan = complete_set_plan()
        plan.estimated_minutes = estimate_elapsed_minutes(plan)
        response = StructuredResponse(
            response_mode="PROPOSE",
            safety_level="none",
            message="候補です。",
            suggested_actions=[SuggestedAction("1", "low", "ignored", meal_plan=plan)],
        )

        rendered = response.user_message()

        self.assertIn("1. 豚肉と白菜の重ね蒸し定食", rendered)
        self.assertIn("約25分｜買い足しなし", rendered)
        self.assertNotIn("豚肉300g", rendered)
        self.assertNotIn("作り方", rendered)
        self.assertEqual(response.suggested_actions[0].action, plan.compact_action())

    def test_parallel_elapsed_time_is_not_component_sum(self):
        plan = complete_set_plan()
        elapsed = estimate_elapsed_minutes(plan, "簡単な家庭料理ならできる")

        self.assertEqual(elapsed, 25)
        self.assertNotEqual(elapsed, 78)

    def test_rice_cooker_time_is_excluded_and_summary_has_note(self):
        plan = complete_set_plan()
        plan.estimated_minutes = estimate_elapsed_minutes(plan)

        self.assertLess(plan.estimated_minutes, 45)
        self.assertIn("※炊飯時間は含みません。", plan.summary())

    def test_ready_rice_reheating_time_is_included(self):
        plan = MealPlan(
            title="冷凍ごはんの卵丼",
            meal_type="丼",
            staple="冷凍ごはん",
            main="卵のレンジ加熱",
            ingredients=["冷凍ごはん", "卵"],
            used_stock_items=["冷凍ごはん", "卵"],
            component_minutes={"staple": 5, "main": 3, "soup": 0, "side": 0},
            ready_rice_used=True,
        )

        self.assertEqual(estimate_elapsed_minutes(plan), 5)
        self.assertNotIn("炊飯時間は含みません", plan.summary())

    def test_cooking_level_adjustment_is_small_and_standard_is_default(self):
        plan = complete_set_plan()

        beginner = estimate_elapsed_minutes(plan, "ほぼ初心者")
        standard = estimate_elapsed_minutes(plan, None)
        experienced = estimate_elapsed_minutes(plan, "作り置きや下味冷凍もできる")

        self.assertEqual((beginner, standard, experienced), (30, 25, 20))


if __name__ == "__main__":
    unittest.main()
