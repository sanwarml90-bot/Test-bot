import os
import sys
import json
import asyncio
import subprocess
import traceback
import logging
import shutil
import time
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import (
    Message, BotCommand,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery,
)
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream

# ---------- Logging (bot.log file + console) ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("aand_bot")
logging.getLogger("pyrogram").setLevel(logging.WARNING)

# Load environment variables
load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
SESSION_STRING = os.getenv("SESSION_STRING")
OWNER_ID = int(os.getenv("OWNER_ID", "7966027018"))

START_TIME = time.time()

# ---------- Stats (stats.json mein save hota hai) ----------
STATS_FILE = "stats.json"
stats = {
    "total_tts": 0,
    "total_chars": 0,
    "groups": [],
    "errors": 0,
    "restarts": 0,
}
try:
    with open(STATS_FILE) as f:
        stats.update(json.load(f))
except Exception:
    pass


def save_stats():
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(stats, f)
    except Exception as e:
        log.warning(f"stats save fail: {e}")


def uptime_str() -> str:
    secs = int(time.time() - START_TIME)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


# 1. Bot Client (Commands handle karne ke liye)
bot = Client(
    "TTS_Bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# 2. Assistant Client (VC join karne ke liye - Userbot)
# Note: Yeh bot nahi hai, yeh aapka account hai
assistant = Client(
    "TTS_Assistant",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING
)

# 3. PyTgCalls Instance — main() mein banega, module level par NAHI.
# (PyTgCalls __init__ mein asyncio.get_event_loop() pakadta hai;
#  module import ke time loop alag hota hai, asyncio.run() ka loop alag —
#  isliye play() mein 'different loop' error aata tha.)
# Handlers global call_py ko use karte hain, jo main() set karega.
call_py = None

# Global lock taaki ek waqt mein ek hi command chale
processing_lock = asyncio.Lock()


def get_audio_duration(path: str, text: str = "") -> float:
    """MP3 ki asli duration (seconds) nikalta hai, ffprobe se.
    Na mile to text length se estimate karta hai."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", path],
            capture_output=True, text=True, timeout=15,
        )
        data = json.loads(out.stdout)
        dur = float(data["format"]["duration"])
        if dur > 0:
            return dur
    except Exception:
        pass
    # Fallback: Hindi TTS approx 0.12 sec per character
    return max(5.0, len(text) * 0.12)


def _gen_tts(text: str, audio_file: str) -> int:
    """espeak-ng se Hindi audio banao, phir ffmpeg se 48kHz stereo
    mein convert karo (Telegram VC 48kHz chahta hai; espeak ka
    22050Hz mono seedha bajao to awaaz garbled/sunai nahi deti).
    Thread mein chalega, event loop se alag."""
    raw = audio_file.replace(".wav", "_raw.wav")
    r1 = subprocess.run(
        ["espeak-ng", "-v", "hi+f2", "-s", "150", "-w", raw, text],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if r1.returncode != 0:
        return r1.returncode
    r2 = subprocess.run(
        ["ffmpeg", "-y", "-v", "quiet", "-i", raw,
         "-ar", "48000", "-ac", "2", "-sample_fmt", "s16", audio_file],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        os.remove(raw)
    except OSError:
        pass
    return r2.returncode


async def speak_text(chat_id: int, text: str, repeat: int = 1):
    """Text ko audio mein badal kar VC mein play karta hai.
    espeak-ng (offline Hindi TTS) use karta hai — gTTS yahan
    rate-limited hai (429) aur edge-tts ko WebSocket chahiye
    jo proxy se nahi chalta. Audio ki asli duration tak wait
    karta hai, isliye lamba text beech mein cut nahi hota.
    Repeat mein VC se leave nahi karta, bas dobara play karta hai."""
    # Text to Speech (offline, espeak-ng) — thread mein, loop se alag
    audio_file = f"tts_{chat_id}.wav"
    rc = await asyncio.to_thread(_gen_tts, text, audio_file)
    if rc != 0 or not os.path.exists(audio_file):
        raise RuntimeError("TTS audio banane mein fail hua (espeak-ng)")

    # Poora audio bajne ka time (duration + join/play ka buffer)
    duration = get_audio_duration(audio_file, text)
    wait = duration + 1.5

    try:
        # VC mein play karein 'repeat' baar
        for _ in range(repeat):
            stream = MediaStream(audio_file)
            await call_py.play(chat_id, stream)
            # Poora audio bajne tak wait karein
            await asyncio.sleep(wait)
    finally:
        # Cleanup: file tabhi delete karein jab play poora ho gaya
        if os.path.exists(audio_file):
            os.remove(audio_file)


def friendly_error(e: Exception) -> str:
    """Telegram/PyTgCalls ke error ko user ke liye samajhne layak banao."""
    msg = str(e)
    if "CHANNEL_INVALID" in msg:
        return ("❌ Assistant is group mein nahi hai!\n"
                "sanji-back (@Sanjipro5) ko is group mein add karo, "
                "phir /tts chalega. (Bot aur assistant dono ko group mein hona chahiye.)")
    if "GROUPCALL_NOT_FOUND" in msg or "group call" in msg.lower():
        return "❌ Is group mein koi voice chat on nahi hai. Pehle VC start karo, phir /tts likho."
    if "TelegramServerError" in type(e).__name__ or "TelegramServerError" in msg:
        return ("❌ Telegram ke voice server mein problem hai, abhi connect nahi ho raha. "
                "Thodi der baad phir /tts try karo. (VC ko ek baar band karke dobara on karna bhi try kar sakte ho.)")
    if not msg.strip():
        return f"❌ Error aaya ({type(e).__name__}), dobara /tts try karo."
    return f"❌ Error: {e}"


# ---------- Owner Panel ----------

def panel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Stats", callback_data="panel_stats"),
         InlineKeyboardButton("🟢 Status", callback_data="panel_status")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="panel_broadcast"),
         InlineKeyboardButton("📋 Logs", callback_data="panel_logs")],
        [InlineKeyboardButton("🔄 Restart", callback_data="panel_restart")],
        [InlineKeyboardButton("❌ Close", callback_data="panel_close")],
    ])


PANEL_TEXT = (
    "👑 Owner Panel\n\n"
    "Bot control yahin se karo:\n"
    "• Stats — kitne TTS, kitne groups\n"
    "• Status — bot / assistant / VC + ffmpeg aur espeak check\n"
    "• Broadcast — sab groups ko ek message\n"
    "• Logs — aakhri log lines\n"
    "• Restart — bot ko dobara start karo"
)


def stats_text() -> str:
    return (
        "📊 Bot Stats\n\n"
        f"• Total TTS: {stats['total_tts']}\n"
        f"• Total characters: {stats['total_chars']}\n"
        f"• Groups: {len(stats['groups'])}\n"
        f"• Errors: {stats['errors']}\n"
        f"• Restarts: {stats['restarts']}\n"
        f"• Uptime: {uptime_str()}"
    )


def status_text() -> str:
    espeak = "✅ installed" if shutil.which("espeak-ng") else "❌ missing"
    ffmpeg = "✅ installed" if shutil.which("ffmpeg") else "❌ missing"
    return (
        "🟢 Bot Status\n\n"
        f"• Bot: {'✅ connected' if bot.is_connected else '❌ disconnected'}\n"
        f"• Assistant: {'✅ connected' if assistant.is_connected else '❌ disconnected'}\n"
        f"• PyTgCalls: {'✅ active' if call_py is not None else '❌ not started'}\n"
        f"• espeak-ng: {espeak}\n"
        f"• ffmpeg: {ffmpeg}\n"
        f"• Uptime: {uptime_str()}\n"
        f"• Groups: {len(stats['groups'])}"
    )


def logs_text() -> str:
    try:
        with open("bot.log") as f:
            lines = f.readlines()[-25:]
        body = "".join(lines)[-3000:]
        return "📋 Logs (aakhri 25 lines)\n\n" + body
    except Exception:
        return "📋 Logs\n\nLog file nahi mili."


async def do_restart():
    stats["restarts"] += 1
    save_stats()
    await asyncio.sleep(1)
    try:
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])
    except Exception as e:
        log.error(f"restart execv fail: {e}")
        os._exit(1)


def track_tts(chat_id: int, text: str):
    stats["total_tts"] += 1
    stats["total_chars"] += len(text)
    if chat_id not in stats["groups"]:
        stats["groups"].append(chat_id)
    save_stats()


def track_error():
    stats["errors"] += 1
    save_stats()


# --- Command Handlers ---

@bot.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    await message.reply_text(
        "👋 Main TTS Bot hoon!\n"
        "Group ke voice chat me text bolwaane ke liye:\n"
        "/tts <text> — 1 baar bolunga\n"
        "/tts2 <text> — 2 baar bolunga\n"
        "/tts3 <text> — 3 baar bolunga\n\n"
        "Pehle mujhe group me add karo aur VC on karo."
    )


@bot.on_message(filters.command("help"))
async def help_handler(client: Client, message: Message):
    await message.reply_text(
        "Commands:\n"
        "/tts <text> — VC me 1 baar bolo\n"
        "/tts2 <text> — VC me 2 baar bolo\n"
        "/tts3 <text> — VC me 3 baar bolo\n"
        "/panel — Owner panel (sirf owner)"
    )


@bot.on_message(filters.command("tts") & filters.group)
async def tts_handler(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("Usage: /tts <text>")
        return

    text = " ".join(message.command[1:])
    chat_id = message.chat.id

    if processing_lock.locked():
        await message.reply_text("⏳ Bot abhi vyast hai, kripya pratiksha karein...")
        return

    async with processing_lock:
        status_msg = await message.reply_text("🔊 VC join kar raha hoon aur bol raha hoon...")

        try:
            # Ensure assistant is started
            if not assistant.is_connected:
                await assistant.start()

            # VC mein join karein aur bolein
            # Note: Agar pehle se join hai toh play() automatically handle kar lega
            await speak_text(chat_id, text, repeat=1)
            track_tts(chat_id, text)

            await status_msg.edit_text("✅ Ho gaya!")

        except Exception as e:
            track_error()
            await status_msg.edit_text(friendly_error(e))
            # Debugging ke liye print
            print(f"Error: {e}")
            traceback.print_exc()


@bot.on_message(filters.command("tts2") & filters.group)
async def tts2_handler(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("Usage: /tts2 <text>")
        return

    text = " ".join(message.command[1:])
    chat_id = message.chat.id

    if processing_lock.locked():
        await message.reply_text("⏳ Bot abhi vyast hai...")
        return

    async with processing_lock:
        status_msg = await message.reply_text("🔊 2 baar bolunga...")
        try:
            if not assistant.is_connected:
                await assistant.start()

            await speak_text(chat_id, text, repeat=2)
            track_tts(chat_id, text)
            await status_msg.edit_text("✅ 2 baar bol diya!")
        except Exception as e:
            track_error()
            await status_msg.edit_text(friendly_error(e))
            traceback.print_exc()


@bot.on_message(filters.command("tts3") & filters.group)
async def tts3_handler(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("Usage: /tts3 <text>")
        return

    text = " ".join(message.command[1:])
    chat_id = message.chat.id

    if processing_lock.locked():
        await message.reply_text("⏳ Bot abhi vyast hai...")
        return

    async with processing_lock:
        status_msg = await message.reply_text("🔊 3 baar bolunga...")
        try:
            if not assistant.is_connected:
                await assistant.start()

            await speak_text(chat_id, text, repeat=3)
            track_tts(chat_id, text)
            await status_msg.edit_text("✅ 3 baar bol diya!")
        except Exception as e:
            track_error()
            await status_msg.edit_text(friendly_error(e))
            traceback.print_exc()


# --- Owner Commands (sirf OWNER_ID) ---

@bot.on_message(filters.command("panel"))
async def panel_handler(client: Client, message: Message):
    if not is_owner(message.from_user.id):
        await message.reply_text("❌ Sirf owner use kar sakta hai.")
        return
    await message.reply_text(PANEL_TEXT, reply_markup=panel_keyboard())


@bot.on_message(filters.command("stats"))
async def stats_handler(client: Client, message: Message):
    if not is_owner(message.from_user.id):
        await message.reply_text("❌ Sirf owner use kar sakta hai.")
        return
    await message.reply_text(stats_text())


@bot.on_message(filters.command("status"))
async def status_handler(client: Client, message: Message):
    if not is_owner(message.from_user.id):
        await message.reply_text("❌ Sirf owner use kar sakta hai.")
        return
    await message.reply_text(status_text())


@bot.on_message(filters.command("logs"))
async def logs_handler(client: Client, message: Message):
    if not is_owner(message.from_user.id):
        await message.reply_text("❌ Sirf owner use kar sakta hai.")
        return
    await message.reply_text(logs_text())


@bot.on_message(filters.command("broadcast"))
async def broadcast_handler(client: Client, message: Message):
    if not is_owner(message.from_user.id):
        await message.reply_text("❌ Sirf owner use kar sakta hai.")
        return
    if len(message.command) < 2:
        await message.reply_text("Usage: /broadcast <message>")
        return
    text = " ".join(message.command[1:])
    ok = 0
    fail = 0
    for gid in list(stats["groups"]):
        try:
            await bot.send_message(gid, text)
            ok += 1
        except Exception:
            fail += 1
    await message.reply_text(f"📢 Broadcast done.\n✅ Sent: {ok}\n❌ Failed: {fail}")


@bot.on_message(filters.command("restart"))
async def restart_handler(client: Client, message: Message):
    if not is_owner(message.from_user.id):
        await message.reply_text("❌ Sirf owner use kar sakta hai.")
        return
    await message.reply_text("🔄 Bot restart ho raha hai...")
    await do_restart()


@bot.on_callback_query()
async def panel_callback(client: Client, query: CallbackQuery):
    if not is_owner(query.from_user.id):
        await query.answer("❌ Sirf owner.", show_alert=True)
        return
    data = query.data
    if data == "panel_close":
        await query.message.delete()
        return
    if data == "panel_stats":
        await query.message.edit_text(stats_text(), reply_markup=panel_keyboard())
    elif data == "panel_status":
        await query.message.edit_text(status_text(), reply_markup=panel_keyboard())
    elif data == "panel_logs":
        await query.message.edit_text(logs_text(), reply_markup=panel_keyboard())
    elif data == "panel_broadcast":
        await query.message.edit_text(
            "📢 Broadcast\n\nSab tracked groups ko message bhejne ke liye:\n"
            "/broadcast <message>\n\nExample:\n/broadcast Hello dosto, bot wapas on hai!",
            reply_markup=panel_keyboard(),
        )
    elif data == "panel_restart":
        await query.message.edit_text("🔄 Bot restart ho raha hai...")
        await do_restart()
        return
    await query.answer()


# --- Main Runner ---

async def main():
    global call_py
    log.info("Bot start ho raha hai...")
    await bot.start()
    await assistant.start()
    # Chat mein "/" dabate hi commands menu dikhe, isliye yahan set karte hain
    try:
        await bot.set_bot_commands([
            BotCommand("start", "Bot shuru karo"),
            BotCommand("help", "Commands ki list"),
            BotCommand("tts", "VC me text bolo (1 baar)"),
            BotCommand("tts2", "VC me text bolo (2 baar)"),
            BotCommand("tts3", "VC me text bolo (3 baar)"),
            BotCommand("panel", "Owner panel (sirf owner)"),
        ])
        log.info("Bot commands set ho gaye.")
    except Exception as e:
        log.warning(f"set_bot_commands fail: {e}")
    # PyTgCalls yahin banayein — ab loop chal raha hai, isliye self.loop
    # wahi hoga jisme handlers chalte hain (nahi to 'different loop' error).
    call_py = PyTgCalls(assistant)
    await call_py.start()
    log.info("Bot online hai. /tts se test karo.")

    # Bot ko online rakhein
    from pyrogram import idle
    await idle()

    await bot.stop()
    await assistant.stop()

if __name__ == "__main__":
    asyncio.run(main())
