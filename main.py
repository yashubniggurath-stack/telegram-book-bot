import os
import telebot
import re
import requests
import threading
import asyncio
from io import BytesIO
import edge_tts
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
    if source_lang == "de":
        prompt_template = os.getenv("DE_PROMPT")
    else:
        prompt_template = os.getenv("EN_PROMPT")
    
    prompt = prompt_template.format(sentence=sentence)
    
    try:
        response = ai_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception:
        response = ai_client.chat.completions.create(
            model="qwen/qwen3.6-27b",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content


# Функция генерации аудио во временную память через edge-tts
async def generate_voice_bytes(text, lang="en"):
    if lang == "en":
        voice = "en-US-SteffanNeural"
        # Делаем темп медленнее и неспешнее с помощью rate (например, -15%)
        communicate = edge_tts.Communicate(text, voice, rate="-25%")
    else:
        voice = "de-DE-KatjaNeural"
        communicate = edge_tts.Communicate(text, voice)
        
    audio_data = BytesIO()
    
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data.write(chunk["data"])
            
    audio_data.seek(0)
    return audio_data


def save_book_to_db(chat_id, message_id, sentences, source_lang, idx, cache, last_audio_id=None):
    url = f"{SUPABASE_URL}/rest/v1/book_sessions"
    payload = {
        "chat_id": chat_id, 
        "message_id": message_id, 
        "sentences": sentences, 
        "source_lang": source_lang, 
        "current_index": idx, 
        "cache": cache
    }
    if last_audio_id is not None:
        payload["last_audio_id"] = last_audio_id
    requests.post(url, headers=headers, json=payload)

def update_last_audio_id(msg_id, new_audio_id):
    url = f"{SUPABASE_URL}/rest/v1/book_sessions?message_id=eq.{msg_id}"
    payload = {"last_audio_id": new_audio_id}
    requests.patch(url, headers=headers, json=payload)

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
    msg_id = call.message.message_id
    
    if call.data.startswith("lang:"):
        lang = call.data.split(":")[1]
        text = temp_storage.get(chat_id)
        sentences = split_into_sentences(text)
        
        current_sentence = sentences[0]
        ai_resp = get_ai_analysis(current_sentence, lang)
        flag = "🇩🇪" if lang == "de" else "🇬🇧"
        final_text = f"{flag} {current_sentence}\n\n{ai_resp}"
        
        # Генерируем и отправляем аудио СВЕРХУ (первым сообщением)
        audio_io = asyncio.run(generate_voice_bytes(current_sentence, lang))
        audio_msg = bot.send_voice(chat_id, audio_io)
        
        # Сохраняем сессию в базу вместе с ID отправленного аудио
        save_book_to_db(chat_id, msg_id, sentences, lang, 0, {"0": final_text}, last_audio_id=audio_msg.message_id)
        
        # Отправляем текст разбора с кнопками под аудио
        bot.edit_message_text(final_text, chat_id, msg_id, reply_markup=get_keyboard(msg_id, 0, len(sentences)))
        
    elif call.data.startswith("book:"):
        _, target_msg_id, action = call.data.split(":")
        target_msg_id = int(target_msg_id)
        
        url = f"{SUPABASE_URL}/rest/v1/book_sessions?message_id=eq.{target_msg_id}"
        response_data = requests.get(url, headers=headers).json()
        if not response_data:
            bot.answer_callback_query(call.id, "Сессия не найдена.")
            return
        data = response_data[0]
        
        idx = data['current_index']
        if action == "next" and idx < len(data['sentences']) - 1:
            idx += 1
        elif action == "prev" and idx > 0:
            idx -= 1
        else:
            bot.answer_callback_query(call.id, "Граница книги.")
            return
            
        cache = data['cache']
        current_sentence = data['sentences'][idx]
        lang = data['source_lang']
        
        if str(idx) not in cache:
            ai_resp = get_ai_analysis(current_sentence, lang)
            flag = "🇩🇪" if lang == "de" else "🇬🇧"
            cache[str(idx)] = f"{flag} {current_sentence}\n\n{ai_resp}"
        
        # 1. Удаляем старое аудио этой книги, если оно было записано
        old_audio_id = data.get('last_audio_id')
        if old_audio_id:
            try:
                bot.delete_message(chat_id, old_audio_id)
            except Exception:
                pass
                
        # 2. Генерируем и отправляем новое аудио
        audio_io = asyncio.run(generate_voice_bytes(current_sentence, lang))
        new_audio_msg = bot.send_voice(chat_id, audio_io)
        
        # 3. Обновляем индекс, кэш и ID нового аудио в базе
        save_book_to_db(chat_id, target_msg_id, data['sentences'], lang, idx, cache, last_audio_id=new_audio_msg.message_id)
        
        # 4. Обновляем текст разбора
        bot.edit_message_text(cache[str(idx)], chat_id, target_msg_id, reply_markup=get_keyboard(target_msg_id, idx, len(data['sentences'])))
        
    bot.answer_callback_query(call.id)

bot.infinity_polling()
