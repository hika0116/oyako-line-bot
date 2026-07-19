import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import Mock, patch

import app as line_app
from family_os.meal_plan import MealPlan, estimate_elapsed_minutes
from family_os.schema import StructuredResponse, SuggestedAction


def structured(
    *,
    mode="PROPOSE",
    message="返答",
    actions=None,
    safety="none",
):
    return StructuredResponse(
        response_mode=mode,
        safety_level=safety,
        message=message,
        suggested_actions=actions or [],
        prompt_version="1.0",
    )


def whole_meal_plan(**overrides):
    values = {
        "title": "豚肉と白菜の重ね蒸し定食",
        "meal_type": "定食",
        "staple": "ごはん",
        "main": "豚肉と白菜の重ね蒸し",
        "soup": "白菜のみそ汁",
        "side": "冷ややっこ",
        "shopping_additions": [],
        "low_capacity": False,
        "servings": 2,
        "ingredients": ["豚肉", "白菜", "豆腐", "米"],
        "used_stock_items": ["豚肉", "白菜", "豆腐"],
        "component_minutes": {"staple": 45, "main": 20, "soup": 10, "side": 3},
        "rice_cooker_used": True,
        "ready_rice_used": False,
    }
    values.update(overrides)
    plan = MealPlan(**values)
    plan.estimated_minutes = estimate_elapsed_minutes(plan)
    return plan


class LineSignatureTests(unittest.TestCase):
    def test_valid_signature_is_accepted_and_invalid_is_rejected(self):
        body = b'{"events":[]}'
        secret = "development-secret"
        digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
        signature = base64.b64encode(digest).decode()

        with patch.object(line_app, "LINE_CHANNEL_SECRET", secret):
            self.assertTrue(line_app.verify_line_signature(body, signature))
            self.assertFalse(line_app.verify_line_signature(body, "invalid"))
            self.assertFalse(line_app.verify_line_signature(body, None))

    def test_webhook_rejects_invalid_signature_before_processing(self):
        body = json.dumps({"events": []}).encode()
        with patch.object(line_app, "LINE_CHANNEL_SECRET", "secret"):
            response = line_app.app.test_client().post(
                "/webhook",
                data=body,
                content_type="application/json",
                headers={"X-Line-Signature": "invalid"},
            )
        self.assertEqual(response.status_code, 400)

    def test_webhook_accepts_valid_signature(self):
        body = json.dumps({"events": []}, separators=(",", ":")).encode()
        secret = "secret"
        signature = base64.b64encode(
            hmac.new(secret.encode(), body, hashlib.sha256).digest()
        ).decode()
        with patch.object(line_app, "LINE_CHANNEL_SECRET", secret):
            response = line_app.app.test_client().post(
                "/webhook",
                data=body,
                content_type="application/json",
                headers={"X-Line-Signature": signature},
            )
        self.assertEqual(response.status_code, 200)

    def test_missing_secret_is_allowed_only_in_development(self):
        with patch.object(line_app, "LINE_CHANNEL_SECRET", None):
            with patch.object(line_app, "APP_ENV", "development"):
                self.assertTrue(line_app.verify_line_signature(b"body", None))
            with patch.object(line_app, "APP_ENV", "production"):
                self.assertFalse(line_app.verify_line_signature(b"body", None))

    def test_production_import_fails_without_channel_secret(self):
        env = os.environ.copy()
        env["APP_ENV"] = "production"
        env.pop("CHANNEL_SECRET", None)
        root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [sys.executable, "-c", "import app"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CHANNEL_SECRET is required", result.stderr)


class MealLogAndSuggestionStateTests(unittest.TestCase):
    def setUp(self):
        line_app.last_suggestions.clear()
        line_app.last_recipes.clear()

    def _normal_patches(self, result):
        return (
            patch.object(line_app, "get_profile", return_value={}),
            patch.object(line_app, "get_recent_logs", return_value=[]),
            patch.object(line_app, "get_stocks", return_value=["卵 4個"]),
            patch.object(line_app, "generate_structured_reply", return_value=result),
            patch.object(line_app, "save_meal_log"),
        )

    def _store_whole_meal(self, user_id="u1"):
        plan = whole_meal_plan()
        line_app._set_last_recipe(
            user_id,
            selected_dish=plan.title,
            candidate_text=plan.compact_action(),
            recipe_text=f"{plan.summary()}\n\n1. みそ汁の湯を沸かす",
            servings=2,
            meal_plan=plan,
        )
        return plan

    def _store_minimal_meal(self, user_id="u1"):
        plan = whole_meal_plan(
            title="豚肉と白菜のフライパン蒸し定食",
            main="豚肉と白菜のフライパン蒸し",
            soup="",
            side="",
            ingredients=["豚肉", "白菜", "米"],
            used_stock_items=["豚肉", "白菜"],
            component_minutes={"staple": 45, "main": 15, "soup": 0, "side": 0},
        )
        line_app._set_last_recipe(
            user_id,
            selected_dish=plan.title,
            candidate_text=plan.compact_action(),
            recipe_text=f"{plan.summary()}\n\n1. 主菜を15分で作る",
            servings=2,
            meal_plan=plan,
        )
        return plan

    def test_non_food_conversation_is_not_saved_to_meal_logs(self):
        result = structured(mode="LISTEN", message="それはしんどかったですね。")
        patches = self._normal_patches(result)
        with patches[0], patches[1], patches[2], patches[3], patches[4] as save:
            reply = line_app.handle_normal_message("u1", "夫と意見が合わなくてつらい。")
        self.assertIn("しんどかった", reply)
        save.assert_not_called()
        self.assertNotIn("u1", line_app.last_suggestions)

    def test_health_conversation_is_not_saved_to_meal_logs(self):
        result = structured(mode="CLARIFY", message="体調が心配ですね。")
        patches = self._normal_patches(result)
        with patches[0], patches[1], patches[2], patches[3], patches[4] as save:
            line_app.handle_normal_message("u1", "頭が痛くて体調が悪い。")
        save.assert_not_called()

    def test_food_conversation_is_saved_and_real_candidates_become_valid(self):
        actions = [
            SuggestedAction("1", "low", "卵とじ丼"),
            SuggestedAction("2", "low", "卵スープ"),
        ]
        result = structured(message="卵を使うなら、この2つです。", actions=actions)
        patches = self._normal_patches(result)
        with patches[0], patches[1], patches[2], patches[3], patches[4] as save:
            reply = line_app.handle_normal_message("u1", "卵で何作れる？")
        save.assert_called_once()
        self.assertIn("1. 卵とじ丼", reply)
        self.assertEqual(
            line_app._get_valid_meal_suggestions("u1")["candidates"]["2"],
            "卵スープ",
        )

    def test_vague_meal_consultation_passes_stock_through_normal_line_flow(self):
        result = structured(
            message="登録在庫から選べます。",
            actions=[
                SuggestedAction("1", "low", "卵焼き（卵を使用。買い足し：なし）"),
                SuggestedAction("2", "low", "豆腐煮（豆腐を使用。買い足し：なし）"),
            ],
        )
        with patch.object(line_app, "get_profile", return_value={}), \
             patch.object(line_app, "get_recent_logs", return_value=[]), \
             patch.object(line_app, "get_stocks", return_value=["卵 14個", "豆腐 2丁"]), \
             patch.object(line_app, "generate_structured_reply", return_value=result) as generate, \
             patch.object(line_app, "save_meal_log") as save:
            reply = line_app.handle_normal_message("u1", "今日どうしよう")

        context = generate.call_args.kwargs["context"]
        self.assertEqual(context["resources"]["food_stock"], ["卵 14個", "豆腐 2丁"])
        self.assertIn("1. 卵焼き", reply)
        self.assertIn("2. 豆腐煮", reply)
        save.assert_called_once()
        self.assertEqual(
            line_app._get_valid_meal_suggestions("u1")["candidates"]["1"],
            "卵焼き（卵を使用。買い足し：なし）",
        )

    def test_listen_or_non_numbered_response_never_updates_candidates(self):
        numeric_listen = structured(
            mode="LISTEN",
            message="疲れていますね。",
            actions=[SuggestedAction("1", "minimum", "冷凍ご飯を使う")],
        )
        rendered = numeric_listen.user_message()
        line_app._set_meal_suggestions("u1", numeric_listen, rendered, food_related=True)
        self.assertNotIn("u1", line_app.last_suggestions)

        plain_proposal = structured(
            mode="PROPOSE",
            message="今日は休みましょう。",
            actions=[SuggestedAction("最小案", "minimum", "惣菜を使う")],
        )
        line_app._set_meal_suggestions("u1", plain_proposal, plain_proposal.user_message(), food_related=True)
        self.assertNotIn("u1", line_app.last_suggestions)

    def test_expired_candidates_are_invalidated(self):
        line_app.last_suggestions["u1"] = {
            "rendered_text": "1. 丼",
            "candidates": {"1": "丼"},
            "expires_at": time.time() - 1,
        }
        self.assertIsNone(line_app._get_valid_meal_suggestions("u1"))
        self.assertNotIn("u1", line_app.last_suggestions)

    def test_number_without_candidates_asks_one_question_and_does_not_call_ai(self):
        event = {
            "events": [{
                "type": "message",
                "replyToken": "reply-token",
                "source": {"userId": "u1"},
                "message": {"type": "text", "text": "1"},
            }]
        }
        with patch.object(line_app, "LINE_CHANNEL_SECRET", None), \
             patch.object(line_app, "APP_ENV", "development"), \
             patch.object(line_app, "ensure_profile"), \
             patch.object(line_app, "handle_recipe_selection") as recipe, \
             patch.object(line_app, "handle_normal_message") as normal, \
             patch.object(line_app, "reply_to_line") as reply:
            response = line_app.app.test_client().post("/webhook", json=event)
        self.assertEqual(response.status_code, 200)
        recipe.assert_not_called()
        normal.assert_not_called()
        text = reply.call_args.args[1]
        self.assertEqual(text.count("？") + text.count("?"), 1)

    def test_proposal_selection_and_recipe_is_single_use(self):
        proposal = structured(
            message="候補です。",
            actions=[
                SuggestedAction("1", "low", "親子丼"),
                SuggestedAction("2", "low", "卵うどん"),
            ],
        )
        recipe = structured(mode="ACT", message="親子丼は、卵2個を使って作れます。")
        with patch.object(line_app, "get_profile", return_value={}), \
             patch.object(line_app, "get_recent_logs", return_value=[]), \
             patch.object(line_app, "get_stocks", return_value=["卵 4個"]), \
             patch.object(line_app, "generate_structured_reply", side_effect=[proposal, recipe]), \
             patch.object(line_app, "save_meal_log") as save:
            line_app.handle_normal_message("u1", "今日のご飯を考えて")
            detail = line_app.handle_recipe_selection("u1", "1", "1")
        self.assertIn("卵2個", detail)
        self.assertNotIn("u1", line_app.last_suggestions)
        self.assertEqual(line_app.last_recipes["u1"]["selected_dish"], "親子丼")
        self.assertEqual(save.call_count, 2)
        self.assertEqual(save.call_args.kwargs["selected_menu"], "親子丼")

    def test_selected_recipe_followup_keeps_dish_and_adjusts_servings(self):
        proposal = structured(
            message="候補です。",
            actions=[
                SuggestedAction(
                    "1",
                    "low",
                    "豚肉とじゃがいもの炒め煮（豚肉・じゃがいもを使用。買い足し：なし）",
                ),
                SuggestedAction(
                    "2",
                    "low",
                    "鶏肉と白菜の煮物（鶏肉・白菜を使用。買い足し：なし）",
                ),
            ],
        )
        recipe = structured(
            mode="ACT",
            message=(
                "豚肉とじゃがいもの炒め煮（2人分）\n"
                "材料：豚肉300g、じゃがいも4個、たまねぎ1個"
            ),
        )
        adjusted = structured(
            mode="ACT",
            message=(
                "豚肉とじゃがいもの炒め煮（1人分）\n"
                "材料：豚肉150g、じゃがいも2個、たまねぎ1/2個"
            ),
            actions=[SuggestedAction("1", "low", "納豆ごはん")],
        )
        stocks = ["豚肉 300g", "鶏肉 300g", "じゃがいも 4個", "白菜 1玉"]
        with patch.object(line_app, "get_profile", return_value={}), \
             patch.object(line_app, "get_recent_logs", return_value=[]), \
             patch.object(line_app, "get_stocks", return_value=stocks), \
             patch.object(
                 line_app,
                 "generate_structured_reply",
                 side_effect=[proposal, recipe, adjusted],
             ) as generate, \
             patch.object(line_app, "save_meal_log") as save:
            line_app.handle_normal_message("u1", "今日どうしよ")
            line_app.handle_recipe_selection("u1", "1", "1")
            reply = line_app.handle_normal_message("u1", "今日は1人分にして")

        self.assertIn("豚肉とじゃがいもの炒め煮", reply)
        self.assertIn("豚肉150g", reply)
        self.assertIn("じゃがいも2個", reply)
        self.assertNotIn("納豆ごはん", reply)
        self.assertNotIn("u1", line_app.last_suggestions)
        self.assertEqual(line_app.last_recipes["u1"]["servings"], 1)
        self.assertEqual(save.call_count, 3)
        followup_instructions = generate.call_args_list[2].kwargs["additional_instructions"]
        self.assertIn("直前に表示した料理", followup_instructions)
        self.assertIn("豚肉300g", followup_instructions)
        self.assertIn("新しい番号選択候補は提示しない", followup_instructions)

    def test_recipe_followup_without_state_asks_once_and_skips_new_candidates(self):
        with patch.object(line_app, "generate_structured_reply") as generate:
            reply = line_app.handle_normal_message("u1", "1人分にして")

        generate.assert_not_called()
        self.assertIn("どの料理", reply)
        self.assertEqual(reply.count("？") + reply.count("?"), 1)
        self.assertNotRegex(reply, r"(?m)^[1-3][.．、:：)]")
        self.assertNotIn("u1", line_app.last_suggestions)

    def test_last_recipe_expires_and_inventory_feature_clears_it(self):
        line_app.last_recipes["u1"] = {
            "selected_dish": "親子丼",
            "candidate_text": "親子丼",
            "recipe_text": "親子丼（2人分）",
            "displayed_at": time.time() - 2000,
            "expires_at": time.time() - 1,
            "servings": 2,
        }
        self.assertIsNone(line_app._get_valid_last_recipe("u1"))
        self.assertNotIn("u1", line_app.last_recipes)

        line_app._set_last_recipe(
            "u1",
            selected_dish="親子丼",
            candidate_text="親子丼",
            recipe_text="親子丼（2人分）",
        )
        with patch.object(line_app, "get_stocks", return_value=["卵 4個"]):
            line_app.handle_stock_list("u1")
        self.assertNotIn("u1", line_app.last_recipes)

    def test_new_meal_consultation_replaces_previous_recipe_state(self):
        line_app._set_last_recipe(
            "u1",
            selected_dish="親子丼",
            candidate_text="親子丼",
            recipe_text="親子丼（2人分）",
        )
        proposal = structured(
            message="新しい候補です。",
            actions=[
                SuggestedAction("1", "low", "豚肉炒め（豚肉を使用。買い足し：なし）"),
                SuggestedAction("2", "low", "鶏肉煮（鶏肉を使用。買い足し：なし）"),
            ],
        )
        with patch.object(line_app, "get_profile", return_value={}), \
             patch.object(line_app, "get_recent_logs", return_value=[]), \
             patch.object(line_app, "get_stocks", return_value=["豚肉 300g", "鶏肉 300g"]), \
             patch.object(line_app, "generate_structured_reply", return_value=proposal), \
             patch.object(line_app, "save_meal_log"):
            line_app.handle_normal_message("u1", "今日どうしよ")

        self.assertNotIn("u1", line_app.last_recipes)
        self.assertIsNotNone(line_app._get_valid_meal_suggestions("u1"))

    def test_whole_meal_selection_shows_components_and_parallel_workflow(self):
        selected = whole_meal_plan()
        other = whole_meal_plan(title="鶏肉の別定食", main="鶏肉の蒸し物")
        proposal = structured(
            message="候補です。",
            actions=[
                SuggestedAction("1", "low", "", meal_plan=selected),
                SuggestedAction("2", "low", "", meal_plan=other),
            ],
        )
        detail = structured(
            mode="ACT",
            message=(
                "材料：豚肉300g、白菜1/4玉、豆腐1/2丁\n"
                "同時調理：1. みそ汁の湯を沸かす 2. その間に主菜を加熱する "
                "3. 加熱中に冷ややっこを盛る"
            ),
        )
        with patch.object(line_app, "get_profile", return_value={}), \
             patch.object(line_app, "get_recent_logs", return_value=[]), \
             patch.object(line_app, "get_stocks", return_value=["豚肉 300g", "白菜 1玉", "豆腐 1丁"]), \
             patch.object(line_app, "generate_structured_reply", side_effect=[proposal, detail]), \
             patch.object(line_app, "save_meal_log"):
            initial = line_app.handle_normal_message("u1", "今日どうしよ")
            reply = line_app.handle_recipe_selection("u1", "1", "1")

        self.assertIn("約25分｜買い足しなし", initial)
        self.assertNotIn("豚肉300g", initial)
        self.assertIn("主食：ごはん", reply)
        self.assertIn("主菜：豚肉と白菜の重ね蒸し", reply)
        self.assertIn("汁物：白菜のみそ汁", reply)
        self.assertIn("副菜：冷ややっこ", reply)
        self.assertIn("同時調理", reply)
        self.assertIn("※炊飯時間は含みません。", reply)
        self.assertNotIn("鶏肉の別定食", reply)
        self.assertEqual(line_app.last_recipes["u1"]["meal_plan"]["title"], selected.title)

    def test_new_rice_selection_explains_separate_cooking_and_removes_false_timing(self):
        selected = self._store_minimal_meal()
        line_app.last_recipes.clear()
        proposal = structured(
            message="候補です。",
            actions=[SuggestedAction("1", "low", "", meal_plan=selected)],
        )
        detail = structured(
            mode="ACT",
            message=(
                "1. 米を研いで炊飯器で炊飯を始めます。\n"
                "2. フライパンで主菜を15分加熱します。\n"
                "フライパン蒸しとスープが完成する頃にごはんも炊きあがります。"
            ),
        )
        with patch.object(line_app, "get_profile", return_value={}), \
             patch.object(line_app, "get_recent_logs", return_value=[]), \
             patch.object(line_app, "get_stocks", return_value=["豚肉 300g", "白菜 1玉"]), \
             patch.object(line_app, "generate_structured_reply", side_effect=[proposal, detail]), \
             patch.object(line_app, "save_meal_log"):
            line_app.handle_normal_message("u1", "今日どうしよ")
            reply = line_app.handle_recipe_selection("u1", "1", "1")

        self.assertIn("別途炊飯時間が必要", reply)
        self.assertIn("以下の約15分は、おかず・汁物・副菜の調理時間", reply)
        self.assertNotIn("完成する頃にごはんも炊きあが", reply)

    def test_ready_rice_selection_includes_reheating_and_never_starts_new_cooking(self):
        selected = whole_meal_plan(
            title="豚肉と卵の簡単丼",
            meal_type="丼",
            staple="ごはん",
            main="豚肉と卵の簡単丼",
            soup="",
            side="",
            ingredients=["豚肉", "卵", "米"],
            used_stock_items=["豚肉", "卵"],
            component_minutes={"staple": 45, "main": 5, "soup": 0, "side": 0},
        )
        proposal = structured(
            message="候補です。",
            actions=[SuggestedAction("1", "low", "", meal_plan=selected)],
        )
        detail = structured(
            mode="ACT",
            message="米を研いで炊飯器で炊飯を開始します。\n冷凍ごはんを5分以内に温めます。",
        )
        with patch.object(line_app, "get_profile", return_value={}), \
             patch.object(line_app, "get_recent_logs", return_value=[]), \
             patch.object(line_app, "get_stocks", return_value=["冷凍ごはん 1食", "豚肉 100g", "卵 1個"]), \
             patch.object(line_app, "generate_structured_reply", side_effect=[proposal, detail]), \
             patch.object(line_app, "save_meal_log"):
            line_app.handle_normal_message("u1", "今日どうしよ")
            reply = line_app.handle_recipe_selection("u1", "1", "1")

        state = line_app.last_recipes["u1"]["meal_plan"]
        self.assertTrue(state["ready_rice_used"])
        self.assertEqual(state["component_minutes"]["staple"], 5)
        self.assertIn("炊いたごはん、冷凍ごはん等を使用する場合", reply)
        self.assertNotIn("米を研", reply)
        self.assertNotIn("炊飯を開始", reply)

    def test_whole_meal_followup_removes_only_soup_without_new_candidates(self):
        self._store_whole_meal()
        adjusted = structured(
            mode="ACT",
            message="主食・主菜・副菜はそのままで、汁物を外しました。",
            actions=[SuggestedAction("1", "low", "別の献立")],
        )
        with patch.object(line_app, "get_profile", return_value={}), \
             patch.object(line_app, "get_recent_logs", return_value=[]), \
             patch.object(line_app, "get_stocks", return_value=["豚肉 300g", "白菜 1玉", "豆腐 1丁"]), \
             patch.object(line_app, "generate_structured_reply", return_value=adjusted), \
             patch.object(line_app, "save_meal_log"):
            reply = line_app.handle_normal_message("u1", "汁物はいらない")

        state_plan = line_app.last_recipes["u1"]["meal_plan"]
        self.assertEqual(state_plan["soup"], "")
        self.assertIn("主菜：豚肉と白菜の重ね蒸し", reply)
        self.assertIn("副菜：冷ややっこ", reply)
        self.assertNotIn("汁物：", reply)
        self.assertNotIn("別の献立", reply)
        self.assertNotIn("u1", line_app.last_suggestions)

    def test_side_addition_directly_updates_previous_meal_without_shortening(self):
        previous = self._store_minimal_meal()
        adjusted = structured(
            mode="ACT",
            message="副菜は食卓に出す器の中で和えると、洗い物を一つ減らせます。",
            actions=[SuggestedAction("1", "low", "別の副菜")],
        )
        with patch.object(line_app, "get_profile", return_value={}), \
             patch.object(line_app, "get_recent_logs", return_value=[]), \
             patch.object(line_app, "get_stocks", return_value=["豚肉 300g", "白菜 1玉", "ズッキーニ 1本"]), \
             patch.object(line_app, "generate_structured_reply", return_value=adjusted), \
             patch.object(line_app, "save_meal_log"):
            reply = line_app.handle_normal_message("u1", "副菜を追加して")

        state = line_app.last_recipes["u1"]["meal_plan"]
        self.assertEqual(state["title"], previous.title)
        self.assertEqual(state["staple"], previous.staple)
        self.assertEqual(state["main"], previous.main)
        self.assertEqual(state["side"], "ズッキーニの簡単和え")
        self.assertEqual(state["shopping_additions"], [])
        self.assertGreaterEqual(state["estimated_minutes"], previous.estimated_minutes)
        self.assertIn("副菜：ズッキーニの簡単和え", reply)
        self.assertNotIn("1. 別の副菜", reply)
        self.assertNotIn("u1", line_app.last_suggestions)

    def test_soup_addition_directly_updates_previous_meal(self):
        previous = self._store_minimal_meal()
        adjusted = structured(
            mode="ACT",
            message="白菜は耐熱容器で電子レンジ調理し、そのまま器にします。",
            actions=[SuggestedAction("1", "low", "別の汁物")],
        )
        with patch.object(line_app, "get_profile", return_value={}), \
             patch.object(line_app, "get_recent_logs", return_value=[]), \
             patch.object(line_app, "get_stocks", return_value=["豚肉 300g", "白菜 1玉"]), \
             patch.object(line_app, "generate_structured_reply", return_value=adjusted), \
             patch.object(line_app, "save_meal_log"):
            reply = line_app.handle_normal_message("u1", "汁物を追加して")

        state = line_app.last_recipes["u1"]["meal_plan"]
        self.assertEqual(state["title"], previous.title)
        self.assertEqual(state["soup"], "白菜の簡単スープ")
        self.assertGreaterEqual(state["estimated_minutes"], previous.estimated_minutes)
        self.assertIn("汁物：白菜の簡単スープ", reply)
        self.assertNotIn("1. 別の汁物", reply)

    def test_component_addition_excludes_profile_allergy(self):
        self._store_minimal_meal()
        adjusted = structured(mode="ACT", message="安全な在庫の副菜を追加しました。")
        with patch.object(line_app, "get_profile", return_value={"allergies": "ズッキーニアレルギー"}), \
             patch.object(line_app, "get_recent_logs", return_value=[]), \
             patch.object(line_app, "get_stocks", return_value=["豚肉 300g", "白菜 1玉", "ズッキーニ 1本", "キャベツ 1玉"]), \
             patch.object(line_app, "generate_structured_reply", return_value=adjusted), \
             patch.object(line_app, "save_meal_log"):
            reply = line_app.handle_normal_message("u1", "副菜を追加して")

        state = line_app.last_recipes["u1"]["meal_plan"]
        self.assertNotIn("ズッキーニ", state["side"])
        self.assertNotIn("ズッキーニ", reply)
        self.assertIn("副菜：キャベツの簡単和え", reply)

    def test_side_replacement_synchronizes_component_and_material_state(self):
        self._store_whole_meal()
        adjusted = structured(mode="ACT", message="副菜だけ在庫の一品へ変えました。")
        with patch.object(line_app, "get_profile", return_value={}), \
             patch.object(line_app, "get_recent_logs", return_value=[]), \
             patch.object(line_app, "get_stocks", return_value=["豚肉 300g", "白菜 1玉", "ズッキーニ 1本"]), \
             patch.object(line_app, "generate_structured_reply", return_value=adjusted), \
             patch.object(line_app, "save_meal_log"):
            line_app.handle_normal_message("u1", "副菜だけ変えて")

        state = line_app.last_recipes["u1"]["meal_plan"]
        self.assertEqual(state["side"], "ズッキーニの簡単和え")
        self.assertNotIn("豆腐", state["ingredients"])
        self.assertNotIn("豆腐", state["used_stock_items"])
        self.assertIn("ズッキーニ", state["ingredients"])

    def test_unrealistic_washing_advice_is_removed(self):
        self._store_minimal_meal()
        adjusted = structured(
            mode="ACT",
            message=(
                "鍋はスープ用に使い、フライパンは蒸し物に使い分けると洗い物を減らせます。\n"
                "フライパンに残った蒸し汁で生のズッキーニを和えます。"
            ),
        )
        with patch.object(line_app, "get_profile", return_value={}), \
             patch.object(line_app, "get_recent_logs", return_value=[]), \
             patch.object(line_app, "get_stocks", return_value=["豚肉 300g", "白菜 1玉"]), \
             patch.object(line_app, "generate_structured_reply", return_value=adjusted), \
             patch.object(line_app, "save_meal_log"):
            reply = line_app.handle_normal_message("u1", "洗い物を減らして")

        self.assertNotIn("使い分けると洗い物", reply)
        self.assertNotIn("残った蒸し汁", reply)

    def test_whole_meal_followup_applies_twenty_minute_limit(self):
        self._store_whole_meal()
        adjusted = structured(mode="ACT", message="20分以内で終わる順番に調整しました。")
        with patch.object(line_app, "get_profile", return_value={}), \
             patch.object(line_app, "get_recent_logs", return_value=[]), \
             patch.object(line_app, "get_stocks", return_value=["豚肉 300g", "白菜 1玉", "豆腐 1丁"]), \
             patch.object(line_app, "generate_structured_reply", return_value=adjusted), \
             patch.object(line_app, "save_meal_log"):
            reply = line_app.handle_normal_message("u1", "20分以内にして")

        self.assertIn("目安時間：約20分", reply)
        self.assertEqual(line_app.last_recipes["u1"]["meal_plan"]["estimated_minutes"], 20)

    def test_whole_meal_followup_changes_only_staple_to_stocked_noodles(self):
        self._store_whole_meal()
        adjusted = structured(mode="ACT", message="主菜を活かして麺中心に組み替えました。")
        with patch.object(line_app, "get_profile", return_value={}), \
             patch.object(line_app, "get_recent_logs", return_value=[]), \
             patch.object(line_app, "get_stocks", return_value=["豚肉 300g", "白菜 1玉", "冷凍うどん 2玉"]), \
             patch.object(line_app, "generate_structured_reply", return_value=adjusted), \
             patch.object(line_app, "save_meal_log"):
            reply = line_app.handle_normal_message("u1", "ごはんがないから麺にして")

        state_plan = line_app.last_recipes["u1"]["meal_plan"]
        self.assertEqual(state_plan["meal_type"], "麺")
        self.assertEqual(state_plan["staple"], "冷凍うどん")
        self.assertEqual(state_plan["title"], "豚肉と白菜のあんかけうどん")
        self.assertEqual(state_plan["main"], "豚肉と白菜のあんかけうどん")
        self.assertEqual(state_plan["side"], "")
        self.assertEqual(state_plan["shopping_additions"], [])
        self.assertIn("主食兼主菜：豚肉と白菜のあんかけうどん", reply)
        self.assertNotIn("主菜：豚肉と白菜の重ね蒸し", reply)
        self.assertNotIn("麺類", reply)
        self.assertNotIn("（麺に変更）", reply)

    def test_no_stocked_noodle_chooses_one_specific_minimal_purchase(self):
        self._store_minimal_meal()
        adjusted = structured(mode="ACT", message="具体的なうどん料理に組み替えました。")
        with patch.object(line_app, "get_profile", return_value={}), \
             patch.object(line_app, "get_recent_logs", return_value=[]), \
             patch.object(line_app, "get_stocks", return_value=["豚肉 300g", "白菜 1玉"]), \
             patch.object(line_app, "generate_structured_reply", return_value=adjusted), \
             patch.object(line_app, "save_meal_log"):
            reply = line_app.handle_normal_message("u1", "ごはんがないから麺にして")

        state = line_app.last_recipes["u1"]["meal_plan"]
        self.assertEqual(state["meal_type"], "麺")
        self.assertEqual(state["staple"], "冷凍うどん")
        self.assertEqual(state["title"], "豚肉と白菜のあんかけうどん")
        self.assertEqual(state["shopping_additions"], ["冷凍うどん 1玉"])
        self.assertIn("買い足し：冷凍うどん 1玉", reply)
        self.assertNotIn("麺類", reply)

    def test_explicit_udon_request_overrides_other_stocked_noodle(self):
        self._store_minimal_meal()
        adjusted = structured(mode="ACT", message="うどん一品へ組み替えました。")
        with patch.object(line_app, "get_profile", return_value={}), \
             patch.object(line_app, "get_recent_logs", return_value=[]), \
             patch.object(line_app, "get_stocks", return_value=["豚肉 300g", "白菜 1玉", "パスタ 200g"]), \
             patch.object(line_app, "generate_structured_reply", return_value=adjusted), \
             patch.object(line_app, "save_meal_log"):
            line_app.handle_normal_message("u1", "うどんにして")

        state = line_app.last_recipes["u1"]["meal_plan"]
        self.assertEqual(state["staple"], "冷凍うどん")
        self.assertIn("うどん", state["title"])
        self.assertEqual(state["shopping_additions"], ["冷凍うどん 1玉"])
        self.assertNotIn("パスタ", state["title"])

    def test_noodle_state_supports_subsequent_serving_change(self):
        self._store_minimal_meal()
        noodle_reply = structured(mode="ACT", message="冷凍うどん1玉で作ります。")
        serving_reply = structured(mode="ACT", message="1人分の材料へ調整しました。")
        with patch.object(line_app, "get_profile", return_value={}), \
             patch.object(line_app, "get_recent_logs", return_value=[]), \
             patch.object(line_app, "get_stocks", return_value=["豚肉 300g", "白菜 1玉", "冷凍うどん 1玉"]), \
             patch.object(line_app, "generate_structured_reply", side_effect=[noodle_reply, serving_reply]), \
             patch.object(line_app, "save_meal_log"):
            line_app.handle_normal_message("u1", "ごはんがないから麺にして")
            reply = line_app.handle_normal_message("u1", "1人分にして")

        state = line_app.last_recipes["u1"]["meal_plan"]
        self.assertEqual(state["meal_type"], "麺")
        self.assertEqual(state["title"], "豚肉と白菜のあんかけうどん")
        self.assertEqual(state["servings"], 1)
        self.assertIn("主食兼主菜：豚肉と白菜のあんかけうどん", reply)
        self.assertNotIn("u1", line_app.last_suggestions)

    def test_whole_meal_followup_adjusts_all_components_to_one_serving(self):
        self._store_whole_meal()
        adjusted = structured(
            mode="ACT",
            message="1人分：豚肉150g、白菜1/8玉、豆腐1/4丁に調整しました。",
        )
        with patch.object(line_app, "get_profile", return_value={}), \
             patch.object(line_app, "get_recent_logs", return_value=[]), \
             patch.object(line_app, "get_stocks", return_value=["豚肉 300g", "白菜 1玉", "豆腐 1丁"]), \
             patch.object(line_app, "generate_structured_reply", return_value=adjusted), \
             patch.object(line_app, "save_meal_log"):
            reply = line_app.handle_normal_message("u1", "1人分にして")

        self.assertIn("1人分", reply)
        self.assertEqual(line_app.last_recipes["u1"]["meal_plan"]["servings"], 1)
        self.assertNotIn("u1", line_app.last_suggestions)


class ExistingFeatureRegressionTests(unittest.TestCase):
    def test_existing_production_model_default_is_preserved(self):
        self.assertEqual(line_app.OPENAI_MODEL, "gpt-4.1-mini")

    def test_setup_flow_reaches_profile_save(self):
        user_id = "setup-user"
        line_app.setup_sessions.clear()
        line_app.start_setup(user_id)
        answers = [
            "大人2人",
            "生後10ヶ月の子ども1人",
            "2",
            "5,7",
            "週2回",
            "少し使いたい",
            "卵アレルギー",
        ]
        with patch.object(line_app, "save_profile", return_value=True) as save:
            for answer in answers:
                final_text = line_app.handle_setup_answer(user_id, answer)
        save.assert_called_once()
        self.assertNotIn(user_id, line_app.setup_sessions)
        self.assertIn("初期設定できました", final_text)

    def test_save_profile_upserts_placeholder_by_user_id(self):
        fake_supabase = Mock()
        table = fake_supabase.table.return_value
        table.upsert.return_value.execute.return_value = Mock()
        profile = {
            "family_size": "大人2人",
            "children_info": "子ども1人",
            "cooking_level": "簡単な家庭料理ならできる",
            "tools": "持っていないもの：オーブン",
            "shopping_frequency": "週2回",
            "frozen_style": "少し使いたい",
            "allergies": "なし",
            "dislikes": "なし",
        }

        with patch.object(line_app, "supabase", fake_supabase):
            saved = line_app.save_profile("setup-user", profile)

        self.assertTrue(saved)
        fake_supabase.table.assert_called_once_with("profiles")
        payload = table.upsert.call_args.args[0]
        self.assertEqual(table.upsert.call_args.kwargs["on_conflict"], "user_id")
        self.assertEqual(payload["user_id"], "setup-user")
        self.assertEqual(payload["family_size"], "大人2人")
        self.assertEqual(payload["notes"], "初期設定済み")

    def test_save_profile_failure_returns_false_without_logging_exception_text(self):
        fake_supabase = Mock()
        request = fake_supabase.table.return_value.upsert.return_value
        request.execute.side_effect = RuntimeError("do-not-log-secret")

        with patch.object(line_app, "supabase", fake_supabase), \
             self.assertLogs(line_app.logger, level="ERROR") as captured:
            saved = line_app.save_profile("setup-user", {})

        self.assertFalse(saved)
        logs = "\n".join(captured.output)
        self.assertIn("RuntimeError", logs)
        self.assertNotIn("do-not-log-secret", logs)

    def test_setup_does_not_claim_completion_when_profile_save_fails(self):
        user_id = "setup-user"
        line_app.setup_sessions[user_id] = {
            "step": "allergies_dislikes",
            "data": {"family_size": "大人2人"},
        }

        with patch.object(line_app, "save_profile", return_value=False):
            response = line_app.handle_setup_answer(user_id, "なし")

        self.assertNotIn("初期設定できました", response)
        self.assertIn("保存できませんでした", response)
        self.assertIn(user_id, line_app.setup_sessions)

    def test_stock_register_add_use_and_list_handlers_remain_connected(self):
        with patch.object(line_app, "save_stock_item") as register:
            text = line_app.handle_stock_register("u1", "在庫登録\n卵 10 個")
        register.assert_called_once_with("u1", "卵", "10", "個")
        self.assertIn("在庫を登録", text)

        with patch.object(line_app, "add_stock_quantity") as add:
            line_app.handle_stock_add("u1", "買い物した\n豆腐 2 丁")
        add.assert_called_once_with("u1", "豆腐", "2", "丁")

        with patch.object(line_app, "subtract_stock_quantity") as subtract:
            line_app.handle_stock_use("u1", "使った\n豆腐 1 丁")
        subtract.assert_called_once_with("u1", "豆腐", "1")

        with patch.object(line_app, "get_stocks", return_value=["卵 10個"]):
            self.assertIn("卵 10個", line_app.handle_stock_list("u1"))


if __name__ == "__main__":
    unittest.main()
