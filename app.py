import os
import asyncio
import tempfile
from pathlib import Path
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai
from supabase import create_client, Client
import edge_tts 

# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GEMINI_KEY = os.environ.get('GEMINI_KEY')
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL')

client_gemini = genai.Client(api_key=GEMINI_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

PROMPT_PROFESOR = """
Eres el profesor personal de francés de Quebec de un estudiante hispanohablante cuyo nivel actual es A1. Tu misión es ayudarlo a comprender el francés hablado, hablar con naturalidad, ganar confianza y avanzar progresivamente.

REGLA 1. FRANCÉS POR DEFECTO: Habla en francés por defecto. Utiliza frases cortas y naturales. Usa español solo cuando el estudiante lo solicite o no comprenda.
REGLA 2. UNA PREGUNTA A LA VEZ: Haz solamente una pregunta por turno. Espera la respuesta.
REGLA 3. CORRECCIÓN OBLIGATORIA (TEXTO Y AUDIO): CADA VEZ que el estudiante envíe un mensaje (ya sea escrito o una nota de voz), analiza si hay errores. Si hay un error, usa esta estructura: "Petite correction. Ta phrase : [frase original]. Correction : [frase correcta]. Explication : [explicación breve]". Si es un audio, transcríbelo mentalmente y corrige el error que escuches.
REGLA 4. APRENDER A HABLAR: Prioriza frases completas. No enseñes listas de palabras aisladas.
REGLA 5. MÉTODO APA AUTOMÁTICO: Cada 4 o 5 mensajes, detén la conversación libre. Haz un mini-ejercicio de 3 pasos basado en el tema que estaban hablando: 1. Enséñame una expresión nueva relacionada. 2. Pídeme que haga una frase con ella. 3. Corrígeme.
REGLA 6. COMPRENSIÓN AUDITIVA: Si el estudiante envía un audio, escúchalo bien. Si no le entiendes, pídele que repita. Si le entiendes, corrígelo si es necesario y responde.
REGLA 7. PRONUNCIACIÓN: Practica repetición guiada cuando sea útil.
REGLA 8. FRANCÉS DE QUEBEC: Prioriza expresiones de Quebec, pero distingue del francés estándar.
REGLA 9. HISTORIAS Y CUESTIONARIOS INTERACTIVOS: Cuando el estudiante te pida una historia, cuéntala en francés (respetando el número de palabras que te pida). Al terminar, NO des la traducción. En su lugar, inicia un cuestionario interactivo: hazle SOLO LA PRIMERA pregunta sobre la historia. Espera a que él responda. Cuando él responda, evalúa su respuesta (dile si está bien o mal) y luego hazle SOLO LA SEGUNDA pregunta. Repite esto hasta hacer 3 o 4 preguntas en total.
REGLA 10. MEMORIA: Usa el perfil de errores para personalizar tus respuestas.
REGLA 11. REPASO ESPACIADO: Pide al estudiante recordar expresiones sin mostrárselas primero.
REGLA 12. TEMAS VARIADOS: Habla de la vida cotidiana (familia, compras, clima, viajes, comida). NO hables siempre de trabajo o fábricas. Solo saca ese tema si el estudiante lo menciona primero.
REGLA 13. EXPLICACIONES SENCILLAS: Explica la gramática con palabras sencillas.
REGLA 14. CONFIANZA: Sé paciente, respetuoso y alentador.
REGLA 15. FORMATO: Escribe respuestas claras para Telegram. NO uses asteriscos (*), negritas ni formato markdown.
REGLA 16. ADAPTACIÓN: Elige una sola prioridad para la respuesta.

REGLA FINAL (MUY IMPORTANTE): Al final de tu respuesta, SIEMPRE, debes añadir una línea oculta con este formato exacto:
[ERROR: tema_del_error]
Si no hubo error, escribe: [ERROR: NINGUNO]
Ejemplo si hubo error: [ERROR: Verbo avoir para la edad]
"""

async def obtener_perfil(chat_id):
    try:
        respuesta_db = supabase.table('perfil_errores').select('debilidades').eq('chat_id', chat_id).execute()
        if respuesta_db.data and respuesta_db.data[0]['debilidades']:
            return respuesta_db.data[0]['debilidades']
    except Exception as e:
        print(f"No se pudo leer el perfil: {e}")
    return "Aún no hay errores registrados."

def guardar_perfil_sync(chat_id, error_detectado, perfil_actual):
    try:
        if error_detectado and "NINGUNO" not in error_detectado.upper():
            if error_detectado.lower() not in perfil_actual.lower():
                nuevo_perfil = f"{perfil_actual}, {error_detectado}" if perfil_actual != "Aún no hay errores registrados." else error_detectado
                supabase.table('perfil_errores').upsert({
                    "chat_id": chat_id,
                    "debilidades": nuevo_perfil
                }).execute()
                print(f"✅ ¡ÉXITO TOTAL! Guardado en Supabase: {error_detectado}")
            else:
                print(f"⚠️ El error '{error_detectado}' ya estaba. No se guardó.")
    except Exception as e:
        print(f"❌ ERROR DEL DETECTIVE: {e}")

async def generar_mp3_async(texto, ruta):
    # Voz de Quebec (Antoine) y velocidad al 90% (rate="-10%")
    communicate = edge_tts.Communicate(texto, "fr-CA-AntoineNeural", rate="-10%")
    await communicate.save(ruta)

async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE, texto_usuario=None, audio_bytes=None):
    chat_id = update.effective_chat.id
    mensaje = update.message
    
    perfil = await obtener_perfil(chat_id)
    
    if texto_usuario:
        prompt_completo = f"{PROMPT_PROFESOR}\n\nPERFIL DEL ESTUDIANTE: {perfil}\n\nUsuario dice: {texto_usuario}"
        respuesta = client_gemini.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=prompt_completo
        )
    elif audio_bytes:
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp.write(audio_bytes)
            ruta_audio = tmp.name
        audio_file = client_gemini.files.upload(file=ruta_audio)
        prompt_completo = f"{PROMPT_PROFESOR}\n\nPERFIL DEL ESTUDIANTE: {perfil}\n\nEl estudiante ha enviado una nota de voz. Escúchala, corrígela si es necesario y responde al contenido:"
        respuesta = client_gemini.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=[prompt_completo, audio_file]
        )
        os.remove(ruta_audio)
    else:
        return

    texto_completo = respuesta.text
    
    error_detectado = "NINGUNO"
    texto_visible = texto_completo
    
    if "[ERROR:" in texto_completo:
        partes = texto_completo.split("[ERROR:")
        texto_visible = partes[0].strip()
        error_extraido = partes[1].replace("]", "").strip()
        if error_extraido and error_extraido.upper() != "NINGUNO":
            error_detectado = error_extraido

    texto_audio = texto_visible.replace('*', '').replace('(', '').replace(')', '').replace('[', '').replace(']', '')
    
    await mensaje.reply_text(texto_visible)
    
    if error_detectado != "NINGUNO":
        asyncio.create_task(asyncio.to_thread(guardar_perfil_sync, chat_id, error_detectado, perfil))
    
    ruta_audio_salida = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temporal:
            ruta_audio_salida = temporal.name
        await generar_mp3_async(texto_audio, ruta_audio_salida)
        with open(ruta_audio_salida, "rb") as archivo_audio:
            await mensaje.reply_voice(voice=archivo_audio)
    except Exception as e:
        print(f"Error generando o enviando audio: {e}")
    finally:
        if ruta_audio_salida and os.path.exists(ruta_audio_salida):
            os.remove(ruta_audio_salida)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("Bonjour ! Soy tu profesor de francés de Quebec. Puedes escribirme o enviarme una nota de voz. Vamos a practicar paso a paso.")

async def manejar_texto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message and update.message.text:
        await responder(update, context, texto_usuario=update.message.text)

async def manejar_voz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.voice:
        return
    ruta_entrada = None
    try:
        archivo_telegram = await context.bot.get_file(update.message.voice.file_id)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temporal:
            ruta_entrada = temporal.name
        await archivo_telegram.download_to_drive(custom_path=ruta_entrada)
        audio_bytes = Path(ruta_entrada).read_bytes()
        await responder(update, context, audio_bytes=audio_bytes)
    except Exception as e:
        print(f"Error procesando nota de voz: {e}")
        await update.message.reply_text("No pude procesar esa nota de voz. Intenta enviarla otra vez.")
    finally:
        if ruta_entrada and os.path.exists(ruta_entrada):
            os.remove(ruta_entrada)

def main() -> None:
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_texto))
    application.add_handler(MessageHandler(filters.VOICE, manejar_voz))

    port = int(os.environ.get("PORT", "10000"))
    webhook_url = f"{RENDER_EXTERNAL_URL.rstrip('/')}/{TELEGRAM_TOKEN}"
    print(f"Iniciando bot en el puerto {port}...")
    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=TELEGRAM_TOKEN,
        webhook_url=webhook_url,
    )

if __name__ == "__main__":
    main()
