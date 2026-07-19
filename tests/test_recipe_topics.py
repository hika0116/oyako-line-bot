import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from family_os.recipe_catalog import Recipe, RecipeCatalog
from family_os.recipe_topics import (
    contains_personal_identifier,
    generate_collection_topics,
    public_topic_payload,
)


def minimal_recipe(recipe_id, occasion="dinner", method="蒸す"):
    return Recipe(
        recipe_id, f"料理{recipe_id}", "", 2, ("main",), (occasion,), "和食", (method,),
        15, 10, "easy", False, False, "low", False, (), (), "published", "internal",
    )


class RecipeTopicTests(unittest.TestCase):
    def test_shortage_increases_bento_priority(self):
        recipes = []
        for occasion in ("breakfast", "lunch", "dinner", "otsumami"):
            recipes.extend(
                minimal_recipe(f"{occasion}-{index}", occasion, "蒸す")
                for index in range(3)
            )
        recipes.append(minimal_recipe("bento-0", "bento", "蒸す"))
        catalog = RecipeCatalog(recipes)
        topics = generate_collection_topics(
            catalog=catalog,
            inventory_aggregates=[{"ingredient_name": "白菜", "household_count": 10}],
            month=1,
        )
        self.assertTrue(topics)
        self.assertEqual(topics[0].target_meal_occasions[0], "bento")

    def test_missing_stir_fry_method_is_selected(self):
        catalog = RecipeCatalog([minimal_recipe(str(i), "dinner", "蒸す") for i in range(3)])
        topics = generate_collection_topics(
            catalog=catalog,
            inventory_aggregates=[{"ingredient_name": "白菜", "stale_count": 4}],
            month=1,
        )
        self.assertEqual(topics[0].target_cooking_methods[0], "炒める")

    def test_public_payload_never_contains_personal_identifiers(self):
        topic = generate_collection_topics(
            catalog=RecipeCatalog(),
            inventory_aggregates=[{"ingredient_name": "白菜", "demand_count": 3}],
            month=1,
        )[0]
        payload = public_topic_payload(topic)
        self.assertFalse(contains_personal_identifier(payload))
        self.assertNotIn("user_id", json.dumps(payload))

    def test_dry_run_cli_does_not_require_credentials_or_write(self):
        root = Path(__file__).resolve().parent.parent
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as handle:
            json.dump([{"ingredient_name": "白菜", "household_count": 2}], handle, ensure_ascii=False)
            handle.flush()
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/generate_monthly_recipe_topics.py",
                    "--dry-run",
                    "--month", "1",
                    "--aggregates", handle.name,
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
                env={},
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["dry_run"])
        self.assertTrue(payload["topics"])

    def test_cli_rejects_personal_identifier_input(self):
        root = Path(__file__).resolve().parent.parent
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as handle:
            json.dump([{"ingredient_name": "白菜", "user_id": "private"}], handle)
            handle.flush()
            result = subprocess.run(
                [sys.executable, "scripts/generate_monthly_recipe_topics.py", "--aggregates", handle.name],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
                env={},
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("private", result.stderr)


if __name__ == "__main__":
    unittest.main()
