import os
import telebot
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

TOKEN = os.environ.get("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

user_sentences = {}
user_index = {}

# def split_into_sentences(text):
#    import re
#    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
#    if not sentences or sentences == ['']:
#        return [text]
#    return [s.strip() for s in sentences if s.strip()]

import re

def split_into_sentences(text):
    # Расширенный список немецких сокращений с точками (и английских тоже)
    abbreviations = [
        "Mr", "Mrs", "Dr", "Prof", "Sr", "Jr",
        "Hr", "Fr", "bzw", "z.B", "d.h", "inkl", "ca", 
        "vgl", "bspw", "usw", "z.T", "S", "Nr"
    ]
    
    processed_text = text
    for abbr in abbreviations:
        # Заменяем точку в сокращении на маркер, чтобы она не считалась концом предложения
        processed_text = processed_text.replace(f"{abbr}.", f"{abbr}<dot>")

    # Также защитим случаи вроде "z. B." (с пробелом)
    processed_text = processed_text.replace("z. B.", "z<dot>B<dot>")

    # Разбиваем по знакам препинания (. ! ?), за которыми идет пробел и заглавная буква
    # Это позволяет корректно разделять длинные абзацы на предложения
    raw_sentences = re.split(r'(?<=[.!?])\s+(?=[A-ZÄÖÜa-zäöüß])', processed_text)
    
    sentences = []
    for s in raw_sentences:
        # Возвращаем точки на место
        restored = s.replace("<dot>", ".")
        cleaned = restored.strip()
        if cleaned:
            sentences.append(cleaned)
            
    return sentences


def get_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("⬅️ Назад", callback_data="prev"),
        InlineKeyboardButton("Далее ➡️", callback_data="next")
    )
    return markup

@bot.message_handler(commands=['start'])
def func_start(message):
    bot.send_message(
        message.chat.id, 
        "Привет! Пришли мне текст главы или книги, и мы начнем чтение по предложениям."
    )

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    chat_id = message.chat.id
    text = message.text
    
    if text.startswith('/'):
        return

    sentences = split_into_sentences(text)
    user_sentences[chat_id] = sentences
    user_index[chat_id] = 0
    
    first_sentence = sentences[0]
    response = f"Книга загружена ({len(sentences)} предложений)!\n\nПредложение №1\n\n{first_sentence}"
    bot.send_message(chat_id, response, reply_markup=get_keyboard())

@bot.callback_query_handler(func=lambda call: True)
def callback_inline(call):
    chat_id = call.message.chat.id
    
    if chat_id not in user_sentences:
        bot.answer_callback_query(call.id, "Сначала отправь текст книги!")
        return
        
    sentences = user_sentences[chat_id]
    idx = user_index.get(chat_id, 0)
    
    if call.data == "next":
        if idx < len(sentences) - 1:
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
            
    user_index[chat_id] = idx
    response = f"Предложение №{idx + 1}\n\n{sentences[idx]}"
    
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=response,
            reply_markup=get_keyboard()
        )
    except Exception:
        pass
        
    bot.answer_callback_query(call.id)

# --- ФЕЙКОВЫЙ СЕРВЕР ДЛЯ RENDER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

def run_server():
    # Render автоматически задает переменную PORT
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()
# ----------------------------------

if __name__ == "__main__":
    # Запускаем сервер-заглушку в параллельном потоке
    threading.Thread(target=run_server, daemon=True).start()
    
    print("Сбрасываем старый вебхук...")
    bot.remove_webhook()
    print("Бот запущен и ждет сообщения...")
    bot.infinity_polling()
