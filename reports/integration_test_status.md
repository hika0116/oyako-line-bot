# Minimum Working Family OS v1.0 統合テスト状況

- 実行日: 2026-07-18
- 統合元: `eba344afbef2eb5087fa616de7d2763f1983fcbc`
- 対象モデル設定: `gpt-4.1-mini`

## 完了

| テスト | 結果 | 証跡 |
|---|---:|---|
| Python単体・統合・回帰 | PASS | 36/36 |
| 添付12件のオフライン行動テスト | PASS | 12/12、`behavior_test_results.json` |
| `must` / `must_not` / 選択権 / 情報量 | PASS | 全12ケース |
| 不正LINE署名の400拒否 | PASS | Flask Webhook統合テスト |
| 正しいLINE署名の受理 | PASS | Flask Webhook統合テスト |
| 本番でSecret未設定時の起動拒否 | PASS | subprocess起動テスト |
| 非食事相談を`meal_logs`へ保存しない | PASS | モックSupabase統合テスト |
| 食事相談だけを`meal_logs`へ保存 | PASS | モックSupabase統合テスト |
| 候補なしの`1`を料理選択にしない | PASS | Webhook統合テスト |
| 献立候補→番号選択→詳細レシピ | PASS | モックOpenAI/Supabase統合テスト |
| 初期設定フロー | PASS | 回帰テスト |
| 在庫登録・追加・使用・確認 | PASS | 回帰テスト |
| Gunicorn本番設定ロード | PASS | `gunicorn --check-config app:app` |
| 既存関数の差分確認 | PASS | 初期設定・在庫7関数がベースと同一 |

## 外部認証情報待ち

この作業環境では一般用途のWork Secrets／Environment Variables設定機能が公開されておらず、次の変数もすべて未設定だったため、実サービスへは接続していない。秘密値のチャット入力やローカルファイル保存は行っていない。

- `OPENAI_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `CHANNEL_ACCESS_TOKEN`
- `CHANNEL_SECRET`

| 実環境テスト | 状態 | 理由 |
|---|---:|---|
| OpenAI実モデルによる12ケース | BLOCKED | `OPENAI_API_KEY`未設定。`--live`が資格情報不足で終了することを確認 |
| 開発用Supabase接続 | BLOCKED | URL・Key未設定 |
| 開発用LINEチャネル返信 | BLOCKED | Access Token・Channel Secret・受信イベントなし |
| 実サービス上の初期設定→レシピ | BLOCKED | 上記3接続が必要 |
| 実Supabaseで非食事ログ非保存 | BLOCKED | 開発用Supabase資格情報が必要 |
| 実LINEで候補なし`1` | BLOCKED | 開発用LINEチャネルが必要 |
| 外部HTTPからの不正署名拒否 | BLOCKED | 開発用デプロイ先とChannel Secretが必要 |

ローカル代替試験の成功を、上記の実環境試験完了とは扱わない。本番マージ前に `docs/PRODUCTION_INTEGRATION.md` の手順で実施する。

## 秘密情報の事前監査

| 確認項目 | 結果 |
|---|---:|
| `.env` と `.env.*` がgit除外対象 | PASS |
| 認証情報を直接ログ出力するコードがない | PASS |
| 外部SDK例外の本文をログへ出さず例外型だけ記録 | PASS |
| テストレポートへ環境変数値を記録しない | PASS |
| `git diff` / `git status` / `git grep` の秘密形式スキャン | PASS（検出0件） |
| Supabaseが開発専用プロジェクトか確認 | BLOCKED（安全なSecret設定機能なし） |

- 外部API呼び出し: 0回
- 外部テストデータ作成: なし
- 外部テストデータ削除: 対象なし
- PR作成、push、mainマージ、Renderデプロイ: すべて未実施
