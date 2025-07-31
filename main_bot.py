import os
import logging
import io
import json
import base64
import tempfile
import fitz  # PyMuPDF
import pandas as pd
import google.generativeai as genai
import asyncio
import httpx
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeResult, DocumentTable
from google.generativeai.types import GenerationConfig

# --- Конфигурация ---
load_dotenv()

# API Ключи
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-1.5-pro-latest") # По умолчанию, если не задано
AZURE_ENDPOINT = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
AZURE_KEY = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

genai.configure(api_key=GEMINI_API_KEY)

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - (%(name)s) - %(message)s",
)
logger = logging.getLogger(__name__)

# Константы
(SELECTING_ACTION, AWAITING_CONFIRMATION, AWAITING_MANUAL_PAGE) = range(3)
TEMP_DIR = "temp_bot_files"
MAX_RETRIES = 3

# --- Функции-помощники ---

def get_prompt(file_path: str) -> str:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.error(f"Prompt file not found: {file_path}")
        return ""

def create_ocr_correction_prompt(ocr_text: str) -> str:
    """
    Создает промпт для коррекции OCR ошибок, используя логику из 2b_ocr_correction.py.
    """
    prompt = f"""
🔧 ЗАДАЧА: Коррекция ошибок OCR в таблице металлопроката

📋 ИСХОДНЫЙ ТЕКСТ OCR:
{ocr_text}

🎯 ЦЕЛЬ: Исправить типичные ошибки OCR сохранив структуру таблицы

⚡ КРИТИЧЕСКИЕ ИСПРАВЛЕНИЯ:

1. **РАЗМЕРЫ ПРОФИЛЕЙ:**
   - 20Б1, 20БI, 20BI → 20Ш1 (двутавр широкополочный)
   - [16n, [16п → 16п (швеллер)  
   - [200, [120 → 200, 120 (швеллер)
   - C247 → 24У (швеллер с уклоном)
   - +5, s5 → s5 (лист толщиной 5мм)
   - nucm → лист (просечно-вытяжной)
   
2. **МАРКИ СТАЛИ:**
   - С5, Ст5 → Ст3 (исправление OCR ошибки)
   - С6 → С235 (конвертация в современный стандарт)
   - C255, C245, C275 → С255, С245, С275 (латинская C → кириллическая С)
   
3. **НАЗВАНИЯ ПРОФИЛЕЙ:**
   - Двутавры → Двутавры стальные горячекатанные
   - Швеллеры → Швеллеры стальные горячекатанные
   - Уголки → Уголки стальные равнополочные
   - Листы → Листы стальные горячекатанные

4. **МАССЫ И КОЛИЧЕСТВА:**
   - Исправь очевидные ошибки в числах (например, 1O → 10)
   - Сохрани десятичные дроби как есть
   
🚨 ВАЖНЫЕ ПРАВИЛА:
- Ошибки OCR чаще в БУКВАХ, цифры в 99% случаев правильные
- Сохрани ВСЮ структуру таблицы (строки, столбцы, форматирование)
- НЕ добавляй новую информацию, только исправляй ошибки
- Сохрани все заголовки таблиц как есть
- Исправляй только ОЧЕВИДНЫЕ ошибки распознавания

📋 ФОРМАТ ОТВЕТА:
Верни ТОЛЬКО исправленный текст без дополнительных комментариев.
Структура таблицы должна остаться идентичной исходной.

🔍 НАЧИНАЙ КОРРЕКЦИЮ:
"""
    return prompt

def table_to_html(table: DocumentTable) -> str:
    """Преобразует объект таблицы из Azure в HTML-строку, используя простую сеточную логику."""
    if not table.cells:
        return ""
    
    # Создаем сетку таблицы
    grid = [['' for _ in range(table.column_count)] for _ in range(table.row_count)]
    
    # Заполняем сетку содержимым ячеек
    for cell in table.cells:
        # Проверяем, что индексы не выходят за пределы сетки
        if cell.row_index < table.row_count and cell.column_index < table.column_count:
            grid[cell.row_index][cell.column_index] = cell.content or ''

    # Генерируем HTML
    html_parts = ['<table border="1">']
    for row in grid:
        html_parts.append('<tr>')
        for cell_content in row:
            # Экранируем HTML-сущности
            import html
            html_parts.append(f'<td>{html.escape(cell_content)}</td>')
        html_parts.append('</tr>')
    html_parts.append('</table>')
    
    return '\n'.join(html_parts)

def flatten_json_to_dataframe(data: dict) -> pd.DataFrame:
    flat_list = []
    unit = data.get("единица_измерения", "не указана")
    for profile, p_data in data.get("профили", {}).items():
        for steel, s_data in p_data.get("марки_стали", {}).items():
            for size, z_data in s_data.get("размеры", {}).items():
                # Изменено: теперь перебираем список, а не словарь, чтобы избежать агрегации
                for e_data in z_data.get("элементы", []):
                    flat_list.append({
                        "Наименование профиля": profile,
                        "Марка стали": steel,
                        "Размер профиля": size,
                        "Тип элемента": e_data.get("тип"), # Данные из словаря в списке
                        "Позиции": ", ".join(map(str, e_data.get("позиции", []))),
                        f"Масса, {unit}": e_data.get("масса"),
                    })
    return pd.DataFrame(flat_list)

async def run_gemini_with_retry(model, prompt, gemini_file, user_id):
    retries = 0
    last_exception = None
    while retries < MAX_RETRIES:
        try:
            logger.info(f"[USER_ID: {user_id}] - Gemini API call attempt {retries + 1}")
            response = await model.generate_content_async([prompt, gemini_file])
            return response
        except Exception as e:
            last_exception = e
            if "500" in str(e) or "internal error" in str(e).lower():
                retries += 1
                wait_time = 5 * (2 ** (retries - 1))
                logger.warning(f"[USER_ID: {user_id}] - Server error. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                raise e
    raise last_exception

# --- Основная логика --- 

async def process_specification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user_id = update.effective_user.id
    try:
        pdf_bytes = context.user_data["pdf_bytes"]
        page_number = context.user_data.get("manual_page_number") or context.user_data.get("found_page_number")

        # Этап 2: Извлечение страницы в PNG и распознавание с Azure
        logger.info(f"[USER_ID: {user_id}] - STEP 2: Extracting page {page_number} to PNG and sending to Azure...")
        pdf_document = fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf")
        page_to_ocr = pdf_document.load_page(page_number - 1)
        pix = page_to_ocr.get_pixmap(dpi=300)
        png_bytes = pix.tobytes("png")
        pdf_document.close()

        async with DocumentIntelligenceClient(endpoint=AZURE_ENDPOINT, credential=AzureKeyCredential(AZURE_KEY)) as client:
            poller = await client.begin_analyze_document("prebuilt-layout", png_bytes, content_type="application/octet-stream")
            result = await poller.result()
        if not result.tables:
            await chat.send_message("Не удалось найти таблицу на указанной странице.")
            return

        # --- Объединяем ВСЕ найденные таблицы в один HTML для Gemini ---
        all_tables_html_parts = [table_to_html(table) for table in result.tables]
        full_html_content = "\n<hr>\n".join(all_tables_html_parts) # Соединяем таблицы линией
        logger.info(f"[USER_ID: {user_id}] - Combined HTML from {len(result.tables)} tables generated for Gemini.")

        # --- ОТЛАДКА: Сохраняем этот же HTML в файл ---
        debug_file_path = os.path.join(TEMP_DIR, f"azure_output_{user_id}.html")
        with open(debug_file_path, "w", encoding="utf-8") as f:
            f.write(full_html_content)
        logger.info(f"[USER_ID: {user_id}] - Azure OCR debug HTML saved to {debug_file_path}")
        # --- КОНЕЦ ОТЛАДКИ ---

        # Этап 3: Единая коррекция и извлечение JSON
        logger.info(f"[USER_ID: {user_id}] - STEP 3: Correcting and extracting JSON with Gemini...")
        prompt = get_prompt("extract_and_correct.txt")
        model = genai.GenerativeModel(model_name=GEMINI_MODEL_NAME)
        response = await model.generate_content_async([prompt, full_html_content], generation_config=GenerationConfig(response_mime_type="application/json"))
        
        json_data = json.loads(response.text)
        logger.info(f"[USER_ID: {user_id}] - JSON extracted successfully.")

        # --- ОТЛАДКА: Сохраняем JSON структурированную версию ---
        json_file_path = os.path.join(TEMP_DIR, f"structured_output_{user_id}.json")
        with open(json_file_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)
        logger.info(f"[USER_ID: {user_id}] - JSON structured data saved to {json_file_path}")
        # --- КОНЕЦ ОТЛАДКИ JSON ---

        # Этап 4: Генерация отчетов
        df = flatten_json_to_dataframe(json_data)
        txt_buffer = io.BytesIO(df.to_string(index=False).encode('utf-8'))
        xlsx_buffer = io.BytesIO()
        df.to_excel(xlsx_buffer, index=False, engine='openpyxl')
        xlsx_buffer.seek(0)

        await chat.send_message("Ваша спецификация обработана:")
        await chat.send_document(document=InputFile(xlsx_buffer, filename="specification.xlsx"))
        await chat.send_document(document=InputFile(txt_buffer, filename="specification.txt"))
        logger.info(f"[USER_ID: {user_id}] - FINAL: Reports sent.")

    except Exception as e:
        logger.error(f"[USER_ID: {user_id}] - Error in process_specification: {e}", exc_info=True)
        await chat.send_message("Произошла непредвиденная ошибка при обработке.")
    finally:
        context.user_data.clear()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Загрузите PDF-файл для обработки.")
    return SELECTING_ACTION

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not update.message.document:
        return

    # --- Проверка размера файла ПЕРЕД скачиванием ---
    if update.message.document.file_size > 20 * 1024 * 1024: # 20 MB limit
        logger.warning(f"[USER_ID: {user_id}] - PDF rejected: file too large ({update.message.document.file_size / 1024 / 1024:.2f} MB).")
        await update.message.reply_text(
            "Ошибка: Файл слишком большой. Пожалуйста, загрузите файл размером не более 20 МБ."
        )
        return ConversationHandler.END

    file_id = update.message.document.file_id
    file_name = update.message.document.file_name
    
    await update.message.reply_text(f"Файл '{file_name}' принят. Начинаю загрузку...")

    try:
        file_info = await context.bot.get_file(file_id)
        
        file_url = file_info.file_path

        # Используем httpx для асинхронной потоковой загрузки
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", file_url) as response:
                response.raise_for_status() # Проверяем на ошибки HTTP
                
                pdf_bytes_io = io.BytesIO()
                async for chunk in response.aiter_bytes():
                    pdf_bytes_io.write(chunk)
                pdf_bytes_io.seek(0)
                pdf_bytes = pdf_bytes_io.read()

        context.user_data["pdf_bytes"] = pdf_bytes
        logger.info(f"[USER_ID: {user_id}] - File '{file_name}' downloaded successfully.")

    except telegram.error.BadRequest as e:
        logger.error(f"[USER_ID: {user_id}] - Error getting file info: {e}", exc_info=True)
        await update.message.reply_text(f"Ошибка при получении информации о файле: {e}")
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"[USER_ID: {user_id}] - Error downloading file: {e}", exc_info=True)
        await update.message.reply_text(f"Произошла непредвиденная ошибка при скачивании файла.")
        return ConversationHandler.END

    # Проверка на количество страниц
    try:
        pdf_document_for_check = fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf")
        num_pages = len(pdf_document_for_check)
        pdf_document_for_check.close()
        if num_pages > 100:
            logger.warning(f"[USER_ID: {user_id}] - PDF rejected: too many pages ({num_pages}).")
            await update.message.reply_text(f"Файл слишком большой ({num_pages} страниц). Пожалуйста, загрузите документ, содержащий не более 100 страниц.")
            return ConversationHandler.END
    except Exception as e:
        logger.error(f"[USER_ID: {user_id}] - Failed to check PDF page count: {e}")
        await update.message.reply_text("Не удалось проверить количество страниц в PDF. Файл может быть поврежден.")
        return ConversationHandler.END

    await update.message.reply_text("Файл принят. Провожу первичный анализ...")

    temp_pdf_path = None
    try:
        os.makedirs(TEMP_DIR, exist_ok=True)
        temp_pdf_path = os.path.join(TEMP_DIR, f"{user_id}_check.pdf")
        with open(temp_pdf_path, "wb") as f:
            f.write(pdf_bytes)

        logger.info(f"[USER_ID: {user_id}] - STEP 1: Performing validation and page search with Gemini.")
        gemini_file = genai.upload_file(path=temp_pdf_path)
        prompt = get_prompt("find_and_validate.txt")
        model = genai.GenerativeModel(model_name=GEMINI_MODEL_NAME)
        
        response = await run_gemini_with_retry(model, prompt, gemini_file, user_id)
        genai.delete_file(gemini_file.name)

        try:
            cleaned_text = response.text.replace("```json", "").replace("```", "").strip()
            result = json.loads(cleaned_text)
        except (json.JSONDecodeError, AttributeError, ValueError) as e:
            logger.error(f"[USER_ID: {user_id}] - Failed to decode Gemini response: {e}", exc_info=True)
            await update.message.reply_text("Не удалось распознать ответ от сервиса анализа. Попробуйте другой файл.")
            return ConversationHandler.END

        page_number = result.get("page", 0)
        if page_number == 0:
            await update.message.reply_text("Не удалось найти страницу. Введите номер вручную.")
            return AWAITING_MANUAL_PAGE

        context.user_data["found_page_number"] = page_number
        pdf_document = fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf")
        page = pdf_document.load_page(page_number - 1)
        img_buffer = io.BytesIO(page.get_pixmap(dpi=200).tobytes("png"))

        keyboard = [[InlineKeyboardButton("✅ Да", callback_data="yes"), InlineKeyboardButton("❌ Нет", callback_data="no")]]
        await update.message.reply_photo(
            photo=img_buffer,
            caption=f"Это верная таблица (страница {page_number})?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return AWAITING_CONFIRMATION

    except Exception as e:
        logger.error(f"[USER_ID: {user_id}] - Error in handle_document: {e}", exc_info=True)
        await update.message.reply_text("Ошибка при анализе документа.")
        return ConversationHandler.END
    finally:
        if temp_pdf_path and os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)

async def handle_confirmation_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "yes":
        await query.edit_message_caption(caption="Отлично! Начинаю обработку...")
        await process_specification(update, context)
        return ConversationHandler.END
    else:
        await query.edit_message_caption(caption="Введите правильный номер страницы:")
        return AWAITING_MANUAL_PAGE

async def handle_manual_page_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        page_number = int(update.message.text)
        context.user_data["manual_page_number"] = page_number
        await update.message.reply_text(f"Принято. Начинаю обработку страницы {page_number}...")
        await process_specification(update, context)
        return ConversationHandler.END
    except (ValueError):
        await update.message.reply_text("Введите корректный номер страницы.")
        return AWAITING_MANUAL_PAGE

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Действие отменено.")
    context.user_data.clear()
    return ConversationHandler.END

def main():
    if not all([TELEGRAM_BOT_TOKEN, AZURE_ENDPOINT, AZURE_KEY, GEMINI_API_KEY]):
        logger.critical("FATAL: One or more environment variables are missing!")
        return

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECTING_ACTION: [MessageHandler(filters.Document.PDF, handle_document)],
            AWAITING_CONFIRMATION: [CallbackQueryHandler(handle_confirmation_choice)],
            AWAITING_MANUAL_PAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_manual_page_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv_handler)
    logger.info("--- BOT INITIALIZED. STARTING POLLING... ---")
    app.run_polling()

if __name__ == "__main__":
    main()