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
1. CORRIGE: Si el usuario comete un error, dile claramente "Pequeño error: ..." y explícale en una sola frase por qué está mal.
2. DA LA FRASE CORRECTA: Escribe la frase exacta que él debería haber dicho.
3. CONTINÚA LA CONVERSACIÓN: Después de corregir, hazle una pregunta nueva y sencilla en francés.

REGLAS:
- Sé breve. No des discursos largos.
- Responde en francés, usa el español solo para la corrección o traducción (entre paréntesis).
- NO uses asteriscos (*), negritas ni formato markdown.
- Si no hay error, simplemente responde y haz otra pregunta.
"""

async def obtener_perfil(chat_id):
    """Busca las debilidades guardadas del usuario."""
    try:
        respuesta_db = supabase.table('perfil_errores').select('debilidades').eq('chat_id', chat_id).execute()
        if respuesta_db.data and respuesta_db.data[0]['debilidades']:
            return respuesta_db.data[0]['debilidades']
    except Exception as e:
        print(f"No se pudo leer el perfil: {e}")
    return "Aún no hay errores registrados."

async def guardar_perfil(chat_id, texto_usuario, correccion_bot):
    """Extrae el error y lo guarda en la lista de debilidades."""
    try:
        # 1. Leemos el perfil actual
        perfil_actual = await obtener_perfil(chat_id)
        
        # 2. Prompt para Gemini: Sé muy estricto
        prompt_extraccion = f"""
        Analiza este intercambio:
        Usuario: {texto_usuario}
        Profesor: {correccion_bot}
        Perfil actual del estudiante: {perfil_actual}
        
        ¿El profesor corrigió un error? 
        - Si SÍ: Devuelve SOLO una frase corta con el tema del error (ej: "Preposiciones", "Verbo être", "Género de sustantivos"). No uses puntos ni formato.
        - Si NO: Devuelve la palabra NINGUNO.
        """
        
        respuesta = client_gemini.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=prompt_extraccion
        )
        error_detectado = respuesta.text.strip().replace('"', '').replace('.', '')
        
        if error_detectado and "NINGUNO" not in error_detectado.upper():
            # 3. Si el error NO está ya en el perfil, lo agregamos
            if error_detectado.lower() not in perfil_actual.lower():
                if perfil_actual == "Aún no hay errores registrados.":
                    nuevo_perfil = error_detectado
                else:
                    nuevo_perfil = f"{perfil_actual}, {error_detectado}"
                
                # 4. Guardamos en Supabase
                supabase.table('perfil_errores').upsert({
                    "chat_id": chat_id,
                    "debilidades": nuevo_perfil
                }).execute()
                print(f"Nuevo error guardado: {error_detectado}")
                
    except Exception as e:
        print(f"Error guardando perfil: {e}")

async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE, texto_usuario: str):
    chat_id = update.effective_chat.id
    
    # 1. Obtener el perfil de errores
    perfil = await obtener_perfil(chat_id)
    
    # 2. Crear prompt con el perfil inyectado
    prompt_completo = f"{PROMPT_PROFESOR}\n\nPERFIL DEL ESTUDIANTE (Sus errores frecuentes): {perfil}\n\nUsuario dice: {texto_usuario}"
    
    # 3. Llamar a Gemini
    respuesta = client_gemini.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents=prompt_completo
    )
    texto_respuesta = respuesta.text
    
    # 4. Guardar el error en el perfil
    await guardar_perfil(chat_id, texto_usuario, texto_respuesta)

    # 5. Responder en Telegram
    await update.message.reply_text(texto_respuesta)

    # 6. Generar y enviar audio
    try:
        texto_limpio = texto_respuesta.replace('*', '').replace('(', '').replace(')', '')
        tts = gTTS(text=texto_limpio, lang='fr', slow=False)
        audio_path = f"respuesta_{chat_id}.mp3"
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
    chat_id = update.effective_chat.id
    file = await context.bot.get_file(update.message.voice.file_id)
    ruta_audio = f"voz_{chat_id}.ogg"
    await file.download_to_drive(ruta_audio)
    
    perfil = await obtener_perfil(chat_id)
    audio_file = client_gemini.files.upload(file=ruta_audio)
    
    prompt_completo = f"{PROMPT_PROFESOR}\n\nPERFIL DEL ESTUDIANTE (Sus errores frecuentes): {perfil}\n\nEscucha este audio y responde:"
    
    respuesta = client_gemini.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents=[prompt_completo, audio_file]
    )
    
    texto_respuesta = respuesta.text
    
    await guardar_perfil(chat_id, "[Audio enviado]", texto_respuesta)
    
    await update.message.reply_text(texto_respuesta)
    
    try:
        texto_limpio = texto_respuesta.replace('*', '').replace('(', '').replace(')', '')
        tts = gTTS(text=texto_limpio, lang='fr', slow=False)
        audio_path = f"respuesta_{chat_id}.mp3"
        tts.save(audio_path)
        await update.message.reply_voice(voice=open(audio_path, 'rb'))
        os.remove(audio_path)
        os.remove(ruta_audio)
    except Exception as e:
        print(f"Error audio: {e}")

def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_texto))
    application.add_handler(MessageHandler(filters.VOICE, manejar_voz))

    PORT = int(os.environ.get('PORT', 10000))
    WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}/{TELEGRAM_TOKEN}"
    
    print(f"Iniciando bot en el puerto {PORT}...")
    
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_TOKEN,
        webhook_url=WEBHOOK_URL
    )

if __name__ == "__main__":
    main()
