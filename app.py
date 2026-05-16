from flask import Flask, request
import requests
import os
from openai import OpenAI

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """
あなたは「おやこ時間ごはんAI」です。
忙しい家庭の脳を助け、現実に合わせて帳尻を調整するAIです。

料理を頑張らせるのではなく、家庭を無理なく回すことを最優先にします。

基本方針：
・正論を押し付けない
・完璧主義を避ける
・短く実用的に答える
・疲れている日は最小労力を優先する
・冷凍、作り置き、時短、ベビーフードを否定しない
・食べたい気分を重視する
・在庫や冷凍ストックを活用する
・医療診断はしない
・不安が強い症状は受診を促す

LINE返信なので、長文にしすぎないでください。
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
            user_message = event["message"]["text"]

            ai_text = generate_reply(user_message)
            reply_to_line(reply_token, ai_text)

    return "OK"

def generate_reply(user_message):
    try:
        response = client.responses.create(
            model="gpt-5.4-mini",
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
        return "ごめん、今ちょっと考えられなかったみたいです💦\nもう一回送ってみてください。"

def reply_to_line(reply_token, text):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }

    data = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": text[:4900]
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
