import os
import asyncio
import tempfile
from pathlib import Path
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
Eres el profesor personal de francés de Quebec de un estudiante hispanohablante cuyo nivel actual es A1. Tu misión es ayudarlo a comprender el francés hablado, hablar con naturalidad, ganar confianza y avanzar progresivamente hasta B1 y niveles superiores.

Tu enseñanza combina adquisición natural, práctica activa, recuperación de memoria, repetición espaciada y corrección progresiva.

REGLA 1. FRANCÉS POR DEFECTO: Habla en francés por defecto. Utiliza frases cortas y naturales. Usa español solo cuando el estudiante lo solicite o no comprenda.
REGLA 2. UNA PREGUNTA A LA VEZ: Haz solamente una pregunta por turno. Espera la respuesta. Procura que el estudiante hable el 80% del tiempo.
REGLA 3. CORRECCIÓN INTELIGENTE: Corrige máximo 1 o 2 errores importantes por turno. Usa esta estructura: "Petite correction. Ta phrase : [frase original]. Correction : [frase correcta]. Explication : [explicación breve]".
REGLA 4. APRENDER A HABLAR: Prioriza frases completas. No enseñes listas de palabras aisladas.
REGLA 5. MÉTODO APA: Adquirir, Practicar, Ajustar.
REGLA 6. COMPRENSIÓN AUDITIVA: Prioriza escuchar antes de leer. Si no comprende, repite o reformula.
REGLA 7. PRONUNCIACIÓN: Practica repetición guiada cuando sea útil.
REGLA 8. FRANCÉS DE QUEBEC: Prioriza expresiones de Quebec, pero distingue del francés estándar.
REGLA 9. HISTORIAS: Crea historias cortas adaptadas a su nivel.
REGLA 10. MEMORIA: Usa el perfil de errores para personalizar tus respuestas.
REGLA 11. REPASO ESPACIADO: Pide al estudiante recordar expresiones sin mostrárselas primero.
REGLA 12. TRABAJO: Practica vocabulario de inyección de plástico, máquinas, moldes, entrevistas.
REGLA 13. EXPLICACIONES SENCILLAS: Explica la gramática con palabras sencillas.
REGLA 14. CONFIANZA: Sé paciente, respetuoso y alentador.
REGLA 15. FORMATO: Escribe respuestas claras para Telegram. NO uses asteriscos (*), negritas ni formato markdown.
REGLA 16. ADAPTACIÓN: Elige una sola prioridad para la respuesta. Tu objetivo no es hablar mucho, sino conseguir que el estudiante comprenda, responda y mejore.
"""

async def obtener_perfil(chat_id):
    try:
        respuesta_db = supabase.table('perfil_errores').select('debilidades').eq('chat_id', chat_id).execute()
        if respuesta_db.data and respuesta_db.data[0]['debilidades']:
            return respuesta_db.data[0]['debilidades']
    except Exception as e:
        print(f"No se pudo leer el perfil: {e}")
    return "Aún no hay errores registrados."

def guardar_perfil_sync(chat_id, texto_usuario, correccion_bot, perfil_actual):
    try:
        prompt_extraccion = f"""
        Analiza este intercambio:
        Usuario: {texto_usuario}
        Profesor: {correccion_bot}
        Perfil actual: {perfil_actual}
        
        ¿El profesor corrigió un error? 
        - Si SÍ: Devuelve SOLO una frase corta con el tema del error (ej: "Preposiciones", "Verbo être"). No uses puntos.
        - Si NO: Devuelve la palabra NINGUNO.
        """
        respuesta = client_gemini.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=prompt_extraccion
        )
        error_detectado = respuesta.text.strip().replace('"', '').replace('.', '')
        
        if error_detectado and "NINGUNO" not in error_detectado.upper():
            if error_detectado.lower() not in perfil_actual.lower():
                nuevo_perfil = error_detectado if perfil_actual == "Aún no hay errores registrados." else f"{perfil_actual}, {error_detectado}"
                
                # --- SOLUCIÓN ROBUSTA PARA GUARDAR EN SUPABASE ---
                try:
                    # 1. Intentamos actualizar la fila existente
                    respuesta_update = supabase.table('perfil_errores').update({
                        "debilidades": nuevo_perfil
                    }).eq('chat_id', chat_id).execute()
                    
                    # 2. Si no se actualizó nada (porque no existía), insertamos una nueva
                    if not respuesta_update.data:
                        supabase.table('perfil_errores').insert({
                            "chat_id": chat_id,
                            "debilidades": nuevo_perfil
                        }).execute()
                        
                    print(f"✅ ¡ÉXITO! Error guardado en Supabase: {error_detectado}")
                except Exception as db_error:
                    print(f"❌ ERROR DE SUPABASE: {db_error}")
                
    except Exception as e:
        print(f"Error en el detective: {e}")

def generar_mp3_sync(texto, ruta):
    tts = gTTS(text=texto, lang='fr', slow=False)
    tts.save(ruta)

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
        prompt_completo = f"{PROMPT_PROFESOR}\n\nPERFIL DEL ESTUDIANTE: {perfil}\n\nEscucha este audio y responde:"
        respuesta = client_gemini.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=[prompt_completo, audio_file]
        )
        os.remove(ruta_audio)
    else:
        return

    texto_respuesta = respuesta.text
    texto_audio = texto_respuesta.replace('*', '').replace('(', '').replace(')', '').replace('[', '').replace(']', '')
    
    # Enviamos el texto PRIMERO
    await mensaje.reply_text(texto_respuesta)
    
    # Guardamos el perfil en segundo plano
    asyncio.create_task(asyncio.to_thread(guardar_perfil_sync, chat_id, texto_usuario or "[Audio]", texto_respuesta, perfil))
    
    # Generamos y enviamos el audio en segundo plano
    ruta_audio_salida = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temporal:
            ruta_audio_salida = temporal.name
        await asyncio.to_thread(generar_mp3_sync, texto_audio, ruta_audio_salida)
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
