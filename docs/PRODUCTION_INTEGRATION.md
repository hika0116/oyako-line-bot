# Minimum Working Family OS v1.0 本番統合手順

## 統合基準

- 本番相当ベース: `hika0116/oyako-line-bot` `main`
- ベースコミット: `eba344afbef2eb5087fa616de7d2763f1983fcbc`
- 本番モデル維持値: `gpt-4.1-mini`
- DBマイグレーション案: `supabase/migrations/202607190001_recipe_catalog.sql`（未適用）

既存 `app.py` を統合元にし、初期設定、`在庫登録`、`買い物した`、`使った`、`在庫確認`、料理番号選択、`profiles` / `stocks` / `meal_logs` の処理を維持している。

## 本番反映前チェック

1. マイグレーションSQLとRLSをレビューし、開発用Supabaseへだけ適用する。
2. `data/recipes_seed_v1.json` をレビューし、開発用カタログへ投入する手順を別工程で確認する。
3. Renderの対象サービスで `CHANNEL_SECRET` を先にSecret環境変数として設定する。
4. `CHANNEL_ACCESS_TOKEN`、`OPENAI_API_KEY`、`SUPABASE_URL`、`SUPABASE_KEY` が開発用ではなく本番用であることを確認する。
5. `APP_ENV=production`、`OPENAI_MODEL=gpt-4.1-mini` を確認する。
6. 全単体テストとオフライン12件を実行する。
7. 開発用認証情報で下記の実環境テストを完了する。
8. PRの差分がベースコミット以降の既存機能を削除していないことを再確認する。

## 開発環境での実環境テスト

### OpenAI 12ケース

```bash
OPENAI_API_KEY=... OPENAI_MODEL=gpt-4.1-mini \
  python scripts/run_behavior_tests.py --live
```

12件それぞれの `must`、`must_not`、選択権、情報量をJSON/Markdownレポートで確認する。

### Supabase

開発用プロジェクトで、テスト専用 `user_id` を使う。

1. `profiles` の取得と初期設定後の更新を確認する。
2. `stocks` へ登録・加算・減算できることを確認する。
3. 食事相談後だけ `meal_logs` が1件増えることを確認する。
4. 夫婦相談または健康相談後に `meal_logs` が増えないことを確認する。
5. テストデータは開発用プロジェクトの運用手順に従って削除する。

### 開発用LINEチャネル

Webhook URLを開発サービスの `/webhook` に設定し、チャネルの `CHANNEL_SECRET` と `CHANNEL_ACCESS_TOKEN` を使う。

1. `初期設定` から全質問へ回答する。
2. `在庫登録` で卵などを登録し、`在庫確認` を送る。
3. `今日のご飯を考えて` を送り、食事区分の確認で `夕食` を選ぶ。
4. 表示された候補番号を選び、選んだ登録レシピだけの材料・工程・参考元が返信されることを確認する。
5. `1人分にして` を送り、OpenAIによる再生成なしで分量が変わることを確認する。
6. `夫と意見が合わない` を送り、一般相談として返答されることを確認する。
7. 新しい候補がない状態で `1` を送り、料理選択に進まず質問が一つだけ返ることを確認する。
8. 署名を改変したHTTPリクエストが400になることを確認する。

実LINEの `replyToken` はLINEから届いたイベントにだけ付与されるため、返信確認は開発用チャネルから実メッセージを送って行う。

## 本番反映

1. 統合ブランチからPRを作成し、`main` の最新変更を取り込む。
2. 自動テスト結果と実環境テスト結果をPRへ添付する。
3. Renderで `CHANNEL_SECRET` が設定済みであることを再確認する。
4. PRを `main` へマージし、Renderのデプロイログで起動成功を確認する。
5. `GET /` の200、署名付きLINEメッセージ1件、献立候補から詳細レシピまでをスモークテストする。
6. `meal_logs` に非食事相談が追加されていないことを確認する。

## ロールバック

コードだけを戻す場合は、統合PRのマージコミットを `git revert` し、新しいリバートPRをマージする。履歴を改変する `reset --hard` や強制pushは使わない。

```bash
git switch -c rollback/mwfos-v1 origin/main
git revert <integration-merge-commit>
git push origin rollback/mwfos-v1
```

Renderで直前の正常デプロイを再デプロイする場合も、リポジトリ側のリバートを同時に行い、次回デプロイで再発しないようにする。レシピカタログのマイグレーションは追加テーブルのみで、コードを戻す際も監査・確認のため即時削除しない。削除が必要な場合は別のレビュー済みdown migrationを作成する。環境変数は `CHANNEL_SECRET` を削除せず維持する。

## 未実施を許容しない項目

本番マージ前に、開発用認証情報を使ったOpenAI、Supabase、LINEの3接続試験をすべて完了し、結果を記録する。認証情報が利用できない環境でのローカル成功だけを、本番統合完了とは扱わない。
