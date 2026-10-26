    # Enviamos primero el texto para que el estudiante no tenga que esperar al audio.
    await mensaje.reply_text(texto_visible)

    # Guardamos el progreso en segundo plano respecto al envío del texto.
    await asyncio.to_thread(guardar_perfil_sync, chat_id, perfil, memoria_nueva)

    # El audio se genera después del texto y fuera del bucle principal de Telegram.
    ruta_audio = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temporal:
            ruta_audio = temporal.name

        await asyncio.to_thread(generar_mp3_sync, texto_audio, ruta_audio)
        with open(ruta_audio, "rb") as archivo_audio:
            await mensaje.reply_voice(voice=archivo_audio)
    except Exception as e:
        print(f"Error generando o enviando audio: {e}")
    finally:
        if ruta_audio and os.path.exists(ruta_audio):
            try:
                os.remove(ruta_audio)
            except OSError as e:
                print(f"No se pudo eliminar el audio temporal: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "Bonjour ! Soy tu profesor de francés de Quebec. "
            "Puedes escribirme o enviarme una nota de voz. "
            "Vamos a practicar paso a paso."
        )


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
        await update.message.reply_text(
            "No pude procesar esa nota de voz. Intenta enviarla otra vez."
        )
    finally:
        if ruta_entrada and os.path.exists(ruta_entrada):
            try:
                os.remove(ruta_entrada)
            except OSError as e:
                print(f"No se pudo eliminar el audio recibido: {e}")


def validar_configuracion() -> None:
    faltantes = []
    for nombre, valor in (
        ("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
        ("GEMINI_KEY", GEMINI_KEY),
        ("SUPABASE_URL", SUPABASE_URL),
        ("SUPABASE_KEY", SUPABASE_KEY),
        ("RENDER_EXTERNAL_URL", RENDER_EXTERNAL_URL),
    ):
        if not valor:
            faltantes.append(nombre)
    if faltantes:
        raise RuntimeError(
            "Faltan variables de entorno en Render: " + ", ".join(faltantes)
        )


def main() -> None:
    validar_configuracion()
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_texto)
    )
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
