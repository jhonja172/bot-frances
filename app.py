import os
import asyncio
import tempfile
from pathlib import Path
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai
from supabase import create_client, Client
import edge_tts
from groq import Groq
from aiohttp import web

# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GEMINI_KEY = os.environ.get('GEMINI_KEY')
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')

client_gemini = genai.Client(api_key=GEMINI_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client_groq = Groq(api_key=GROQ_API_KEY)

contadores_apa = {}

PROMPT_PROFESOR = """
Eres un profesor personal de francés de Quebec. Tu OBJETIVO PRINCIPAL es que el estudiante aprenda a comunicarse con un nativo, perdiendo el miedo a hablar y entendiendo el contexto cultural.

REGLA 1. FRANCÉS POR DEFECTO Y AYUDA CON TRADUCCIÓN: Habla en francés por defecto. Si el estudiante te escribe en español o pide ayuda para decir algo (ej: "¿Cómo se dice...?"), NO le des una clase de gramática. Simplemente dile exactamente: "Tu peux dire : [frase en francés]". Luego, pídele que repita la frase.
REGLA 2. UNA PREGUNTA A LA VEZ Y VARIEDAD: Haz solamente una pregunta por turno. Espera la respuesta. NUNCA repitas la misma pregunta que ya hiciste antes. Si el estudiante responde corto, hazle una pregunta de seguimiento.
REGLA 3. CORRECCIÓN OBLIGATORIA (TEXTO Y AUDIO): CADA VEZ que el estudiante envíe un mensaje, analiza si hay errores. Si hay un error, usa esta estructura: "Petite correction. Ta phrase : [frase original]. Correction : [frase correcta]. Explication : [explicación breve]".
REGLA 4. APRENDER A HABLAR: Prioriza frases completas. No enseñes listas de palabras aisladas.
REGLA 5. COMPRENSIÓN AUDITIVA: Si el estudiante envía un audio, escúchalo bien (se te dará la transcripción).
REGLA 6. PRONUNCIACIÓN: Practica repetición guiada cuando sea útil.
REGLA 7. FRANCÉS DE QUEBEC: Prioriza expresiones de Quebec.
REGLA 8. HISTORIAS Y CUESTIONARIOS INTERACTIVOS: Cuando el estudiante te pida una historia, cuéntala en francés. Al terminar, inicia un cuestionario interactivo: hazle SOLO LA PRIMERA pregunta. Espera a que él responda. Cuando él responda, evalúa y luego hazle SOLO LA SEGUNDA pregunta.
REGLA 9. COMPRENSIÓN DE TEXTOS (MODO EXAMEN): Cuando el estudiante te envíe un texto y te pida preguntas, DEBES ceñirte ESTRICTAMENTE a ese texto. Hazle una pregunta, espera su respuesta, evalúala, y luego hazle la siguiente.
REGLA 10. MEMORIA: Usa el perfil de errores y el historial de conversación para personalizar tus respuestas.
REGLA 11. REPASO ESPACIADO: Pide al estudiante recordar expresiones sin mostrárselas primero.
REGLA 12. TEMAS VARIADOS: Habla de la vida cotidiana. NO hables siempre de trabajo.
REGLA 13. EXPLICACIONES SENCILLAS: Explica la gramática con palabras sencillas.
REGLA 14. CONFIANZA: Sé paciente, respetuoso y alentador.
REGLA 15. FORMATO: Escribe respuestas claras para Telegram. NO uses asteriscos (*), negritas ni formato markdown.
REGLA 16. ADAPTACIÓN: Elige una sola prioridad para la respuesta.

REGLA FINAL (MUY IMPORTANTE): Al final de tu respuesta, SIEMPRE, debes añadir una línea oculta con este formato exacto:
[ERROR: tema_del_error]
Si no hubo error, escribe: [ERROR: NINGUNO]
"""

async def obtener_perfil(chat_id):
    try:
        respuesta_db = supabase.table('perfil_errores').select('debilidades').eq('chat_id', chat_id).execute()
        if respuesta_db.data and respuesta_db.data[0]['debilidades']:
            return respuesta_db.data[0]['debilidades']
    except Exception as e:
        print(f"No se pudo leer el perfil: {e}")
    return "Aún no hay errores registrados."

async def obtener_historial(chat_id):
    try:
        respuesta_db = supabase.table('historial_chat').select('role', 'contenido').eq('chat_id', chat_id).order('fecha', desc=True).limit(6).execute()
        if respuesta_db.data:
            historial = list(reversed(respuesta_db.data))
            texto_historial = "HISTORIAL DE LA CONVERSACIÓN RECIENTE:\n"
            for msg in historial:
                rol = "Estudiante" if msg['role'] == 'user' else "Profesor"
                texto_historial += f"{rol}: {msg['contenido']}\n"
            return texto_historial
    except Exception as e:
        print(f"No se pudo leer el historial: {e}")
    return ""

def guardar_historial_sync(chat_id, role, contenido):
    try:
        supabase.table('historial_chat').insert({
            "chat_id": chat_id, "role": role, "contenido": contenido
        }).execute()
    except Exception as e:
        print(f"Error guardando historial: {e}")

def guardar_perfil_sync(chat_id, error_detectado, perfil_actual):
    try:
        if error_detectado and "NINGUNO" not in error_detectado.upper():
            if error_detectado.lower() not in perfil_actual.lower():
                nuevo_perfil = f"{perfil_actual}, {error_detectado}" if perfil_actual != "Aún no hay errores registrados." else error_detectado
                supabase.table('perfil_errores').upsert({
                    "chat_id": chat_id, "debilidades": nuevo_perfil
                }).execute()
                print(f"✅ Error guardado: {error_detectado}")
    except Exception as e:
        print(f"❌ ERROR DEL DETECTIVE: {e}")

async def generar_mp3_async(texto, ruta):
    communicate = edge_tts.Communicate(texto, "fr-CA-AntoineNeural", rate="-15%")
    await communicate.save(ruta)

async def transcribir_con_groq(ruta_audio):
    def _transcribir():
        with open(ruta_audio, "rb") as file:
            transcripcion = client_groq.audio.transcriptions.create(
                file=file, model="whisper-large-v3-turbo", language="fr", response_format="text"
            )
        return transcripcion
    return await asyncio.to_thread(_transcribir)

async def pensar_con_groq(prompt):
    def _pensar():
        completion = client_groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7, max_tokens=600
        )
        return completion.choices[0].message.content
    return await asyncio.to_thread(_pensar)

async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE, texto_usuario=None):
    chat_id = update.effective_chat.id
    mensaje = update.message
    
    contadores_apa[chat_id] = contadores_apa.get(chat_id, 0) + 1
    orden_apa = ""
    if contadores_apa[chat_id] >= 5:
        orden_apa = "\n\n¡ALTO! ES MOMENTO DEL EJERCICIO APA. Ignora la conversación libre. Basado en el tema que estaban hablando, haz el mini-ejercicio de 3 pasos: 1. Enséñame una expresión nueva relacionada. 2. Pídeme que haga una frase con ella. 3. Corrígeme. Empieza directamente con el paso 1."
        contadores_apa[chat_id] = 0
        print("🎓 Forzando Método APA...")

    perfil = await obtener_perfil(chat_id)
    historial = await obtener_historial(chat_id)
    
    if texto_usuario:
        prompt_completo = f"{PROMPT_PROFESOR}\n\nPERFIL DEL ESTUDIANTE: {perfil}\n\n{historial}\n\nUsuario dice AHORA: {texto_usuario}{orden_apa}"
        try:
            texto_completo = await pensar_con_groq(prompt_completo)
            print("🧠 Respuesta generada por Groq")
        except Exception as e:
            print(f"⚠️ Groq falló, intentando con Gemini... Error: {e}")
            try:
                respuesta = client_gemini.models.generate_content(
                    model='gemini-3.5-flash-lite', contents=prompt_completo
                )
                texto_completo = respuesta.text
                print("🧠 Respuesta generada por Gemini (respaldo)")
            except Exception as e2:
                print(f"❌ Ambos modelos fallaron: {e2}")
                await mensaje.reply_text("El profesor está descansando un momento. Intenta de nuevo en un minuto.")
                return
    else:
        return

    error_detectado = "NINGUNO"
    texto_visible = texto_completo
    if "[ERROR:" in texto_completo:
        partes = texto_completo.split("[ERROR:")
        texto_visible = partes[0].strip()
        error_extraido = partes[1].replace("]", "").strip()
        if error_extraido and error_extraido.upper() != "NINGUNO":
            error_detectado = error_extraido

    texto_audio = texto_visible.replace('*', '').replace('(', '').replace(')', '').replace('[', '').replace(']', '')
    
    asyncio.create_task(asyncio.to_thread(guardar_historial_sync, chat_id, "user", texto_usuario))
    asyncio.create_task(asyncio.to_thread(guardar_historial_sync, chat_id, "model", texto_visible))
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
        print("🎤 Transcribiendo audio con Groq...")
        texto_transcrito = await transcribir_con_groq(ruta_entrada)
        print(f"✅ Texto transcrito: {texto_transcrito}")
        await responder(update, context, texto_usuario=texto_transcrito)
    except Exception as e:
        print(f"❌ Error procesando nota de voz: {e}")
        await update.message.reply_text("No pude procesar esa nota de voz. Intenta enviarla otra vez.")
    finally:
        if ruta_entrada and os.path.exists(ruta_entrada):
            os.remove(ruta_entrada)

# --- NUEVA FUNCIÓN PARA EL HEALTH CHECK ---
async def health_check(request):
    return web.Response(text="OK", status=200)

# --- FUNCIÓN MAIN CORREGIDA ---
def main() -> None:
    port = int(os.environ.get("PORT", "10000"))
    webhook_url = f"{RENDER_EXTERNAL_URL.rstrip('/')}/{TELEGRAM_TOKEN}"

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_texto))
    application.add_handler(MessageHandler(filters.VOICE, manejar_voz))

    # Iniciamos el servidor web DENTRO del bucle de eventos de la aplicación
    async def post_init(app):
        # Configurar el webhook de Telegram
        await app.bot.set_webhook(url=webhook_url)
        print("✅ Webhook configurado automáticamente.")
        
        # Iniciar el servidor de salud para UptimeRobot/Render
        web_app = web.Application()
        web_app.router.add_get('/health', health_check)
        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        print(f"✅ Servidor de salud en http://0.0.0.0:{port}/health")

    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)  # Usamos post_init para arrancar el servidor web
        .build()
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_texto))
    application.add_handler(MessageHandler(filters.VOICE, manejar_voz))

    print(f"Iniciando bot en el puerto {port}...")
    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=TELEGRAM_TOKEN,
        webhook_url=webhook_url,
    )

if __name__ == "__main__":
    main()
