import unittest
from pathlib import Path

from family_os.prompt_loader import load_prompt


class PromptLoaderTests(unittest.TestCase):
    def test_prompt_is_external_and_versioned(self):
        prompt = load_prompt()
        self.assertEqual(prompt.version, "1.0")
        self.assertTrue(prompt.content.startswith("# Family OS System Prompt v1.0"))
        self.assertEqual(len(prompt.digest_sha256), 64)
        self.assertEqual(prompt.path.suffix, ".md")

    def test_empty_prompt_is_rejected(self):
        path = Path(__file__).parent / "_empty_prompt_v9.9.md"
        try:
            path.write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_prompt(path)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
