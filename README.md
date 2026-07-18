# Project Family OS — LINE Bot

Book 0 v1.1 / Book 7 v1.0 の共通AI基盤を、既存の「おやこ時間ごはんAI」へ追加した最小動作版です。統合元は本番相当の `main`（`eba344afbef2eb5087fa616de7d2763f1983fcbc`）で、既存の初期設定、在庫登録・追加・使用・確認、料理候補選択、Supabase記録を残しています。

## 処理経路

通常会話は `ContextBuilder → Safety preflight → Response Mode Router → Structured Output → Memory Candidate filter → LINE表示` の順で処理します。System Promptは `prompts/Family_OS_Work_Handoff_System_Prompt_v1.0.md` から読み込み、バージョンとSHA-256を応答ログへ記録します。

`StructuredResponse.suggested_actions` はLINEで失われないよう、`message` の後へ短い自然文として表示します。料理選択用の `1`〜`3` は、構造化出力と実際のLINE表示の両方に連番候補がある場合だけ有効になります。

## 保存ポリシー

- `meal_logs`: 食事、献立、在庫、レシピに関する会話だけを保存します。食事と無関係な家族関係、感情、夫婦、健康の相談は保存しません。不明確な場合は保存しない側に倒します。
- `memory_candidates`: モデルが候補を返すだけで、自動保存しません。一時的な感情、関係評価、性格、親としての能力、健康推測はフィルタで除外します。
- DBスキーマ変更はありません。

## プロセスメモリの制約

`setup_sessions`、`last_suggestions`、`last_recipes` はプロセスメモリです。`last_recipes` は、選択した料理名、直前に表示したレシピ、表示時刻、判定できる場合の人数をユーザー単位で保持します。

- 再起動やデプロイで消えます。
- 複数Gunicornワーカー間では共有されません。
- 料理候補は既定30分で失効し、一度選択すると削除されます。
- 直前レシピも既定30分で失効し、新しい料理の選択、明確な新規献立相談、初期設定、在庫管理への移動で置き換えまたは削除されます。
- 現状の `gunicorn app:app` は既定1ワーカーを前提にしています。複数ワーカー化する前に、これらの状態を共有ストアへ移してください。

## 環境変数

必須（本番）：

- `CHANNEL_ACCESS_TOKEN`
- `CHANNEL_SECRET`
- `OPENAI_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `APP_ENV=production`

`APP_ENV=production` で `CHANNEL_SECRET` がない場合、アプリは起動時に停止します。`CHANNEL_SECRET` が設定されている環境では `X-Line-Signature` が不正または欠落したWebhookを処理せず400で拒否します。

開発環境では `CHANNEL_SECRET` 未設定時に限り、警告ログを出して署名検証を省略します。これはローカルテスト用です。開発用LINEチャネルのWebhook試験でも `CHANNEL_SECRET` を設定してください。

任意：

- `OPENAI_MODEL`（既定・本番維持値: `gpt-4.1-mini`）
- `FAMILY_OS_PROMPT_PATH`
- `FAMILY_OS_DOMAIN_PROMPT_PATH`
- `MEAL_SUGGESTION_TTL_SECONDS`（既定: `1800`）
- `LOG_LEVEL`（既定: `INFO`）

## ローカル実行とテスト

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/run_behavior_tests.py
```

OpenAI実モデルの12ケース：

```bash
OPENAI_API_KEY=... OPENAI_MODEL=gpt-4.1-mini \
  python scripts/run_behavior_tests.py --live
```

結果は `reports/behavior_test_results.json` と `reports/behavior_test_results.md` に保存されます。実LINE・Supabaseを含む確認手順、本番反映、ロールバックは `docs/PRODUCTION_INTEGRATION.md` を参照してください。
