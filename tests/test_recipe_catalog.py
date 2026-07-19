import time
import unittest

from family_os.meal_plan import MealPlan
from family_os.recipe_catalog import (
    Recipe,
    RecipeCatalog,
    RecipeIngredient,
    RecipeSource,
    RecipeStep,
    compose_meal_plans,
    format_quantity,
    ingredient_matches_stock,
    render_recipe_detail,
    scale_quantity,
    validate_plan_components,
)


class RecipeCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = RecipeCatalog.from_json()
        cls.stocks = [
            "豚肉 300g", "鶏肉 300g", "白菜 1玉", "じゃがいも 4個",
            "卵 8個", "豆腐 2丁", "なす 3本", "ズッキーニ 1本",
            "冷凍ごはん 2食", "冷凍うどん 2玉", "鮭 2切れ",
        ]

    def test_seed_has_all_occasions_roles_and_methods(self):
        self.assertGreaterEqual(len(self.catalog.recipes), 20)
        occasions = {value for recipe in self.catalog.recipes for value in recipe.meal_occasions}
        roles = {value for recipe in self.catalog.recipes for value in recipe.dish_roles}
        methods = {value for recipe in self.catalog.recipes for value in recipe.cooking_method}
        self.assertEqual(occasions, {"breakfast", "lunch", "bento", "dinner", "otsumami"})
        self.assertTrue({"one_dish", "main", "side", "soup", "staple_and_main"} <= roles)
        self.assertTrue({"炒める", "煮る", "蒸す", "電子レンジ", "和える", "焼く"} <= methods)

    def test_only_published_recipes_are_returned(self):
        draft = Recipe(
            "draft", "下書き", "", 2, ("main",), ("dinner",), "", ("焼く",),
            10, 5, "easy", False, False, "low", False, (), (), "draft", "internal",
        )
        catalog = RecipeCatalog([*self.catalog.recipes, draft])
        self.assertNotIn("draft", {recipe.id for recipe in catalog.published("dinner")})

    def test_dinner_candidates_are_diverse_and_use_stock(self):
        plans = compose_meal_plans(
            self.catalog,
            meal_occasion="dinner",
            stocks=self.stocks,
            month=7,
            limit=3,
        )
        self.assertEqual(len(plans), 3)
        primary_ids = [next(iter(plan.recipe_ids.values())) for plan in plans]
        self.assertEqual(len(primary_ids), len(set(primary_ids)))
        self.assertEqual(len({plan.title for plan in plans}), 3)
        self.assertTrue(all(plan.used_stock_items for plan in plans))

    def test_bento_excludes_soup_and_high_leak_risk(self):
        plans = compose_meal_plans(
            self.catalog,
            meal_occasion="bento",
            stocks=self.stocks,
            limit=3,
        )
        self.assertTrue(plans)
        for plan in plans:
            self.assertFalse(plan.soup)
            self.assertNotIn("soup", plan.recipe_ids)
            for component in plan.recipe_components.values():
                recipe = Recipe.from_mapping(component)
                self.assertNotEqual(recipe.leak_risk, "high")

    def test_otsumami_does_not_add_staple(self):
        plans = compose_meal_plans(
            self.catalog,
            meal_occasion="otsumami",
            stocks=self.stocks,
        )
        self.assertTrue(plans)
        self.assertTrue(all(not plan.staple and "staple" not in plan.recipe_ids for plan in plans))

    def test_low_capacity_returns_one_low_burden_recipe(self):
        plans = compose_meal_plans(
            self.catalog,
            meal_occasion="dinner",
            stocks=self.stocks,
            low_capacity=True,
        )
        self.assertEqual(len(plans), 1)
        self.assertTrue(plans[0].low_capacity)
        self.assertLessEqual(plans[0].estimated_minutes, 20)

    def test_allergy_excludes_every_component(self):
        plans = compose_meal_plans(
            self.catalog,
            meal_occasion="dinner",
            stocks=self.stocks,
            exclusions=["卵"],
        )
        self.assertTrue(plans)
        self.assertTrue(all("卵" not in " ".join(plan.ingredients) for plan in plans))

    def test_recent_proposals_are_penalized(self):
        baseline = compose_meal_plans(
            self.catalog,
            meal_occasion="dinner",
            stocks=self.stocks,
            limit=1,
        )[0]
        primary_id = next(iter(baseline.recipe_ids.values()))
        history = [
            {"recipe_id": primary_id, "proposed_at": time.time()}
            for _ in range(3)
        ]
        next_plan = compose_meal_plans(
            self.catalog,
            meal_occasion="dinner",
            stocks=self.stocks,
            recent_history=history,
            limit=1,
        )[0]
        self.assertNotEqual(next(iter(next_plan.recipe_ids.values())), primary_id)

    def test_scaling_modes_are_deterministic_and_practical(self):
        linear = RecipeIngredient("肉", "肉", 300, "g", "linear", 10)
        count = RecipeIngredient("卵", "卵", 3, "個", "count", 1, 1)
        seasoning = RecipeIngredient("しょうゆ", "しょうゆ", 2, "大さじ", "seasoning", 0.25)
        fixed = RecipeIngredient("油", "油", 1, "小さじ", "mostly_fixed", 0.25)
        self.assertEqual(scale_quantity(linear, 2, 1), 150)
        self.assertEqual(scale_quantity(count, 2, 1), 2)
        self.assertGreater(scale_quantity(seasoning, 2, 1), 1)
        self.assertGreater(scale_quantity(fixed, 2, 1), 0.5)
        self.assertNotIn(".83", format_quantity(scale_quantity(seasoning, 2, 1), "大さじ"))

    def test_normalized_stock_aliases_match_recipe_ingredients(self):
        self.assertTrue(ingredient_matches_stock("鶏肉", ["鶏もも肉"]))
        self.assertTrue(ingredient_matches_stock("豚肉", ["豚こま肉"]))
        self.assertTrue(ingredient_matches_stock("玉ねぎ", ["たまねぎ"]))

    def test_render_preserves_recipe_ids_steps_and_internal_source(self):
        plan = compose_meal_plans(
            self.catalog,
            meal_occasion="dinner",
            stocks=self.stocks,
            limit=1,
        )[0]
        text = render_recipe_detail(plan, 1)
        self.assertTrue(validate_plan_components(plan))
        self.assertIn("材料（1人分）", text)
        self.assertIn("作り方", text)
        self.assertIn("おやこ時間ごはんAI 内部レシピ", text)
        self.assertNotIn("https://", text)

    def test_external_reference_url_is_rendered_without_copying_a_page(self):
        source = RecipeSource("Example Kitchen", "https://example.com/r/1", "licensed_api")
        recipe = Recipe(
            "r", "許諾済み料理", "", 2, ("one_dish",), ("lunch",), "", ("焼く",),
            10, 5, "easy", True, False, "low", False, (), (), "published", "licensed_api",
            ingredients=(RecipeIngredient("卵", "卵", 2, "個", "count", 1),),
            steps=(RecipeStep(1, "卵を十分に加熱する。", 10, ("フライパン",)),),
            sources=(source,),
        )
        plan = compose_meal_plans(
            RecipeCatalog([recipe]), meal_occasion="lunch", stocks=["卵 2個"], limit=1
        )[0]
        self.assertIn("https://example.com/r/1", render_recipe_detail(plan))

    def test_ankake_requires_registered_thickening_step(self):
        bad = Recipe(
            "bad", "あんかけうどん", "", 2, ("staple_and_main",), ("lunch",), "", ("煮る",),
            10, 5, "easy", True, False, "high", False, (), ("麺",), "published", "internal",
            ingredients=(RecipeIngredient("うどん", "うどん", 2, "玉", "count", 1),),
            steps=(RecipeStep(1, "うどんを煮る。", 10, ("鍋",)),),
        )
        plan = compose_meal_plans(
            RecipeCatalog([bad]), meal_occasion="lunch", stocks=["うどん 2玉"], limit=1
        )[0]
        self.assertFalse(validate_plan_components(plan))
        self.assertIn("詳細を表示できません", render_recipe_detail(plan))

    def test_empty_catalog_returns_no_plans_instead_of_inventing(self):
        self.assertEqual(
            compose_meal_plans(RecipeCatalog(), meal_occasion="dinner", stocks=self.stocks),
            [],
        )


if __name__ == "__main__":
    unittest.main()
