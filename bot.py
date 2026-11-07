import os
import asyncio
import tempfile
from dotenv import load_dotenv
from gtts import gTTS
from mutagen.mp3 import MP3

from pyrogram import Client, filters, idle
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message

from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream


load_dotenv()

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
SESSION_STRING = os.getenv("SESSION_STRING")

if not all([API_ID, API_HASH, BOT_TOKEN, SESSION_STRING]):
    raise RuntimeError("ERROR: .env mein required variables missing hain.")

API_ID = int(API_ID)

bot = None
assistant = None
call_py = None
processing_lock = None


async def generate_audio(text):
    """Hindi text ko MP3 mein convert kare."""

    temp = tempfile.NamedTemporaryFile(
        suffix=".mp3",
        delete=False
    )
    audio_file = temp.name
    temp.close()

    try:
        await asyncio.to_thread(
            lambda: gTTS(text=text, lang="hi").save(audio_file)
        )

        duration = MP3(audio_file).info.length

        return audio_file, duration

    except Exception:
        if os.path.exists(audio_file):
            os.remove(audio_file)
        raise


async def speak_text(chat_id, text, repeat=1):
    """Voice chat mein audio play kare."""

    audio_file = None

    try:
        audio_file, duration = await generate_audio(text)

        for index in range(repeat):

            print(
                f"Playing audio: {index + 1}/{repeat}, "
                f"Chat: {chat_id}"
            )

            await call_py.play(
                chat_id,
                MediaStream(audio_file)
            )

            # Audio complete hone ka wait
            await asyncio.sleep(duration + 1.5)

    finally:
        if audio_file and os.path.exists(audio_file):
            os.remove(audio_file)


async def process_tts(message: Message, repeat: int):

    global processing_lock

    if not message.command or len(message.command) < 2:
        await message.reply_text(
            "Usage: /tts <text>"
        )
        return

    text = " ".join(message.command[1:]).strip()

    if len(text) > 500:
        await message.reply_text(
            "❌ Maximum 500 characters allowed."
        )
        return

    if processing_lock.locked():
        await message.reply_text(
            "⏳ Bot abhi busy hai. Thodi der baad try karein."
        )
        return

    async with processing_lock:

        status = await message.reply_text(
            "🔊 Voice chat mein audio play kar raha hoon..."
        )

        try:
            await speak_text(
                message.chat.id,
                text,
                repeat
            )

            await status.edit_text(
                f"✅ Audio complete! ({repeat} time)"
            )

        except Exception as error:

            print(f"TTS ERROR: {error}")

            await status.edit_text(
                "❌ Audio play nahi ho paya.\n"
                f"Error: {str(error)[:700]}\n\n"
                "Check karein ki group mein voice chat active hai "
                "aur Assistant ko join karne ki permission hai."
            )


async def tts_handler(client, message):
    await process_tts(message, 1)


async def tts2_handler(client, message):
    await process_tts(message, 2)


async def tts3_handler(client, message):
    await process_tts(message, 3)


async def main():

    global bot, assistant, call_py, processing_lock

    print("Bot start ho raha hai...")

    processing_lock = asyncio.Lock()

    # Clients ko running event loop ke andar initialize karein.
    bot = Client(
        "TTS_Bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN
    )

    assistant = Client(
        "TTS_Assistant",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=SESSION_STRING
    )

    call_py = PyTgCalls(assistant)

    # Commands register karein.
    bot.add_handler(
        MessageHandler(
            tts_handler,
            filters.command("tts") & filters.group
        )
    )

    bot.add_handler(
        MessageHandler(
            tts2_handler,
            filters.command("tts2") & filters.group
        )
    )

    bot.add_handler(
        MessageHandler(
            tts3_handler,
            filters.command("tts3") & filters.group
        )
    )

    bot_started = False
    assistant_started = False
    calls_started = False

    try:

        await bot.start()
        bot_started = True
        print("Bot client started.")

        await assistant.start()
        assistant_started = True
        print("Assistant client started.")

        await call_py.start()
        calls_started = True
        print("PyTgCalls started.")

        print("BOT IS RUNNING!")

        await idle()

    finally:

        print("Bot shutting down...")

        if calls_started:
            await call_py.stop()

        if assistant_started:
            await assistant.stop()

        if bot_started:
            await bot.stop()

        print("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())