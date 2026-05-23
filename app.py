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

【LINE表示ルール】
・Markdown記法を使わない
・**太字**を使わない
・見出し記号を多用しない
・LINEで自然に読める文章にする
・1返信はできるだけ短くする
・提案は最大3つまで

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

【レシピURLルール】
・具体的なレシピを提案するときは、可能なら参考URLも添える
・存在しないURLを作らない
・URLが不確かな場合は無理に貼らない
・URLを貼れない場合は検索ワードを提案する
・例：「豆腐 卵 レンジ レシピ」で検索すると見つけやすいよ
・「スクショしておくと次回楽だよ😊」を自然に添える

【初心者向け表現ルール】
・「少し」「適量」「お好みで」だけで終わらせない
・料理初心者でも分かるように、量はできるだけ具体的に書く
・例：しょうゆ小さじ1、水300ml、豆腐150g、卵1個
・難しい調理用語は避ける

【番号選択ルール】
・最初の提案では、料理候補を最大3つまで番号付きで出す
・詳しい作り方は最初から全部書かない
・「番号で返してくれたら作り方を書くよ」と案内する
・ユーザーが番号で返信したら、その番号のレシピを詳しく出す

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
