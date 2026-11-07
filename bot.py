import os
import asyncio
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import Message
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream
from gtts import gTTS

# Load environment variables
load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
SESSION_STRING = os.getenv("SESSION_STRING")

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

# 3. PyTgCalls Instance
call_py = PyTgCalls(assistant)

# Global lock taaki ek waqt mein ek hi command chale
processing_lock = asyncio.Lock()

async def speak_text(chat_id: int, text: str, repeat: int = 1):
    """Text ko audio mein badal kar VC mein play karta hai"""
    # Text to Speech
    tts = gTTS(text=text, lang='hi') # 'hi' ya 'en' apni zaroorat ke hisaab se badlein
    audio_file = f"tts_{chat_id}.mp3"
    tts.save(audio_file)

    # VC mein play karein 'repeat' baar
    for i in range(repeat):
        # MediaStream object ka istemal karein (naye pytgcalls versions ke liye)
        # Agar purana version hai toh simply audio_file path bhi chal sakta hai
        stream = MediaStream(audio_file)
        await call_py.play(chat_id, stream)
        
        # File play hone ka intezaar karein
        # Ek simple tareeka: duration ka wait karna, ya stream_end event ka wait karna
        # Yahan hum thoda wait kar rahe hain (duration nikalna is example mein complex hai)
        # Behtar hoga ki aap stream_end event handler use karein, par simplicity ke liye:
        await asyncio.sleep(5) # Approx wait, real implementation mein duration calculate karein
        
        # Agar repeat baaki hai toh stop karke wapas play karein
        if i < repeat - 1:
            await call_py.leave_call(chat_id) # Chhota break
            await asyncio.sleep(1)
            # Wapas join karna padega agar leave kiya toh, isliye better hai play karte rehna
            # Neeche wala logic zyada sahi hai: ek hi baar mein loop nahi, balki queue use karein
            pass

    # Cleanup
    if os.path.exists(audio_file):
        os.remove(audio_file)

# --- Command Handlers ---

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
            
            await status_msg.edit_text("✅ Ho gaya!")
            
        except Exception as e:
            await status_msg.edit_text(f"❌ Error: {e}")
            # Debugging ke liye print
            print(f"Error: {e}")

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
            await status_msg.edit_text("✅ 2 baar bol diya!")
        except Exception as e:
            await status_msg.edit_text(f"❌ Error: {e}")

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
            await status_msg.edit_text("✅ 3 baar bol diya!")
        except Exception as e:
            await status_msg.edit_text(f"❌ Error: {e}")

# --- Main Runner ---

async def main():
    print("Bot start ho raha hai...")
    await bot.start()
    await assistant.start()
    await call_py.start()
    
    # Bot ko online rakhein
    from pyrogram import idle
    await idle()
    
    await bot.stop()
    await assistant.stop()

if __name__ == "__main__":
    asyncio.run(main())