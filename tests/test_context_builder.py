import unittest

from family_os.context_builder import ContextBuilder, is_food_related


class ContextBuilderTests(unittest.TestCase):
    def setUp(self):
        self.builder = ContextBuilder()

    def test_missing_information_stays_unknown_or_empty(self):
        context = self.builder.build("こんにちは", timestamp="2026-07-18T00:00:00+00:00")
        self.assertEqual(context["family_profile"]["members"], [])
        self.assertEqual(context["family_profile"]["dietary_restrictions"], [])
        self.assertIsNone(context["current_state"]["time_available_min"])
        self.assertEqual(context["current_state"]["physical_energy"], "unknown")
        self.assertEqual(context["resources"]["food_stock"], [])
        self.assertEqual(context["memory"]["confirmed"], [])

    def test_explicit_profile_stock_time_and_capacity_are_included(self):
        context = self.builder.build(
            "今日は疲れた。30分でご飯を作りたい。",
            profile={
                "family_size": "大人2人",
                "children_info": "生後10ヶ月の子ども1人",
                "allergies": "卵",
                "dislikes": "辛い物",
                "tools": "電子レンジ、炊飯器",
            },
            food_stock=["鶏肉 300g", "じゃがいも 2個"],
            timestamp="2026-07-18T00:00:00+00:00",
        )
        self.assertEqual(context["current_state"]["time_available_min"], 30)
        self.assertEqual(context["current_state"]["physical_energy"], "low")
        self.assertEqual(len(context["family_profile"]["members"]), 2)
        self.assertEqual(context["resources"]["food_stock"], ["鶏肉 300g", "じゃがいも 2個"])
        confirmed_types = {item["type"] for item in context["memory"]["confirmed"]}
        self.assertIn("child_age_or_months", confirmed_types)
        self.assertIn("allergy_or_explicit_restriction", confirmed_types)

    def test_unrelated_request_does_not_receive_food_stock_or_logs(self):
        context = self.builder.build(
            "妻が最近疲れてる。",
            food_stock=["鶏肉"],
            recent_logs=[{"message": "昨日の献立", "suggestions": "カレー"}],
            timestamp="2026-07-18T00:00:00+00:00",
        )
        self.assertEqual(context["resources"]["food_stock"], [])
        self.assertEqual(context["recent_confirmed_context"], [])

    def test_changed_preference_is_not_silently_resolved(self):
        context = self.builder.build(
            "以前は辛い物が好きだったけど、今は控えている。",
            profile={"dislikes": "辛い物は好き"},
            timestamp="2026-07-18T00:00:00+00:00",
        )
        self.assertEqual(len(context["memory"]["conflicting"]), 1)
        self.assertIn("今は控えている", context["memory"]["conflicting"][0]["latest_explicit_statement"])

    def test_food_log_classifier_is_conservative_and_can_use_confirmed_stock(self):
        self.assertTrue(is_food_related("今週の献立を考えて"))
        self.assertTrue(is_food_related("卵で何作れる？", ["卵 6個"]))
        self.assertFalse(is_food_related("夫と意見が合わない"))
        self.assertFalse(is_food_related("頭が痛くて不安"))


if __name__ == "__main__":
    unittest.main()
