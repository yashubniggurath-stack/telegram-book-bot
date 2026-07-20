import os
import json
import telebot
import re
import requests
import threading
from flask import Flask
from google import genai
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# Веб-сервер для удержания открытого порта на Render
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# Запуск веб-сервера в фоновом потоке
threading.Thread(target=run_web, daemon=True).start()

TOKEN = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

bot = telebot.TeleBot(TOKEN)
ai_client = genai.Client()

headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates"
}

def split_into_sentences(text):
    abbreviations = [
        "Mr", "Mrs", "Dr", "Prof", "Sr", "Jr",
        "Hr", "Fr", "bzw", "z.B", "d.h", "inkl", "ca", 
        "vgl", "bspw", "usw", "z.T", "S", "Nr"
    ]
    processed_text = text
    for abbr in abbreviations:
        processed_text = processed_text.replace(f"{abbr}.", f"{abbr}<dot>")
    processed_text = processed_text.replace("z. B.", "z<dot>B<dot>")

    raw_sentences = re.split(r'(?<=[.!?])\s+(?=[A-ZÄÖÜa-zäöüß])', processed_text)
    
    sentences = []
    for s in raw_sentences:
        restored = s.replace("<dot>", ".")
        cleaned = restored.strip()
        if cleaned:
            sentences.append(cleaned)
    return sentences

def get_keyboard(idx, total):
    markup = InlineKeyboardMarkup()
    buttons = []
    if idx > 0:
        buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data="prev"))
    if idx < total - 1:
        buttons.append(InlineKeyboardButton("Далее ➡️", callback_data="next"))
    if buttons:
        markup.row(*buttons)
    return markup

def get_ai_analysis(sentence):
    prompt = (
        f"Ты — личный репетитор по немецкому языку для уровня A2. "
        f"Разбери следующее предложение из книги:\n\n\"{sentence}\"\n\n"
        f"Дай ответ в таком формате:\n"
        f"🇩🇪 **Оригинал:** {sentence}\n"
        f"🇷🇺 **Перевод:** [Естественный перевод на русский]\n"
        f"💡 **Разбор:** [Кратко объясни грамматику, форму глаголов или интересные конструкции, если они есть]"
    )
    try:
        response = ai_client.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt,
        )
        return response.text
    except Exception as e:
        return f"⚠️ Ошибка запроса к API: {e}"

def load_user_data(chat_id):
    url = f"{SUPABASE_URL}/rest/v1/book_progress?chat_id=eq.{chat_id}"
    resp = requests.get(url, headers=headers)
    if resp.status_code == 200 and resp.json():
        data = resp.json()[0]
        return data.get("sentences"), data.get("current_index", 0), data.get("cache", {})
    return None, 0, {}

def save_user_data(chat_id, sentences, current_index, cache):
    url = f"{SUPABASE_URL}/rest/v1/book_progress"
    payload = {
        "chat_id": chat_id,
        "sentences": sentences,
        "current_index": current_index,
        "cache": cache
    }
    requests.post(url, headers=headers, json=payload)

def process_and_start(chat_id, text):
    sentences = split_into_sentences(text)
    if not sentences:
        bot.send_message(chat_id, "⚠️ Не удалось найти предложения в тексте.")
        return

    msg = bot.send_message(chat_id, f"📥 Текст загружен ({len(sentences)} предложений). Обрабатываю первое...")
    
    first_analysis = get_ai_analysis(sentences[0])
    cache = {"0": first_analysis}
    
    save_user_data(chat_id, sentences, 0, cache)
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=msg.message_id,
        text=first_analysis,
        reply_markup=get_keyboard(0, len(sentences))
    )

@bot.message_handler(commands=['start'])
def func_start(message):
    bot.send_message(
        message.chat.id, 
        "Привет! Пришли файл книги (`.txt`) с компьютера или отправь произвольный текст сообщением — "
        "и мы начнем пошаговый разбор с сохранением в облаке."
    )

@bot.message_handler(content_types=['text'])
def handle_text(message):
    if message.text.startswith('/'):
        return
    process_and_start(message.chat.id, message.text)

@bot.message_handler(content_types=['document'])
def handle_document(message):
    chat_id = message.chat.id
    doc = message.document
    
    if not doc.file_name.endswith('.txt'):
        bot.send_message(chat_id, "⚠️ Пожалуйста, отправь файл в формате `.txt`")
        return
        
    try:
        file_info = bot.get_file(doc.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        try:
            text = downloaded_file.decode('utf-8')
        except UnicodeDecodeError:
            text = downloaded_file.decode('cp1251')
            
        process_and_start(chat_id, text)
    except Exception as e:
        bot.send_message(chat_id, f"⚠️ Ошибка при чтении файла: {e}")

@bot.callback_query_handler(func=lambda call: True)
def callback_inline(call):
    chat_id = call.message.chat.id
    
    sentences, idx, cache = load_user_data(chat_id)
    if not sentences:
        bot.answer_callback_query(call.id, "Данные не найдены в базе. Отправьте текст или файл!")
        return
        
    total = len(sentences)
    
    if call.data == "next":
        if idx < total - 1:
            idx += 1
        else:
            bot.answer_callback_query(call.id, "Это последнее предложение.")
            return
    elif call.data == "prev":
        if idx > 0:
            idx -= 1
        else:
            bot.answer_callback_query(call.id, "Это первое предложение.")
            return
            
    idx_str = str(idx)
    if idx_str in cache:
        analysis = cache[idx_str]
    else:
        analysis = get_ai_analysis(sentences[idx])
        cache[idx_str] = analysis
        
    save_user_data(chat_id, sentences, idx, cache)
    
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=analysis,
            reply_markup=get_keyboard(idx, total)
        )
    except Exception:
        pass
        
    bot.answer_callback_query(call.id)

bot.infinity_polling()
