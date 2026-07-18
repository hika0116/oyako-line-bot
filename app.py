from flask import Flask, request
import base64
import hashlib
import hmac
import logging
import requests
import os
import re
import time
import unicodedata
from openai import OpenAI
from supabase import create_client, Client

from family_os import ContextBuilder, FamilyOSEngine, StructuredResponse, is_food_related


logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Existing in-process state is retained. Both mappings reset on restart and are
# not shared across multiple Gunicorn workers; see README.md for this limitation.
last_suggestions: dict[str, dict] = {}
setup_sessions: dict[str, dict] = {}

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("CHANNEL_SECRET")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
FAMILY_OS_PROMPT_PATH = os.environ.get("FAMILY_OS_PROMPT_PATH")
FAMILY_OS_DOMAIN_PROMPT_PATH = os.environ.get("FAMILY_OS_DOMAIN_PROMPT_PATH")
APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
MEAL_SUGGESTION_TTL_SECONDS = int(os.environ.get("MEAL_SUGGESTION_TTL_SECONDS", "1800"))

if APP_ENV in {"production", "prod"} and not LINE_CHANNEL_SECRET:
    raise RuntimeError("CHANNEL_SECRET is required when APP_ENV=production")
if not LINE_CHANNEL_SECRET:
    logger.warning(
        "CHANNEL_SECRET is not configured; LINE signature checks are bypassed only in development"
    )

def _initialize_openai_client():
    if not OPENAI_API_KEY:
        return None
    try:
        return OpenAI(api_key=OPENAI_API_KEY)
    except Exception as exc:
        logger.error("OpenAI client initialization failed error_type=%s", type(exc).__name__)
        return None


def _initialize_supabase_client() -> Client | None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as exc:
        logger.error("Supabase client initialization failed error_type=%s", type(exc).__name__)
        return None


client = _initialize_openai_client()
supabase = _initialize_supabase_client()
context_builder = ContextBuilder(book0_version="1.1", book7_version="1.0")
family_os_engine = (
    FamilyOSEngine(
        client=client,
        model=OPENAI_MODEL,
        prompt_path=FAMILY_OS_PROMPT_PATH,
        domain_prompt_path=FAMILY_OS_DOMAIN_PROMPT_PATH,
    )
    if client
    else None
)

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

_NUMBERED_CANDIDATE_LINE = re.compile(r"^\s*([1-3])[.．、:：)]\s*(.+?)\s*$", re.MULTILINE)


def verify_line_signature(raw_body: bytes, signature: str | None) -> bool:
    """Verify LINE's HMAC-SHA256 signature before processing an event."""

    if not LINE_CHANNEL_SECRET:
        return APP_ENV not in {"production", "prod"}
    if not signature:
        return False
    digest = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, signature)


def _meal_candidates_from_response(
    result: StructuredResponse,
    rendered_text: str,
) -> dict[str, str]:
    """Accept only structured, sequential choices that were actually shown."""

    if result.response_mode not in {"PROPOSE", "ACT"}:
        return {}
    actions = [item for item in result.suggested_actions if item.action.strip()]
    labels = [item.label.strip().rstrip(".．、:：)") for item in actions]
    if not actions or labels != [str(index) for index in range(1, len(actions) + 1)]:
        return {}

    displayed = {
        number: text.strip()
        for number, text in _NUMBERED_CANDIDATE_LINE.findall(rendered_text)
    }
    expected = {label: action.action.strip() for label, action in zip(labels, actions)}
    if any(displayed.get(number) != action for number, action in expected.items()):
        return {}
    return expected


def _set_meal_suggestions(
    user_id: str,
    result: StructuredResponse,
    rendered_text: str,
    *,
    food_related: bool,
) -> None:
    last_suggestions.pop(user_id, None)
    if not food_related:
        return
    candidates = _meal_candidates_from_response(result, rendered_text)
    if not candidates:
        return
    last_suggestions[user_id] = {
        "rendered_text": rendered_text,
        "candidates": candidates,
        "expires_at": time.time() + MEAL_SUGGESTION_TTL_SECONDS,
    }


def _get_valid_meal_suggestions(user_id: str) -> dict | None:
    state = last_suggestions.get(user_id)
    if not isinstance(state, dict) or state.get("expires_at", 0) <= time.time():
        last_suggestions.pop(user_id, None)
        return None
    candidates = state.get("candidates")
    if not isinstance(candidates, dict) or not candidates:
        last_suggestions.pop(user_id, None)
        return None
    return state


def handle_unmatched_numeric_selection(number: str) -> str:
    return f"「{number}」は何を選んだ番号ですか？\n料理候補なら、先に献立を相談してください。"

@app.route("/", methods=["GET"])
def home():
    return "LINE Bot is running!"

@app.route("/webhook", methods=["POST"])
def webhook():
    raw_body = request.get_data(cache=True)
    signature = request.headers.get("X-Line-Signature")
    if not verify_line_signature(raw_body, signature):
        logger.warning("Rejected LINE webhook with invalid signature")
        return "Invalid signature", 400

    body = request.get_json(silent=True) or {}
    events = body.get("events", [])

    for event in events:
        if event.get("type") == "message" and event["message"].get("type") == "text":
            reply_token = event["replyToken"]
            user_message = event["message"]["text"].strip()
            user_id = event["source"]["userId"]

            ensure_profile(user_id)

            normalized_message = unicodedata.normalize("NFKC", user_message).strip()

            if normalized_message == "初期設定":
                last_suggestions.pop(user_id, None)
                ai_text = start_setup(user_id)

            elif user_id in setup_sessions:
                ai_text = handle_setup_answer(user_id, normalized_message)

            elif normalized_message.startswith("在庫登録"):
                last_suggestions.pop(user_id, None)
                ai_text = handle_stock_register(user_id, user_message)

            elif normalized_message.startswith("買い物した"):
                last_suggestions.pop(user_id, None)
                ai_text = handle_stock_add(user_id, user_message)

            elif normalized_message.startswith("使った"):
                last_suggestions.pop(user_id, None)
                ai_text = handle_stock_use(user_id, user_message)

            elif normalized_message in ["在庫", "在庫確認"]:
                last_suggestions.pop(user_id, None)
                ai_text = handle_stock_list(user_id)

            elif normalized_message in ["1", "2", "3"]:
                state = _get_valid_meal_suggestions(user_id)
                if state and normalized_message in state["candidates"]:
                    ai_text = handle_recipe_selection(user_id, normalized_message, user_message)
                else:
                    ai_text = handle_unmatched_numeric_selection(normalized_message)

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
    state = _get_valid_meal_suggestions(user_id)
    if not state or normalized_message not in state["candidates"]:
        return handle_unmatched_numeric_selection(normalized_message)

    # A meal choice is single-use. This prevents a later bare number from
    # selecting an old menu after the conversation has moved on.
    last_suggestions.pop(user_id, None)
    profile = get_profile(user_id)
    recent_logs = get_recent_logs(user_id)
    stocks = get_stocks(user_id)
    selected_dish = state["candidates"][normalized_message]
    context = context_builder.build(
        f"前回の料理候補から{normalized_message}番を選びました。詳しい作り方を教えて。",
        channel="line",
        profile=profile,
        food_stock=stocks,
        recent_logs=recent_logs,
    )
    instructions = (
        f"選ばれた料理は「{selected_dish}」です。"
        "料理初心者向けに、材料の具体的な量と短い手順を説明してください。"
        "在庫にない材料は買い足しと明記し、存在しないURLを作らないでください。"
        "これはレシピ詳細であり、新しい番号選択候補は提示しないでください。"
    )
    result = generate_structured_reply(
        context=context,
        additional_instructions=instructions,
    )
    ai_text = result.user_message()
    save_meal_log(user_id, original_message, ai_text, selected_menu=selected_dish)
    return ai_text

def handle_normal_message(user_id, user_message):
    profile = get_profile(user_id)
    stocks = get_stocks(user_id)
    food_related = is_food_related(user_message, stocks)
    recent_logs = get_recent_logs(user_id) if food_related else []
    context = context_builder.build(
        user_message,
        channel="line",
        profile=profile,
        food_stock=stocks,
        recent_logs=recent_logs,
    )
    result = generate_structured_reply(context=context)
    ai_text = result.user_message()

    _set_meal_suggestions(
        user_id,
        result,
        ai_text,
        food_related=food_related,
    )
    if food_related:
        save_meal_log(user_id, user_message, ai_text)
    return ai_text

def ensure_profile(user_id):
    if supabase is None:
        logger.error("Supabase is not configured")
        return
    try:
        result = supabase.table("profiles").select("*").eq("user_id", user_id).execute()

        if not result.data:
            supabase.table("profiles").insert({
                "user_id": user_id,
                "notes": "初回登録。詳細プロフィールは未設定。"
            }).execute()

    except Exception as exc:
        logger.error("ensure_profile failed error_type=%s", type(exc).__name__)

def save_profile(user_id, data):
    if supabase is None:
        logger.error("Supabase is not configured")
        return
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

    except Exception as exc:
        logger.error("save_profile failed error_type=%s", type(exc).__name__)

def save_stock_item(user_id, item_name, quantity="", unit=""):
    if supabase is None:
        logger.error("Supabase is not configured")
        return
    try:
        supabase.table("stocks").insert({
            "user_id": user_id,
            "item_name": item_name,
            "quantity": quantity,
            "unit": unit
        }).execute()

    except Exception as exc:
        logger.error("save_stock_item failed error_type=%s", type(exc).__name__)

def add_stock_quantity(user_id, item_name, quantity, unit=""):
    if supabase is None:
        logger.error("Supabase is not configured")
        return
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

    except Exception as exc:
        logger.error("add_stock_quantity failed error_type=%s", type(exc).__name__)


def subtract_stock_quantity(user_id, item_name, quantity):
    if supabase is None:
        logger.error("Supabase is not configured")
        return
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

    except Exception as exc:
        logger.error("subtract_stock_quantity failed error_type=%s", type(exc).__name__)

def get_stocks(user_id):
    if supabase is None:
        return []
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

                except (TypeError, ValueError):
                    quantity_display = quantity

                stocks.append(
                    f"{item_name} {quantity_display}{unit}"
                )

            else:
                stocks.append(item_name)

        return stocks

    except Exception as exc:
        logger.error("get_stocks failed error_type=%s", type(exc).__name__)
        return [] 
        
def get_profile(user_id):
    if supabase is None:
        return {}
    try:
        result = supabase.table("profiles").select("*").eq("user_id", user_id).execute()
        if result.data:
            return result.data[0]
        return {}
    except Exception as exc:
        logger.error("get_profile failed error_type=%s", type(exc).__name__)
        return {}

def get_recent_logs(user_id):
    if supabase is None:
        return []
    try:
        result = (
            supabase.table("meal_logs")
            .select("message,suggestions,selected_menu,created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )

        return result.data or []

    except Exception as exc:
        logger.error("get_recent_logs failed error_type=%s", type(exc).__name__)
        return []

def save_meal_log(user_id, message, suggestions, selected_menu=None):
    if supabase is None:
        logger.error("Supabase is not configured")
        return
    try:
        supabase.table("meal_logs").insert({
            "user_id": user_id,
            "message": message,
            "suggestions": suggestions,
            "selected_menu": selected_menu
        }).execute()

    except Exception as exc:
        logger.error("save_meal_log failed error_type=%s", type(exc).__name__)

def generate_structured_reply(
    user_message=None,
    *,
    context=None,
    additional_instructions=None,
) -> StructuredResponse:
    if context is None:
        context = context_builder.build(str(user_message or ""), channel="line")
    if family_os_engine is None:
        logger.error("OPENAI_API_KEY is not configured")
        return StructuredResponse(
            response_mode="PROPOSE",
            safety_level="none",
            message="ごめんなさい。少し調子が悪いようです。もう一度送ってください。",
            reasoning_tags=["generation_unavailable"],
            prompt_version="1.0",
        )
    # memory_candidates are review-only. There is deliberately no save call.
    return family_os_engine.respond(
        context,
        additional_instructions=additional_instructions,
    )


def generate_reply(user_message=None, *, context=None, additional_instructions=None):
    """Compatibility wrapper for existing callers that expect plain LINE text."""

    result = generate_structured_reply(
        user_message,
        context=context,
        additional_instructions=additional_instructions,
    )
    return result.user_message()

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

    try:
        response = requests.post(
            "https://api.line.me/v2/bot/message/reply",
            headers=headers,
            json=data,
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("LINE reply failed error_type=%s", type(exc).__name__)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
