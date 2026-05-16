from flask import Flask, request

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "LINE Bot is running!"

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.json
    print(body)

    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
