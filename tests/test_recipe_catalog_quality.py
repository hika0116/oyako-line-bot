import json
from pathlib import Path
import re
import unittest

from family_os.recipe_catalog import (
    RecipeCatalog,
    compose_meal_plans,
    refresh_plan_from_components,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "supabase" / "migrations" / "202607190001_recipe_catalog.sql"
SEED_PATH = ROOT / "data" / "recipes_seed_v1.json"


class RecipeCatalogMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRATION_PATH.read_text(encoding="utf-8")
        cls.normalized_sql = " ".join(cls.sql.split())

    def test_recipe_history_rls_is_enabled_without_client_policies(self):
        self.assertIn(
            "alter table public.recipe_proposal_history enable row level security;",
            self.sql,
        )
        self.assertNotRegex(
            self.sql,
            re.compile(
                r"create\s+policy[^;]+on\s+public\.recipe_proposal_history",
                re.IGNORECASE | re.DOTALL,
            ),
        )
        self.assertNotIn("auth.uid()::text = user_id", self.sql)
        self.assertIn("auth_user_id", self.sql)

    def test_active_minutes_cannot_exceed_total_minutes(self):
        self.assertIn(
            "constraint recipes_active_minutes_not_over_total check ( active_minutes <= total_minutes )",
            self.normalized_sql,
        )

    def test_season_months_are_limited_to_calendar_months(self):
        self.assertIn("constraint recipes_season_months_valid check", self.normalized_sql)
        self.assertIn(
            "season_months <@ array[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]::integer[]",
            self.normalized_sql,
        )

    def test_ingredient_numeric_constraints_reject_invalid_values(self):
        expected = (
            "quantity is null or quantity > 0",
            "rounding_increment is null or rounding_increment > 0",
            "minimum_quantity is null or minimum_quantity >= 0",
        )
        for expression in expected:
            with self.subTest(expression=expression):
                self.assertIn(expression, self.normalized_sql)

    def test_recipes_updated_at_has_an_update_trigger(self):
        self.assertIn("create or replace function public.set_recipe_updated_at()", self.sql)
        self.assertIn("before update on public.recipes", self.normalized_sql)
        self.assertIn("new.updated_at = now();", self.sql)


class RecipeSeedQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw_recipes = json.loads(SEED_PATH.read_text(encoding="utf-8"))["recipes"]
        cls.by_title = {recipe["title"]: recipe for recipe in cls.raw_recipes}
        cls.catalog = RecipeCatalog.from_json(SEED_PATH)

    def ingredient_names(self, title):
        return {
            item["normalized_name"]
            for item in self.by_title[title]["ingredients"]
        }

    def instructions(self, title):
        return " ".join(
            step["instruction"] for step in self.by_title[title]["steps"]
        )

    def test_ankake_don_has_quantified_water_and_starch(self):
        recipe = self.by_title["豆腐と卵の和風あんかけ丼"]
        ingredients = {item["normalized_name"]: item for item in recipe["ingredients"]}
        self.assertEqual(ingredients["水"]["quantity"], 250)
        self.assertEqual(ingredients["水"]["unit"], "ml")
        self.assertEqual(ingredients["水"]["scaling_mode"], "linear")
        self.assertGreater(ingredients["水"]["rounding_increment"], 0)
        self.assertIn("片栗粉", ingredients)
        self.assertIn("とろみ", self.instructions(recipe["title"]))

    def test_ankake_udon_has_quantified_water_and_starch(self):
        recipe = self.by_title["豚肉と白菜のあんかけうどん"]
        ingredients = {item["normalized_name"]: item for item in recipe["ingredients"]}
        self.assertEqual(ingredients["水"]["quantity"], 400)
        self.assertEqual(ingredients["水"]["unit"], "ml")
        self.assertEqual(ingredients["片栗粉"]["scaling_mode"], "linear")
        self.assertIn("とろみ", self.instructions(recipe["title"]))

    def test_soups_have_quantified_water(self):
        for title in ("白菜と豆腐のみそ汁", "玉ねぎのコンソメスープ"):
            with self.subTest(title=title):
                water = next(
                    item for item in self.by_title[title]["ingredients"]
                    if item["normalized_name"] == "水"
                )
                self.assertEqual((water["quantity"], water["unit"]), (400, "ml"))
                self.assertIn("水400ml", self.instructions(title))

    def test_microwave_potato_has_required_liquid(self):
        water = next(
            item for item in self.by_title["じゃがいものレンジ煮"]["ingredients"]
            if item["normalized_name"] == "水"
        )
        self.assertEqual((water["quantity"], water["unit"]), (2, "大さじ"))
        self.assertIn("水", self.instructions("じゃがいものレンジ煮"))

    def test_frozen_rice_egg_bowl_has_required_liquid(self):
        water = next(
            item for item in self.by_title["冷凍ごはんの卵丼"]["ingredients"]
            if item["normalized_name"] == "水"
        )
        self.assertEqual((water["quantity"], water["unit"]), (2, "大さじ"))
        self.assertIn("卵が固まるまで", self.instructions("冷凍ごはんの卵丼"))

    def test_tuna_egg_sandwich_boils_raw_egg_and_is_not_for_bento(self):
        recipe = self.by_title["ツナ卵サンド"]
        instructions = self.instructions(recipe["title"])
        self.assertIn("卵がかぶる量の水", instructions)
        self.assertIn("沸騰後10分", instructions)
        self.assertIn("殻をむいて", instructions)
        self.assertFalse(recipe["bento_suitable"])
        self.assertNotIn("bento", recipe["meal_occasions"])
        self.assertGreater(recipe["total_minutes"], recipe["active_minutes"])

    def test_rice_total_time_is_longer_than_hands_on_time(self):
        rice = self.by_title["ごはん"]
        self.assertGreaterEqual(rice["total_minutes"], 45)
        self.assertEqual(rice["active_minutes"], 5)
        self.assertGreater(rice["total_minutes"], rice["active_minutes"])

    def test_all_23_recipes_have_basic_material_step_and_time_consistency(self):
        self.assertEqual(len(self.raw_recipes), 23)
        self.assertEqual(len(self.by_title), 23)
        self.assertEqual(len({recipe["id"] for recipe in self.raw_recipes}), 23)

        known_materials = {
            item["normalized_name"]
            for recipe in self.raw_recipes
            for item in recipe["ingredients"]
        }
        for recipe in self.raw_recipes:
            with self.subTest(title=recipe["title"]):
                self.assertEqual(recipe["base_servings"], 2)
                self.assertGreater(recipe["total_minutes"], 0)
                self.assertGreaterEqual(recipe["active_minutes"], 0)
                self.assertLessEqual(recipe["active_minutes"], recipe["total_minutes"])
                self.assertTrue(all(1 <= month <= 12 for month in recipe["season_months"]))
                self.assertTrue(recipe["steps"])
                self.assertEqual(
                    [step["step_number"] for step in recipe["steps"]],
                    list(range(1, len(recipe["steps"]) + 1)),
                )
                self.assertTrue(all(step["instruction"].strip() for step in recipe["steps"]))
                self.assertTrue(all(step["duration_minutes"] >= 0 for step in recipe["steps"]))
                self.assertFalse(recipe["bento_suitable"] and recipe["leak_risk"] == "high")

                sources = recipe["sources"]
                self.assertTrue(sources)
                for source in sources:
                    if source["source_type"] == "internal":
                        self.assertEqual(source["source_url"], "")
                        self.assertIn("内部レシピ", source["source_name"])

                declared = {item["normalized_name"] for item in recipe["ingredients"]}
                instructions = self.instructions(recipe["title"])
                for item in recipe["ingredients"]:
                    self.assertIsNotNone(item["quantity"])
                    self.assertGreater(item["quantity"], 0)
                    self.assertGreater(item["rounding_increment"], 0)
                    if item.get("minimum_quantity") is not None:
                        self.assertGreaterEqual(item["minimum_quantity"], 0)
                    aliases = {item["ingredient_name"], item["normalized_name"]}
                    if item["normalized_name"] == "サラダ油":
                        aliases.add("油")
                    if "ごはん" in item["normalized_name"]:
                        aliases.add("ごはん")
                    if item["normalized_name"] == "和風だし":
                        aliases.add("だし")
                    self.assertTrue(
                        any(alias in instructions for alias in aliases),
                        f"{item['ingredient_name']} is not used by {recipe['title']}",
                    )

                for material in known_materials - {"水"}:
                    if material in instructions:
                        self.assertIn(material, declared)
                if re.search(r"水(?:\d|を|と|、)|水溶き", instructions):
                    self.assertIn("水", declared)
                if re.search(r"油を(?:入れ|ひい|薄くひい)", instructions):
                    self.assertTrue(any("油" in item for item in declared))
                if "ごはん" in instructions:
                    self.assertTrue(any("ごはん" in item for item in declared))
                if "だし" in instructions:
                    self.assertTrue(any("だし" in item for item in declared))

                if "あんかけ" in recipe["title"]:
                    self.assertTrue({"水", "片栗粉"} <= declared)
                    self.assertIn("とろみ", instructions)

    def test_catalog_meal_time_does_not_add_passive_rice_cooking(self):
        plans = compose_meal_plans(
            self.catalog,
            meal_occasion="dinner",
            stocks=["米 1合", "豚肉 300g", "白菜 1玉", "豆腐 1丁"],
            limit=3,
        )
        plan = next(plan for plan in plans if plan.rice_cooker_used)
        self.assertEqual(plan.component_minutes["staple"], 50)
        self.assertLess(plan.estimated_minutes, 50)
        self.assertIn("炊飯時間は含みません", plan.summary())

        refresh_plan_from_components(plan, stocks=["米", "豚肉", "白菜", "豆腐"])
        self.assertLess(plan.estimated_minutes, 50)


if __name__ == "__main__":
    unittest.main()
