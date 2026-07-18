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

    def _normal_patches(self, result):
        return (
            patch.object(line_app, "get_profile", return_value={}),
            patch.object(line_app, "get_recent_logs", return_value=[]),
            patch.object(line_app, "get_stocks", return_value=["卵 4個"]),
            patch.object(line_app, "generate_structured_reply", return_value=result),
            patch.object(line_app, "save_meal_log"),
        )

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
        self.assertEqual(save.call_count, 2)
        self.assertEqual(save.call_args.kwargs["selected_menu"], "親子丼")


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
