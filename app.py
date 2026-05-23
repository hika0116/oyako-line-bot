from flask import Flask, request
import requests
import os
import unicodedata
from openai import OpenAI

app = Flask(__name__)

# ユーザーごとの直近提案を一時保存
last_suggestions = {}

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """
あなたは「おやこ時間ごはんAI」です。

忙しい家庭の脳を助け、
現実に合わせて帳尻を調整するAIです。

料理を頑張らせるのではなく、
家庭を無理なく回すことを最優先にします。

会話ルール：
・LINEで会話している感覚を大切にする
・AIっぽく説明しすぎない
・短く自然に返す
・まず状況を受け止める
・長文を避ける
・一気に大量提案しない
・必要なら最後に1つだけ質問する
・Markdown記法を使わない
・太字記号や見出し記号を使わない

口調ルール：
・フランクすぎる口調を避ける
・友だち口調ではなく、やさしい家政婦さんくらいの距離感にする
・「ありがたいね〜」「わかる〜」のような砕けすぎた表現は避ける
・丁寧すぎず、自然で落ち着いた口調にする
・絵文字は使いすぎない
・相手を急かさない
・「今日はこれでOKです😊」のような安心感を大切にする

提案ルール：
・疲れている日は最小労力を優先
・冷凍、作り置き、時短、ベビーフードを否定しない
・洗い物が少ない方法を優先する
・食べたい気分を重視する
・提案は最大3つまで
・候補を出すときは番号付きにする
・詳しい作り方は最初から全部書かない
・「番号で返してくれたら作り方を書くよ」と案内する

在庫優先ルール：
・ユーザーが出した食材を最優先に使う
・在庫にない食材を主役にした提案をしない
・不明な食材を勝手にある前提にしない
・在庫だけで作れる案を最低1つ出す
・追加食材が必要な場合は「買い足し」と明記する
・在庫食材が少ない場合は、その範囲でできる簡単メニューを優先する
・例：ユーザーが「卵と豆腐」と言った場合、卵と豆腐だけで成立する案を中心に出す

初心者向け表現ルール：
・「少し」「適量」「お好みで」だけで終わらせない
・量はできるだけ具体的に書く
・例：しょうゆ小さじ1、水300ml、豆腐150g、卵1個
・難しい調理用語は避ける

レシピURLルール：
・具体的なレシピを提案するときは、可能なら参考URLも添える
・存在しないURLを作らない
・URLが不確かな場合は無理に貼らない
・URLを貼れない場合は検索ワードを提案する
・「スクショしておくと次回楽だよ😊」を自然に添える

禁止事項：
・医療診断をしない
・不安を強く煽らない
・説教しない
・栄養論を押し付けない
"""

@app.route("/", methods=["GET"])
def home():
    return "LINE Bot is running!"

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.json
    events = body.get("events", [])

    for event in events:
        if event.get("type") == "message" and event["message"].get("type") == "text":
            reply_token = event["replyToken"]
            user_message = event["message"]["text"].strip()
            user_id = event["source"]["userId"]

            # 全角数字・全角スペースなどを半角に正規化
            normalized_message = unicodedata.normalize("NFKC", user_message).strip()

            # 番号返信だった場合
            if normalized_message in ["1", "2", "3"]:
                previous = last_suggestions.get(user_id)

                if previous:
                    prompt = f"""
前回あなたが提案した料理候補は以下です。

{previous}

ユーザーは「{normalized_message}」を選びました。

選ばれた料理の詳しい作り方を、
料理初心者向けに分かりやすく説明してください。

条件：
・材料は具体的な量を書く
・LINE向けに短く
・Markdown記法は禁止
・太字記号は禁止
・見出し記号は禁止
・在庫にない食材を勝手に追加しない
・追加食材が必要な場合は「買い足し」と明記する
・最後に「スクショしておくと便利だよ😊」を添える
"""
                    ai_text = generate_reply(prompt)
                else:
                    ai_text = "前の提案が見つかりませんでした💦\nもう一回、食材を教えてください😊"

            else:
                ai_text = generate_reply(user_message)

                # 提案内容を保存
                last_suggestions[user_id] = ai_text

            reply_to_line(reply_token, ai_text)

    return "OK"

def generate_reply(user_message):
    try:
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": user_message
                }
            ],
        )

        return response.output_text

    except Exception as e:
        print("OpenAI error:", e)
        return "ごめんなさい💦\n少し調子が悪いみたいです。\nもう一回送ってみてください😊"

def clean_line_text(text):
    return (
        text
        .replace("**", "")
        .replace("###", "")
        .replace("##", "")
        .replace("#", "")
    )

def reply_to_line(reply_token, text):
    clean_text = clean_line_text(text)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }

    data = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": clean_text[:4900]
            }
        ]
    }

    requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers=headers,
        json=data
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
