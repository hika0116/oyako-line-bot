from flask import Flask, request
import requests
import os
import unicodedata
from openai import OpenAI
from supabase import create_client, Client

app = Flask(__name__)

last_suggestions = {}
setup_sessions = {}

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

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
・丁寧すぎず、自然で落ち着いた口調にする
・絵文字は使いすぎない
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

履歴活用ルール：
・最近の提案履歴を参考にする
・直近で提案したメニューと同じものをなるべく避ける
・同じ食材でも、調理方法・味付け・食べ方を変える
・ただし、ユーザーが「昨日みたいに」「同じのでいい」と言った場合は同系統でもよい
・疲れている日は重複回避より簡単さを優先してよい

在庫優先ルール：
・ユーザーが出した食材や保存済み在庫を最優先に使う
・在庫にない食材を主役にした提案をしない
・不明な食材を勝手にある前提にしない
・在庫だけで作れる案を最低1つ出す
・追加食材が必要な場合は「買い足し」と明記する

初心者向け表現ルール：
・「少し」「適量」「お好みで」だけで終わらせない
・量はできるだけ具体的に書く
・例：しょうゆ小さじ1、水300ml、豆腐150g、卵1個
・難しい調理用語は避ける

レシピURLルール：
・詳しい作り方を出すときは、参考レシピの探し方も添える
・存在しないURLを作らない
・URLが確実でない場合は、具体的な検索ワードを出す
・検索ワードは料理名＋主材料＋調理方法にする
・例：「豆腐 卵 レンジ レシピ」
・「スクショしておくと次回楽だよ😊」を自然に添える

禁止事項：
・医療診断をしない
・不安を強く煽らない
・説教しない
・栄養論を押し付けない
"""

TOOL_LIST = {
    "1": "電子レンジ",
    "2": "炊飯器",
    "3": "フライパン",
    "4": "鍋",
    "5": "オーブントースター",
    "6": "ブレンダー",
    "7": "ホットクック",
    "8": "食洗機",
    "9": "すべて持っている"
}

COOKING_LEVELS = {
    "1": "ほぼ初心者",
    "2": "簡単な家庭料理ならできる",
    "3": "作り置きや下味冷凍もできる",
    "4": "料理はかなり得意"
}

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

            ensure_profile(user_id)

            normalized_message = unicodedata.normalize("NFKC", user_message).strip()

            if normalized_message == "初期設定":
                ai_text = start_setup(user_id)

            elif user_id in setup_sessions:
                ai_text = handle_setup_answer(user_id, normalized_message)

            elif normalized_message.startswith("在庫登録"):
                ai_text = handle_stock_register(user_id, user_message)

            elif normalized_message.startswith("買い物した"):
                ai_text = handle_stock_add(user_id, user_message)

            elif normalized_message.startswith("使った"):
                ai_text = handle_stock_use(user_id, user_message)

            elif normalized_message in ["在庫", "在庫確認"]:
                ai_text = handle_stock_list(user_id)

            elif normalized_message in ["1", "2", "3"]:
                ai_text = handle_recipe_selection(user_id, normalized_message, user_message)

            else:
                ai_text = handle_normal_message(user_id, user_message)

            reply_to_line(reply_token, ai_text)

    return "OK"

def start_setup(user_id):
    setup_sessions[user_id] = {
        "step": "family_size",
        "data": {}
    }

    return (
        "あなたの家庭に合った提案をするために、"
        "いくつかだけ教えてください😊\n\n"
        "まず、一緒に住んでいる大人は何人ですか？\n"
        "例：2人"
    )

def handle_setup_answer(user_id, message):
    session = setup_sessions[user_id]
    step = session["step"]
    data = session["data"]

    if step == "family_size":
        data["family_size"] = message
        session["step"] = "children_info"

        return (
            "ありがとうございます😊\n\n"
            "次に、お子さんはいますか？\n"
            "いる場合は年齢や月齢も教えてください。\n\n"
            "例：生後2ヶ月の子どもが1人\n"
            "例：子どもはいない"
        )

    if step == "children_info":
        data["children_info"] = message
        session["step"] = "cooking_level"

        return (
            "料理の難しさを合わせたいので、料理レベルを教えてください😊\n\n"
            "半角数字で返してください。\n\n"
            "1. ほぼ初心者\n"
            "2. 簡単な家庭料理ならできる\n"
            "3. 作り置きや下味冷凍もできる\n"
            "4. 料理はかなり得意"
        )

    if step == "cooking_level":
        if message not in COOKING_LEVELS:
            return (
                "半角数字で教えてください😊\n\n"
                "1. ほぼ初心者\n"
                "2. 簡単な家庭料理ならできる\n"
                "3. 作り置きや下味冷凍もできる\n"
                "4. 料理はかなり得意"
            )

        data["cooking_level"] = COOKING_LEVELS[message]
        session["step"] = "tools"

        return (
            "使える調理器具に合わせて提案したいので、"
            "持っていないものを番号で教えてください😊\n\n"
            "半角数字で、複数ある場合は「1,3,5」のように返してください。\n\n"
            "1. 電子レンジ\n"
            "2. 炊飯器\n"
            "3. フライパン\n"
            "4. 鍋\n"
            "5. オーブントースター\n"
            "6. ブレンダー\n"
            "7. ホットクック\n"
            "8. 食洗機\n"
            "9. すべて持っている"
        )

    if step == "tools":
        selected = [x.strip() for x in message.replace("、", ",").split(",")]

        invalid = [x for x in selected if x not in TOOL_LIST]
        if invalid:
            return (
                "番号で教えてください😊\n\n"
                "複数ある場合は「1,3,5」のように返せます。\n\n"
                "1. 電子レンジ\n"
                "2. 炊飯器\n"
                "3. フライパン\n"
                "4. 鍋\n"
                "5. オーブントースター\n"
                "6. ブレンダー\n"
                "7. ホットクック\n"
                "8. 食洗機\n"
                "9. すべて持っている"
            )

        if "9" in selected:
            data["tools"] = "基本的な調理器具はすべて持っている"
        else:
            missing_tools = [TOOL_LIST[x] for x in selected]
            data["tools"] = "持っていないもの：" + "、".join(missing_tools)

        session["step"] = "shopping_frequency"

        return (
            "買い物リストを作りやすくするために、"
            "買い物頻度を教えてください😊\n\n"
            "例：週1回まとめ買い\n"
            "例：週2〜3回\n"
            "例：ほぼ毎日"
        )

    if step == "shopping_frequency":
        data["shopping_frequency"] = message
        session["step"] = "frozen_style"

        return (
            "平日を楽にする提案にしたいので、"
            "冷凍ストックは活用したいですか？😊\n\n"
            "例：かなり使いたい\n"
            "例：少しなら使いたい\n"
            "例：あまり使わない"
        )

    if step == "frozen_style":
        data["frozen_style"] = message
        session["step"] = "allergies_dislikes"

        return (
            "安全面と好みに合わせるために、"
            "アレルギーや苦手食材があれば教えてください😊\n\n"
            "なければ「なし」でOKです。"
        )

    if step == "allergies_dislikes":
        data["allergies"] = message
        data["dislikes"] = message

        save_profile(user_id, data)
        setup_sessions.pop(user_id, None)

        return (
            "初期設定できました😊\n\n"
            "これからは、この家庭情報を前提に提案します。\n"
            "まずは気軽に、\n"
            "「今日のごはんどうしよう」\n"
            "みたいに送ってください。"
        )

    setup_sessions.pop(user_id, None)
    return "設定が途中で分からなくなりました💦\nもう一度「初期設定」と送ってください。"

def handle_stock_register(user_id, message):
    parsed_items = parse_stock_lines(message)

    if not parsed_items:
        return (
            "在庫登録する食材を改行で送ってください😊\n\n"
            "例：\n"
            "在庫登録\n"
            "卵 10 個\n"
            "豆腐 2 丁\n"
            "冷凍うどん 3 玉"
        )

    saved_items = []

    for item in parsed_items:
        save_stock_item(
            user_id,
            item["item_name"],
            item["quantity"],
            item["unit"]
        )

        saved_items.append(
            f'{item["item_name"]} {item["quantity"]}{item["unit"]}'
        )

    return (
        "在庫を登録しました😊\n\n"
        + "\n".join([f"・{item}" for item in saved_items])
        + "\n\n"
        "次から「今日どうしよう」だけでも、在庫を見ながら提案できます。"
    )
    
def parse_stock_lines(message):
    lines = message.splitlines()
    parsed_items = []

    for line in lines[1:]:
        line = line.strip()

        if not line:
            continue

        parts = line.split()

        if len(parts) >= 3:
            item_name = parts[0]
            quantity = parts[1]
            unit = parts[2]
        elif len(parts) == 2:
            item_name = parts[0]
            quantity = parts[1]
            unit = ""
        else:
            item_name = line
            quantity = ""
            unit = ""

        parsed_items.append({
            "item_name": item_name,
            "quantity": quantity,
            "unit": unit
        })

    return parsed_items
    
def handle_stock_list(user_id):
    stocks = get_stocks(user_id)

    if not stocks:
        return (
            "まだ在庫が登録されていません😊\n\n"
            "こんな感じで送ると登録できます。\n\n"
            "在庫登録\n"
            "卵 10個\n"
            "豆腐 2丁\n"
            "冷凍うどん 3玉"
        )

    stock_text = "\n".join([f"・{item}" for item in stocks])

    return (
        "今の登録在庫です😊\n\n"
        f"{stock_text}\n\n"
        "この在庫をもとに提案できます。"
    )

def handle_stock_add(user_id, message):
    parsed_items = parse_stock_lines(message)

    if not parsed_items:
        return (
            "買い物したものを改行で送ってください😊\n\n"
            "例：\n"
            "買い物した\n"
            "卵 10 個\n"
            "豆腐 2 丁"
        )

    added_items = []

    for item in parsed_items:
        add_stock_quantity(
            user_id,
            item["item_name"],
            item["quantity"],
            item["unit"]
        )

        added_items.append(
            f'{item["item_name"]} {item["quantity"]}{item["unit"]}'
        )

    return (
        "買い物したものを在庫に追加しました😊\n\n"
        + "\n".join([f"・{item}" for item in added_items])
    )


def handle_stock_use(user_id, message):
    parsed_items = parse_stock_lines(message)

    if not parsed_items:
        return (
            "使った食材を改行で送ってください😊\n\n"
            "例：\n"
            "使った\n"
            "卵 2 個\n"
            "豆腐 1 丁"
        )

    used_items = []

    for item in parsed_items:
        subtract_stock_quantity(
            user_id,
            item["item_name"],
            item["quantity"]
        )

        used_items.append(
            f'{item["item_name"]} {item["quantity"]}{item["unit"]}'
        )

    return (
        "使った分を在庫から減らしました😊\n\n"
        + "\n".join([f"・{item}" for item in used_items])
    )
    
def handle_recipe_selection(user_id, normalized_message, original_message):
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
        save_meal_log(user_id, original_message, ai_text, selected_menu=normalized_message)
        return ai_text

    return "前の提案が見つかりませんでした💦\nもう一回、食材を教えてください😊"

def handle_normal_message(user_id, user_message):
    profile = get_profile(user_id)
    recent_logs = get_recent_logs(user_id)
    stocks = get_stocks(user_id)

    context = f"""
ユーザー情報：
{profile}

登録在庫：
{stocks}

最近の提案履歴：
{recent_logs}

重要：
登録在庫がある場合は、その在庫を優先してください。
最近提案した料理と同じものはできるだけ避けてください。
同じ食材を使う場合でも、調理方法・味付け・食べ方を変えてください。
ただし、ユーザーが「昨日みたいに」「同じのでいい」と言った場合は、同系統でも大丈夫です。
疲れている相談の場合は、重複回避より簡単さを優先してください。

ユーザーの今回の相談：
{user_message}
"""
    ai_text = generate_reply(context)

    last_suggestions[user_id] = ai_text
    save_meal_log(user_id, user_message, ai_text)

    return ai_text

def ensure_profile(user_id):
    try:
        result = supabase.table("profiles").select("*").eq("user_id", user_id).execute()

        if not result.data:
            supabase.table("profiles").insert({
                "user_id": user_id,
                "notes": "初回登録。詳細プロフィールは未設定。"
            }).execute()

    except Exception as e:
        print("ensure_profile error:", e)

def save_profile(user_id, data):
    try:
        supabase.table("profiles").upsert({
            "user_id": user_id,
            "family_size": data.get("family_size"),
            "children_info": data.get("children_info"),
            "cooking_level": data.get("cooking_level"),
            "tools": data.get("tools"),
            "shopping_frequency": data.get("shopping_frequency"),
            "frozen_style": data.get("frozen_style"),
            "allergies": data.get("allergies"),
            "dislikes": data.get("dislikes"),
            "notes": "初期設定済み"
        }).execute()

    except Exception as e:
        print("save_profile error:", e)

def save_stock_item(user_id, item_name, quantity="", unit=""):
    try:
        supabase.table("stocks").insert({
            "user_id": user_id,
            "item_name": item_name,
            "quantity": quantity,
            "unit": unit
        }).execute()

    except Exception as e:
        print("save_stock_item error:", e)

def add_stock_quantity(user_id, item_name, quantity, unit=""):
    try:
        result = (
            supabase.table("stocks")
            .select("*")
            .eq("user_id", user_id)
            .eq("item_name", item_name)
            .limit(1)
            .execute()
        )

        add_qty = float(quantity)

        if result.data:
            stock = result.data[0]
            current_qty = float(stock.get("quantity") or 0)
            new_qty = current_qty + add_qty

            supabase.table("stocks").update({
                "quantity": str(new_qty),
                "unit": unit or stock.get("unit")
            }).eq("id", stock["id"]).execute()

        else:
            save_stock_item(user_id, item_name, quantity, unit)

    except Exception as e:
        print("add_stock_quantity error:", e)


def subtract_stock_quantity(user_id, item_name, quantity):
    try:
        result = (
            supabase.table("stocks")
            .select("*")
            .eq("user_id", user_id)
            .eq("item_name", item_name)
            .limit(1)
            .execute()
        )

        used_qty = float(quantity)

        if result.data:
            stock = result.data[0]
            current_qty = float(stock.get("quantity") or 0)
            new_qty = max(current_qty - used_qty, 0)

            supabase.table("stocks").update({
                "quantity": str(new_qty)
            }).eq("id", stock["id"]).execute()

    except Exception as e:
        print("subtract_stock_quantity error:", e)

def get_stocks(user_id):
    try:
        result = (
            supabase.table("stocks")
            .select("item_name,quantity,unit")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )

        if not result.data:
            return []

        stocks = []

        for row in result.data:
            item_name = row.get("item_name") or ""
            quantity = row.get("quantity") or ""
            unit = row.get("unit") or ""

            if quantity:
                try:
                    q = float(quantity)

                    if q.is_integer():
                        quantity_display = str(int(q))
                    else:
                        quantity_display = str(q)

                except:
                    quantity_display = quantity

                stocks.append(
                    f"{item_name} {quantity_display}{unit}"
                )

            else:
                stocks.append(item_name)

        return stocks

    except Exception as e:
        print("get_stocks error:", e)
        return [] 
        
def get_profile(user_id):
    try:
        result = supabase.table("profiles").select("*").eq("user_id", user_id).execute()
        if result.data:
            return result.data[0]
        return "プロフィール未設定"
    except Exception as e:
        print("get_profile error:", e)
        return "プロフィール取得エラー"

def get_recent_logs(user_id):
    try:
        result = (
            supabase.table("meal_logs")
            .select("message,suggestions,selected_menu,created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )

        if not result.data:
            return "まだ提案履歴はありません。"

        return result.data

    except Exception as e:
        print("get_recent_logs error:", e)
        return "履歴取得エラー"

def save_meal_log(user_id, message, suggestions, selected_menu=None):
    try:
        supabase.table("meal_logs").insert({
            "user_id": user_id,
            "message": message,
            "suggestions": suggestions,
            "selected_menu": selected_menu
        }).execute()

    except Exception as e:
        print("save_meal_log error:", e)

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
