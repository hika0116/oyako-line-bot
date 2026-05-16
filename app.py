from flask import Flask, request
import requests
import os

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN")

@app.route("/", methods=["GET"])
def home():
    return "LINE Bot is running!"

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.json

    events = body.get("events", [])

    for event in events:
        if event["type"] == "message":
            reply_token = event["replyToken"]
            user_message = event["message"]["text"]

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"
            }

            data = {
                "replyToken": reply_token,
                "messages": [
                    {
                        "type": "text",
                        "text": f"受け取りました😊\n『{user_message}』"
                    }
                ]
            }

            requests.post(
                "https://api.line.me/v2/bot/message/reply",
                headers=headers,
                json=data
            )

    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
