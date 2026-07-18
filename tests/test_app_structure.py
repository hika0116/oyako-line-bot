import ast
import unittest
from pathlib import Path


class AppStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = Path(__file__).resolve().parent.parent / "app.py"
        cls.source = cls.path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_existing_entry_points_remain(self):
        functions = {
            node.name for node in ast.walk(self.tree) if isinstance(node, ast.FunctionDef)
        }
        expected = {
            "webhook",
            "start_setup",
            "handle_setup_answer",
            "handle_recipe_selection",
            "handle_normal_message",
            "handle_stock_register",
            "handle_stock_add",
            "handle_stock_use",
            "handle_stock_list",
            "parse_stock_lines",
            "ensure_profile",
            "save_profile",
            "save_stock_item",
            "add_stock_quantity",
            "subtract_stock_quantity",
            "get_profile",
            "get_recent_logs",
            "get_stocks",
            "save_meal_log",
            "generate_reply",
            "generate_structured_reply",
            "verify_line_signature",
            "reply_to_line",
        }
        self.assertTrue(expected.issubset(functions))

    def test_long_system_prompt_is_not_embedded_in_app(self):
        self.assertNotIn("SYSTEM_PROMPT =", self.source)
        self.assertNotIn("あなたは Family OS です。", self.source)


if __name__ == "__main__":
    unittest.main()
