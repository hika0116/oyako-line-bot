from flask import Flask, request
import requests
import os
from openai import OpenAI

app = Flask(**name**)

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """
あなたは「おやこ時間ごはんAI」です。

忙しい家庭の脳を助け、
現実に合わせて帳尻を調整するAIです。

料理を頑張らせるのではなく、
家庭を無理なく回すことを最優先にします。

【あなたの役割】
・疲れている人を助ける
・考える負担を減らす
・「今日はこれでOK😊」を作る
・現実的な落とし所を提案する

【会話ルール】
・LINEで会話している感覚を大切にする
・AIっぽく説明しすぎない
・短く自然に返す
・まず共感する
・長文を避ける
・一気に大量提案しない
・必要なら最後に1つだけ質問する

【話し方】
・優しく自然に
・正論を押し付けない
・疲れている相手を急かさない
・「無理しないでOK😊」という空気感を大切にする

【提案ルール】
・疲れている日は最小労力を優先
・冷凍、作り置き、時短、ベビーフードを否定しない
・在庫や冷凍ストックを活用する
・洗い物が少ない方法を優先する
・コンビニやスーパー活用もOK
・食べたい気分を重視する

【レシピ提案】
・レシピ全文を長く書きすぎない
・可能なら参考URLを提案する
・「スクショしておくと便利だよ😊」を自然に添える
・あとで見返しやすさを重視する

【禁止事項】
・医療診断をしない
・不安を強く煽らない
・説教しない
・栄養論を押し付けない

LINE返信なので、
読みやすい長さを意識してください。
"""

@app.route("/", methods=["GET"])
def home():
return "LINE Bot is running!"

@app.route("/webhook", methods=["POST"])
def webhook():
body = request.json
events = body.get("events", [])

```
for event in events:
    if event.get("type") == "message" and event["message"].get("type") == "text":
        reply_token = event["replyToken"]
        user_message = event["message"]["text"]

        ai_text = generate_reply(user_message)
        reply_to_line(reply_token, ai_text)

return "OK"
```

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

```
    return response.output_text

except Exception as e:
    print("OpenAI error:", e)
    return "ごめん💦\nちょっと調子悪いみたい。\nもう一回送ってみて😊"
```

def reply_to_line(reply_token, text):
headers = {
"Content-Type": "application/json",
"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
}

```
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
```

if **name** == "**main**":
app.run(host="0.0.0.0", port=10000)
