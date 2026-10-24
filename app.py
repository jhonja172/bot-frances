import os
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai
from supabase import create_client, Client
from gtts import gTTS

# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GEMINI_KEY = os.environ.get('GEMINI_KEY')
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL')

client_gemini = genai.Client(api_key=GEMINI_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__)
application = Application.builder().token(TELEGRAM_TOKEN).build()

PROMPT_PROFESOR = """
Eres un profesor de francés paciente y motivador. El usuario te escribirá o enviará audios.
Tu tarea:
1. Corregir sus errores de francés de forma amable.
2. Explicar brevemente la regla.
3. Responder en francés y luego dar la traducción al español.
IMPORTANTE: No uses asteriscos (*), ni negritas, ni corchetes, ni formato markdown. Escribe en texto plano.
Sé breve y conversacional.
"""

async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE, texto_usuario: str):
    prompt_completo = f"{PROMPT_PROFESOR}\n\nUsuario dice: {texto_usuario}"
    respuesta = client_gemini.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents=prompt_completo
    )
    texto_respuesta = respuesta.text
    
    try:
        supabase.table('historial_chat').insert({
            "chat_id": update.effective_chat.id, "role": "user", "contenido": texto_usuario
        }).execute()
        supabase.table('historial_chat').insert({
            "chat_id": update.effective_chat.id, "role": "model", "contenido": texto_respuesta
        }).execute()
    except Exception as e:
        print(f"Aviso Supabase: {e}")

    await update.message.reply_text(texto_respuesta)

    try:
        texto_limpio = texto_respuesta.replace('*', '').replace('(', '').replace(')', '')
        tts = gTTS(text=texto_limpio, lang='fr', slow=False)
        audio_path = f"respuesta_{update.effective_chat.id}.mp3"
        tts.save(audio_path)
        await update.message.reply_voice(voice=open(audio_path, 'rb'))
        os.remove(audio_path)
    except Exception as e:
        print(f"Error audio: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("¡Bonjour! Soy tu profe de francés. Escríbeme o envíame una nota de voz.")

async def manejar_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await responder(update, context, update.message.text)

async def manejar_voz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = await context.bot.get_file(update.message.voice.file_id)
    ruta_audio = f"voz_{update.effective_chat.id}.ogg"
    await file.download_to_drive(ruta_audio)
    
    audio_file = client_gemini.files.upload(file=ruta_audio)
    prompt_completo = f"{PROMPT_PROFESOR}\n\nEscucha este audio y responde:"
    
    respuesta = client_gemini.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents=[prompt_completo, audio_file]
    )
    
    texto_respuesta = respuesta.text
    await update.message.reply_text(texto_respuesta)
    
    try:
        texto_limpio = texto_respuesta.replace('*', '').replace('(', '').replace(')', '')
        tts = gTTS(text=texto_limpio, lang='fr', slow=False)
        audio_path = f"respuesta_{update.effective_chat.id}.mp3"
        tts.save(audio_path)
        await update.message.reply_voice(voice=open(audio_path, 'rb'))
        os.remove(audio_path)
        os.remove(ruta_audio)
    except Exception as e:
        print(f"Error audio: {e}")

application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_texto))
application.add_handler(MessageHandler(filters.VOICE, manejar_voz))

@app.route(f"/{TELEGRAM_TOKEN}", methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    asyncio.run(application.process_update(update))
    return "ok", 200

@app.route("/")
def index():
    return "Bot de francés funcionando."

@app.route("/set_webhook")
def set_webhook():
    webhook_url = f"{RENDER_EXTERNAL_URL}/{TELEGRAM_TOKEN}"
    asyncio.run(application.bot.set_webhook(url=webhook_url))
    return f"Webhook configurado en: {webhook_url}"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
