import os
import telebot
import re
import requests
import threading
from flask import Flask
from openai import OpenAI
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# Веб-сервер для удержания активным Render
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web, daemon=True).start()

# Конфигурация
TOKEN = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

bot = telebot.TeleBot(TOKEN)
ai_client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")

headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates"
}

temp_storage = {}

def split_into_sentences(text):
    abbreviations = ["Mr", "Mrs", "Dr", "Prof", "Sr", "Jr", "Hr", "Fr", "bzw", "z.B", "d.h", "inkl", "ca", "vgl", "bspw", "usw", "z.T", "S", "Nr"]
    processed_text = text
    for abbr in abbreviations:
        processed_text = processed_text.replace(f"{abbr}.", f"{abbr}<dot>")
    processed_text = processed_text.replace("z. B.", "z<dot>B<dot>")
    raw_sentences = re.split(r'(?<=[.!?])\s+(?=[A-ZÄÖÜa-zäöüß])', processed_text)
    return [s.replace("<dot>", ".").strip() for s in raw_sentences if s.strip()]

def get_keyboard(message_id, idx, total):
    markup = InlineKeyboardMarkup()
    buttons = []
    if idx > 0:
        buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"book:{message_id}:prev"))
    if idx < total - 1:
        buttons.append(InlineKeyboardButton("Далее ➡️", callback_data=f"book:{message_id}:next"))
    if buttons:
        markup.row(*buttons)
    return markup

def get_ai_analysis(sentence, source_lang):
    lang_name = "немецкого" if source_lang == "de" else "английского"
    prompt = (
        f"Ты — личный репетитор по языкам для уровня A2. "
        f"Разбери это предложение из книги (исходный язык — {lang_name}): \"{sentence}\"\n\n"
        f"Ответь cтрого без иероглифов и строго в формате:\n"
        f"🇷🇺 [Естественный перевод на русский]\n"
        f"—————————————————\n"
        f"💡 [Краткий грамматический разбор, максимум 4-5 предложений]"
    
    )
    
    try:
        response = ai_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception:
        response = ai_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content

def save_book_to_db(chat_id, message_id, sentences, source_lang, idx, cache):
    url = f"{SUPABASE_URL}/rest/v1/book_sessions"
    payload = {"chat_id": chat_id, "message_id": message_id, "sentences": sentences, 
               "source_lang": source_lang, "current_index": idx, "cache": cache}
    requests.post(url, headers=headers, json=payload)

@bot.message_handler(commands=['start'])
def func_start(message):
    bot.send_message(message.chat.id, "Привет! Пришли текст или .txt файл книги.")

@bot.message_handler(content_types=['text', 'document'])
def handle_input(message):
    chat_id = message.chat.id
    text = ""
    if message.content_type == 'document':
        file_info = bot.get_file(message.document.file_id)
        downloaded = bot.download_file(file_info.file_path)
        text = downloaded.decode('utf-8', errors='ignore')
    else:
        text = message.text
        
    temp_storage[chat_id] = text
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🇩🇪 Немецкий", callback_data="lang:de"),
               InlineKeyboardButton("🇬🇧 Английский", callback_data="lang:en"))
    bot.send_message(chat_id, "Выберите язык книги:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id
    
    if call.data.startswith("lang:"):
        lang = call.data.split(":")[1]
        text = temp_storage.get(chat_id)
        sentences = split_into_sentences(text)
        
        ai_resp = get_ai_analysis(sentences[0], lang)
        flag = "🇩🇪" if lang == "de" else "🇬🇧"
        final_text = f"{flag} {sentences[0]}\n\n{ai_resp}"
        
        save_book_to_db(chat_id, call.message.message_id, sentences, lang, 0, {"0": final_text})
        bot.edit_message_text(final_text, chat_id, call.message.message_id, reply_markup=get_keyboard(call.message.message_id, 0, len(sentences)))
        
    elif call.data.startswith("book:"):
        _, msg_id, action = call.data.split(":")
        msg_id = int(msg_id)
        
        url = f"{SUPABASE_URL}/rest/v1/book_sessions?message_id=eq.{msg_id}"
        data = requests.get(url, headers=headers).json()[0]
        
        idx = data['current_index']
        if action == "next" and idx < len(data['sentences']) - 1:
            idx += 1
        elif action == "prev" and idx > 0:
            idx -= 1
        else:
            bot.answer_callback_query(call.id, "Граница книги.")
            return
            
        cache = data['cache']
        if str(idx) not in cache:
            ai_resp = get_ai_analysis(data['sentences'][idx], data['source_lang'])
            flag = "🇩🇪" if data['source_lang'] == "de" else "🇬🇧"
            cache[str(idx)] = f"{flag} {data['sentences'][idx]}\n\n{ai_resp}"
            
        save_book_to_db(chat_id, msg_id, data['sentences'], data['source_lang'], idx, cache)
        bot.edit_message_text(cache[str(idx)], chat_id, msg_id, reply_markup=get_keyboard(msg_id, idx, len(data['sentences'])))
        
    bot.answer_callback_query(call.id)

bot.infinity_polling()
