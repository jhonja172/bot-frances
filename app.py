import os
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

PROMPT_PROFESOR = """
Eres un profesor de francés conversacional y directo. El usuario te escribirá o enviará audios.

TU MISIÓN EN CADA RESPUESTA:
1. CORRIGE: Si el usuario comete un error (gramática, vocabulario o estructura), dile claramente "Pequeño error: ..." y explícale en una sola frase por qué está mal.
2. DA LA FRASE CORRECTA: Escribe la frase exacta que él debería haber dicho.
3. CONTINÚA LA CONVERSACIÓN: Después de corregir, hazle una pregunta nueva y sencilla en francés para que siga practicando.

REGLAS:
- Sé breve. No des discursos largos.
- Responde siempre en francés, pero usa el español solo para la corrección o la traducción (entre paréntesis).
- NO uses asteriscos (*), negritas ni formato markdown. Escribe en texto plano.
- Si no hay ningún error, simplemente responde a lo que dijo y hazle otra pregunta.
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

def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Registrar los comandos
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_texto))
    application.add_handler(MessageHandler(filters.VOICE, manejar_voz))

    # Configuración para Render
    PORT = int(os.environ.get('PORT', 10000))
    WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}/{TELEGRAM_TOKEN}"
    
    print(f"Iniciando bot en modo Webhook en el puerto {PORT}...")
    
    # run_webhook se encarga de todo automáticamente
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_TOKEN,
        webhook_url=WEBHOOK_URL
    )

if __name__ == "__main__":
    main()
