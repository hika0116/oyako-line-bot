# Project Family OS — LINE Bot

Book 0 v1.1 / Book 7 v1.0 の共通AI基盤を、既存の「おやこ時間ごはんAI」へ追加した最小動作版です。統合元は本番相当の `main`（`eba344afbef2eb5087fa616de7d2763f1983fcbc`）で、既存の初期設定、在庫登録・追加・使用・確認、料理候補選択、Supabase記録を残しています。

## 処理経路

通常会話は `ContextBuilder → Safety preflight → Response Mode Router → Structured Output → Memory Candidate filter → LINE表示` の順で処理します。System Promptは `prompts/Family_OS_Work_Handoff_System_Prompt_v1.0.md` から読み込み、バージョンとSHA-256を応答ログへ記録します。

献立相談は第1段階のレシピカタログ経路を使用します。食事区分が不明なら朝食・昼食・弁当・夕食・おつまみを一度だけ確認し、publishedの構造化レシピを在庫・時間・余力・アレルギー・直近履歴で絞ります。料理名、材料、分量、工程、時間、器具、参考元はレシピカタログを正とし、候補検索、番号選択、詳細表示、人数変更ではOpenAIを呼びません。条件に合う登録レシピがなければ、存在しない料理を生成せず件数不足をそのまま伝えます。

カタログはSupabaseの `recipes` と子テーブルを優先します。マイグレーション未適用・一時障害時は、レビュー済みの開発用seed `data/recipes_seed_v1.json` と同じ構造へフォールバックします。外部レシピ本文や未確認URLはseedへ含めていません。

`StructuredResponse.suggested_actions` はLINEで失われないよう、`message` の後へ表示します。料理候補には構造化された `meal_plan` を持たせ、初回は献立タイトル・一食完成時間・買い足し有無だけを表示します。料理選択用の `1`〜`3` は、構造化出力と実際のLINE表示の両方に連番候補がある場合だけ有効になります。

一食完成時間は、炊いたごはん・冷凍ごはん等を使う場合と新たに炊飯する場合を区別します。新規炊飯では炊飯時間を表示時間から除外し、別途必要であることを詳細表示で明記します。基本調味料は既定で常備扱いですが、将来プロフィールから `non_stocked_seasonings` が渡された場合は指定項目だけ買い足しへ戻せます。現時点ではこの値のDB保存は行いません。

## 保存ポリシー

- `meal_logs`: 食事、献立、在庫、レシピに関する会話だけを保存します。食事と無関係な家族関係、感情、夫婦、健康の相談は保存しません。不明確な場合は保存しない側に倒します。
- `memory_candidates`: モデルが候補を返すだけで、自動保存しません。一時的な感情、関係評価、性格、親としての能力、健康推測はフィルタで除外します。
- レシピカタログ用のDB変更案は `supabase/migrations/202607190001_recipe_catalog.sql` にあります。アプリが自動適用することはありません。

## プロセスメモリの制約

`setup_sessions`、`last_suggestions`、`last_recipes`、`pending_meal_requests`、`recent_recipe_history` はプロセスメモリです。`last_recipes` は選択した一食の構成、参照recipe_id、直前に表示したレシピ、表示時刻、人数をユーザー単位で保持します。`pending_meal_requests` は食事区分確認前の疲労・時間・希望条件を保持します。`recent_recipe_history` は同一プロセス内の即時重複抑制用で、マイグレーション適用後は `recipe_proposal_history` にも記録します。

- 再起動やデプロイで消えます。
- 複数Gunicornワーカー間では共有されません。
- 料理候補は既定30分で失効し、一度選択すると削除されます。
- 直前レシピも既定30分で失効し、新しい料理の選択、明確な新規献立相談、初期設定、在庫管理への移動で置き換えまたは削除されます。
- 食事区分のpending状態も同じ30分で失効します。
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
python scripts/generate_monthly_recipe_topics.py --dry-run
```

月次テーマCLIは既定でdry-runで、ネットワーク接続もDB書き込みも行いません。集計済み食材需要JSONを `--aggregates` で渡せますが、`user_id` やプロフィール等の個人情報を含む入力は拒否します。明示的な `--enqueue` を指定した場合だけ `recipe_collection_topics` への登録を試みます。外部レシピ検索は実装していません。

OpenAI実モデルの12ケース：

```bash
OPENAI_API_KEY=... OPENAI_MODEL=gpt-4.1-mini \
  python scripts/run_behavior_tests.py --live
```

結果は `reports/behavior_test_results.json` と `reports/behavior_test_results.md` に保存されます。実LINE・Supabaseを含む確認手順、本番反映、ロールバックは `docs/PRODUCTION_INTEGRATION.md` を参照してください。
