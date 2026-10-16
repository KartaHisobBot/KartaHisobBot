import os
import json
import hmac
import hashlib
import sqlite3
import asyncio
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlparse, parse_qs
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
    Update,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.environ.get("BOT_TOKEN")
DB_NAME = "karta_hisob.db"
MAX_PLAYERS = 6
OUT_SCORE = 24
BASE_DIR = Path(__file__).resolve().parent
WEBAPP_URL = "https://kartahisobbot-g32w.onrender.com/"


# ---------------- MA'LUMOTLAR BAZASI ----------------

def setup():
    with sqlite3.connect(DB_NAME) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                started TEXT NOT NULL,
                finished TEXT,
                champion TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS players (
                game_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                score INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (game_id, user_id)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                player_name TEXT NOT NULL,
                points INTEGER NOT NULL,
                added_by TEXT NOT NULL,
                created TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS current_games (
                chat_id INTEGER PRIMARY KEY,
                game_id INTEGER NOT NULL
            )
        """)


def current_game(chat_id):
    with sqlite3.connect(DB_NAME) as con:
        row = con.execute(
            "SELECT game_id FROM current_games WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
    return row[0] if row else None


def get_players(game_id):
    with sqlite3.connect(DB_NAME) as con:
        return con.execute("""
            SELECT user_id, name, score, active
            FROM players
            WHERE game_id = ?
            ORDER BY rowid
        """, (game_id,)).fetchall()


def score_text(game_id):
    players = get_players(game_id)
    lines = [f"🃏 KARTA HISOBI — O‘yin #{game_id}", ""]

    active = [p for p in players if p[3] == 1]

    for _, name, score, _ in players:
        if score >= OUT_SCORE:
            lines.append(f"❌ {name}: {score} ball — SABIN")
        else:
            lines.append(f"🟢 {name}: {score}/{OUT_SCORE} ball")

    lines.extend(["", f"O‘yinda qolganlar: {len(active)}"])

    if len(players) >= 2 and len(active) == 1:
        lines.append(f"\n🏆 CHEMPION: {active[0][1]}!")

    return "\n".join(lines)


def game_keyboard(game_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "✍️ Raund ballarini kiritish",
            callback_data=f"round:{game_id}",
        )],
        [InlineKeyboardButton(
            "🔄 Hisobni yangilash",
            callback_data=f"refresh:{game_id}",
        )],
        [InlineKeyboardButton(
            "📚 O‘yinlar tarixi",
            callback_data="history",
        )],
        [InlineKeyboardButton(
            "📱 Mini App ochish",
            web_app=WebAppInfo(
                url=f"{WEBAPP_URL}?chat_id={game_chat_id(game_id)}"
            ),
        )],
    ])


def game_chat_id(game_id):
    with sqlite3.connect(DB_NAME) as con:
        row = con.execute(
            "SELECT chat_id FROM games WHERE id = ?", (game_id,)
        ).fetchone()
    return row[0] if row else 0


async def show_score(chat_id, game_id, context):
    await context.bot.send_message(
        chat_id=chat_id,
        text=score_text(game_id),
        reply_markup=game_keyboard(game_id),
    )


# ---------------- TELEGRAM MINI APP XAVFSIZLIGI ----------------

def validate_init_data(init_data):
    """Telegram yuborgan initData imzosini tekshiradi."""
    if not TOKEN or not init_data:
        return None

    try:
        values = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = values.pop("hash", None)

        if not received_hash:
            return None

        auth_date = int(values.get("auth_date", "0"))
        if auth_date <= 0 or abs(time.time() - auth_date) > 86400:
            return None

        data_check_string = "\n".join(
            f"{key}={value}" for key, value in sorted(values.items())
        )

        secret_key = hmac.new(
            b"WebAppData",
            TOKEN.encode("utf-8"),
            hashlib.sha256,
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        user_raw = values.get("user")
        if not user_raw:
            return None

        return json.loads(user_raw)

    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def telegram_api(method, payload):
    """Telegram Bot API ga so‘rov yuboradi."""
    request = Request(
        f"https://api.telegram.org/bot{TOKEN}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError("Telegram API so‘rovi bajarilmadi.")

    return result["result"]


def is_chat_member(chat_id, user_id):
    """Foydalanuvchining shu guruhga a'zoligini tekshiradi."""
    try:
        result = telegram_api("getChatMember", {
            "chat_id": chat_id,
            "user_id": user_id,
        })
        return result.get("status") in (
            "creator", "administrator", "member"
        ) or (
            result.get("status") == "restricted"
            and result.get("is_member", False)
        )
    except Exception:
        return False


# ---------------- WEB SERVER VA API ----------------

class HealthHandler(BaseHTTPRequestHandler):

    def send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, content):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def get_identity_and_chat(self):
        init_data = self.headers.get("X-Telegram-Init-Data", "")
        user = validate_init_data(init_data)

        if not user or not user.get("id"):
            return None, None, (401, "Telegram ma'lumotlari tasdiqlanmadi.")

        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        try:
            chat_id = int(query.get("chat_id", [""])[0])
        except (ValueError, TypeError):
            return None, None, (400, "chat_id noto‘g‘ri.")

        if not is_chat_member(chat_id, int(user["id"])):
            return None, None, (403, "Bu guruh a'zoligi tasdiqlanmadi.")

        return user, chat_id, None

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path in ("/", "/index.html"):
            html_file = BASE_DIR / "static" / "index.html"

            if not html_file.is_file():
                self.send_json(404, {
                    "error": "static/index.html fayli topilmadi."
                })
                return

            self.send_html(html_file.read_bytes())
            return

        if parsed.path == "/api/state":
            user, chat_id, error = self.get_identity_and_chat()
            if error:
                self.send_json(error[0], {"error": error[1]})
                return

            game_id = current_game(chat_id)
            if not game_id:
                self.send_json(200, {
                    "game_id": None,
                    "players": [],
                    "message": "Faol o‘yin yo‘q. Guruhda /newgame buyrug‘ini ishlating.",
                })
                return

            players = get_players(game_id)
            self.send_json(200, {
                "game_id": game_id,
                "out_score": OUT_SCORE,
                "players": [
                    {
                        "id": p[0],
                        "name": p[1],
                        "score": p[2],
                        "active": bool(p[3]),
                    }
                    for p in players
                ],
            })
            return

        self.send_json(404, {"error": "Sahifa topilmadi."})

    def do_POST(self):
        if urlparse(self.path).path != "/api/round":
            self.send_json(404, {"error": "API manzili topilmadi."})
            return

        user, chat_id, error = self.get_identity_and_chat()
        if error:
            self.send_json(error[0], {"error": error[1]})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 20000:
                self.send_json(400, {"error": "So‘rov hajmi noto‘g‘ri."})
                return

            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            scores = payload.get("scores")

            if not isinstance(scores, list) or not all(
                type(value) is int and 0 <= value <= 100000
                for value in scores
            ):
                self.send_json(400, {"error": "Ballar noto‘g‘ri kiritilgan."})
                return

            game_id = current_game(chat_id)
            if not game_id:
                self.send_json(409, {"error": "Faol o‘yin topilmadi."})
                return

            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            with sqlite3.connect(DB_NAME, timeout=10) as con:
                con.execute("BEGIN IMMEDIATE")

                current = con.execute(
                    "SELECT game_id FROM current_games WHERE chat_id = ?",
                    (chat_id,),
                ).fetchone()

                if not current or current[0] != game_id:
                    con.rollback()
                    self.send_json(409, {"error": "O‘yin o‘zgargan. Sahifani yangilang."})
                    return

                players = con.execute("""
                    SELECT user_id, name, score, active
                    FROM players
                    WHERE game_id = ?
                    ORDER BY rowid
                """, (game_id,)).fetchall()

                active_players = [p for p in players if p[3] == 1]

                if len(scores) != len(active_players):
                    con.rollback()
                    self.send_json(400, {
                        "error": f"{len(active_players)} ta faol o‘yinchi uchun shuncha ball kiriting."
                    })
                    return

                for player, points in zip(active_players, scores):
                    user_id, name, old_score, _ = player
                    new_score = old_score + points
                    active = 0 if new_score >= OUT_SCORE else 1

                    con.execute("""
                        UPDATE players
                        SET score = ?, active = ?
                        WHERE game_id = ? AND user_id = ?
                    """, (new_score, active, game_id, user_id))

                    con.execute("""
                        INSERT INTO history
                        (game_id, player_name, points, added_by, created)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        game_id,
                        name,
                        points,
                        str(user.get("first_name", "Telegram foydalanuvchisi")),
                        now,
                    ))

                updated = con.execute("""
                    SELECT name, score, active
                    FROM players
                    WHERE game_id = ?
                    ORDER BY rowid
                """, (game_id,)).fetchall()

                still_active = [p for p in updated if p[2] == 1]
                champion = None

                if len(updated) >= 2 and len(still_active) == 1:
                    champion = still_active[0][0]
                    con.execute("""
                        UPDATE games SET finished = ?, champion = ?
                        WHERE id = ?
                    """, (now, champion, game_id))

                con.commit()

            self.send_json(200, {
                "ok": True,
                "game_id": game_id,
                "champion": champion,
                "players": [
                    {
                        "name": p[0],
                        "score": p[1],
                        "active": bool(p[2]),
                    }
                    for p in updated
                ],
            })

        except (ValueError, TypeError, json.JSONDecodeError):
            self.send_json(400, {"error": "JSON so‘rovi noto‘g‘ri."})
        except Exception:
            self.send_json(500, {"error": "Server xatosi. Qayta urinib ko‘ring."})

    def log_message(self, format, *args):
        pass


def start_web_server():
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


# ---------------- TELEGRAM BUYRUQLARI ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "👋 KartaHisobBot!\n\n"
        "Buyruqlar:\n"
        "/newgame Ali, Vali, Sardor — yangi o‘yin\n"
        "/score — hisobni ko‘rish\n"
        "/history — o‘yinlar tarixi\n"
        "/app — Mini App ochish tugmasi\n\n"
        "Mini App orqali hisobni ko‘rish va raund ballarini kiritish mumkin."
    )


async def newgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.effective_message.text or ""
    names_text = text.partition(" ")[2].strip()

    if not names_text:
        await update.effective_message.reply_text(
            "O‘yinchilar ismini vergul bilan yozing:\n"
            "/newgame Ali, Vali, Sardor"
        )
        return

    names = [name.strip() for name in names_text.split(",") if name.strip()]

    if not 2 <= len(names) <= MAX_PLAYERS:
        await update.effective_message.reply_text(
            "O‘yinchilar soni 2 tadan 6 tagacha bo‘lishi kerak."
        )
        return

    if len({name.casefold() for name in names}) != len(names):
        await update.effective_message.reply_text(
            "Bir xil ism ikki marta yozilgan. Ismlarni tekshiring."
        )
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with sqlite3.connect(DB_NAME) as con:
        cur = con.execute(
            "INSERT INTO games (chat_id, started) VALUES (?, ?)",
            (chat_id, now),
        )
        game_id = cur.lastrowid

        con.execute("""
            INSERT OR REPLACE INTO current_games (chat_id, game_id)
            VALUES (?, ?)
        """, (chat_id, game_id))

        for index, name in enumerate(names, start=1):
            con.execute("""
                INSERT INTO players
                (game_id, user_id, name, score, active)
                VALUES (?, ?, ?, 0, 1)
            """, (game_id, -index, name))

    await update.effective_message.reply_text(
        "✅ Yangi o‘yin boshlandi!\n"
        "O‘yinchilar: " + ", ".join(names)
    )
    await show_score(chat_id, game_id, context)


async def score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game_id = current_game(chat_id)

    if not game_id:
        await update.effective_message.reply_text(
            "Faol o‘yin yo‘q. /newgame bilan boshlang."
        )
        return

    await show_score(chat_id, game_id, context)


async def app_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    if chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text(
            "Bro, avval botni o‘yin bo‘ladigan guruhga qo‘sh. "
            "Keyin guruhda /app buyrug‘ini yubor."
        )
        return

    game_id = current_game(chat.id)
    if not game_id:
        await update.effective_message.reply_text(
            "Avval /newgame Ali, Vali kabi buyruq bilan o‘yin boshlang."
        )
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "📱 Karta hisobini ochish",
            web_app=WebAppInfo(url=f"{WEBAPP_URL}?chat_id={chat.id}"),
        )
    ]])

    await update.effective_message.reply_text(
        "Karta hisobini to‘liq ekranli Mini App’da oching:",
        reply_markup=keyboard,
    )


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    with sqlite3.connect(DB_NAME) as con:
        rows = con.execute("""
            SELECT id, started, finished, champion
            FROM games
            WHERE chat_id = ?
            ORDER BY id DESC
            LIMIT 10
        """, (chat_id,)).fetchall()

    if not rows:
        await update.effective_message.reply_text(
            "Hozircha o‘yinlar tarixi yo‘q."
        )
        return

    lines = ["📚 O‘YINLAR TARIXI", ""]
    for game_id, started, finished, champion in rows:
        lines.append(f"🃏 O‘yin #{game_id} — {started}")
        if champion:
            lines.append(f"🏆 Chempion: {champion}")
        elif finished:
            lines.append("O‘yin yakunlangan.")
        else:
            lines.append("O‘yin yakunlanmagan.")
        lines.append("")

    await update.effective_message.reply_text("\n".join(lines))


async def receive_scores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending = context.user_data.get("pending_round")
    if not pending or update.effective_chat.id != pending["chat_id"]:
        return

    text = (update.effective_message.text or "").strip()

    try:
        scores = [int(value) for value in text.replace(",", " ").split()]
    except ValueError:
        await update.effective_message.reply_text(
            "Faqat raqamlarni yozing. Masalan: 4 6 2 0"
        )
        return

    active_players = [
        p for p in get_players(pending["game_id"]) if p[3] == 1
    ]

    if len(scores) != len(active_players) or any(x < 0 for x in scores):
        await update.effective_message.reply_text(
            f"{len(active_players)} ta faol o‘yinchi uchun manfiy bo‘lmagan "
            "ballarni kiriting. Masalan: 4 6 2"
        )
        return

    pending["scores"] = scores
    pending["players"] = active_players

    lines = ["📝 Raundni tekshiring:", ""]
    for player, points in zip(active_players, scores):
        lines.append(f"• {player[1]}: {points} ball (jami {player[2] + points})")

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ Raundni saqlash",
            callback_data=f"save:{pending['game_id']}",
        ),
        InlineKeyboardButton(
            "❌ Bekor qilish",
            callback_data=f"cancel:{pending['game_id']}",
        ),
    ]])

    await update.effective_message.reply_text(
        "\n".join(lines),
        reply_markup=keyboard,
    )


async def history_from_button(query):
    chat_id = query.message.chat_id

    with sqlite3.connect(DB_NAME) as con:
        rows = con.execute("""
            SELECT id, started, champion
            FROM games
            WHERE chat_id = ?
            ORDER BY id DESC
            LIMIT 10
        """, (chat_id,)).fetchall()

    if not rows:
        await query.message.reply_text("Hozircha tarix yo‘q.")
        return

    lines = ["📚 O‘YINLAR TARIXI", ""]
    for game_id, started, champion in rows:
        lines.append(f"O‘yin #{game_id} — {started}")
        lines.append(f"Chempion: {champion}" if champion else "Yakunlanmagan")
        lines.append("")

    await query.message.reply_text("\n".join(lines))


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data.split(":")
    action = data[0]

    if action == "history":
        await history_from_button(query)
        return

    game_id = int(data[1])
    chat_id = query.message.chat_id

    if current_game(chat_id) != game_id:
        await query.message.reply_text(
            "Bu eski o‘yin. Joriy hisob uchun /score yuboring."
        )
        return

    if action == "refresh":
        await show_score(chat_id, game_id, context)
        return

    if action == "round":
        players = [p for p in get_players(game_id) if p[3] == 1]
        if len(players) < 2:
            await query.message.reply_text(
                "Raund kiritish uchun kamida 2 faol o‘yinchi kerak."
            )
            return

        context.user_data["pending_round"] = {
            "game_id": game_id,
            "chat_id": chat_id,
        }

        lines = ["✍️ Faol o‘yinchilarning ballarini shu tartibda yozing:", ""]
        for index, player in enumerate(players, start=1):
            lines.append(f"{index}. {player[1]}")
        lines.extend(["", f"{len(players)} ta raqam kiriting. Masalan: 4 6 2"])

        await query.message.reply_text("\n".join(lines))
        return

    if action == "cancel":
        context.user_data.pop("pending_round", None)
        await query.message.reply_text("Raund bekor qilindi.")
        return

    if action == "save":
        pending = context.user_data.get("pending_round")
        if not pending or pending["game_id"] != game_id:
            await query.message.reply_text(
                "Saqlanadigan raund topilmadi. Qaytadan boshlang."
            )
            return

        players = pending["players"]
        scores = pending["scores"]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        champion = None

        with sqlite3.connect(DB_NAME) as con:
            for player, points in zip(players, scores):
                user_id, name, old_score, _ = player
                new_score = old_score + points
                active = 0 if new_score >= OUT_SCORE else 1

                con.execute("""
                    UPDATE players SET score = ?, active = ?
                    WHERE game_id = ? AND user_id = ?
                """, (new_score, active, game_id, user_id))

                con.execute("""
                    INSERT INTO history
                    (game_id, player_name, points, added_by, created)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    game_id, name, points, query.from_user.full_name, now
                ))

            updated = con.execute("""
                SELECT name, score, active
                FROM players WHERE game_id = ?
            """, (game_id,)).fetchall()

            active_players = [p for p in updated if p[2] == 1]
            if len(updated) >= 2 and len(active_players) == 1:
                champion = active_players[0][0]
                con.execute("""
                    UPDATE games SET finished = ?, champion = ?
                    WHERE id = ?
                """, (now, champion, game_id))

        context.user_data.pop("pending_round", None)

        out_names = [p[0] for p in updated if p[1] >= OUT_SCORE]
        if out_names:
            await query.message.reply_text("❌ SABIN: " + ", ".join(out_names))

        if champion:
            await query.message.reply_text(f"🎉 CHEMPION 🏆: {champion}!")

        await show_score(chat_id, game_id, context)


def main():
    if not TOKEN:
        raise RuntimeError(
            "Render Environment Variables bo‘limiga BOT_TOKEN kiriting."
        )

    setup()

    threading.Thread(
        target=start_web_server,
        daemon=True,
    ).start()

    asyncio.set_event_loop(asyncio.new_event_loop())

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("newgame", newgame))
    app.add_handler(CommandHandler("score", score))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("app", app_command))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, receive_scores)
    )

    print("KartaHisobBot ishga tushmoqda...")
    app.run_polling()


if __name__ == "__main__":
    main()
