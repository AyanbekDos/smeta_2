import os
import logging
import io
import json
import base64
import tempfile
import re
import gzip
import uuid
from datetime import datetime, timezone
from PIL import Image
import fitz  # PyMuPDF
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import google.generativeai as genai
import asyncio
import httpx
import telegram
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
from google.cloud import storage

# Для веб-сервера (Cloud Run)
from flask import Flask, request
import asyncio

# --- Конфигурация ---
load_dotenv()

# API Ключи
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-1.5-pro-latest") # По умолчанию, если не задано
AZURE_ENDPOINT = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
AZURE_KEY = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Google Cloud Storage
GCS_BUCKET = os.getenv("GCS_BUCKET")
PROMPT_VERSION = os.getenv("PROMPT_VERSION", "v1.0")

genai.configure(api_key=GEMINI_API_KEY)

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - (%(name)s) - %(message)s",
)
logger = logging.getLogger(__name__)

# Константы
(SELECTING_ACTION, AWAITING_CONFIRMATION, AWAITING_MANUAL_PAGE, AWAITING_URL) = range(4)
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

async def run_gemini_with_retry(model, prompt, content, user_id, generation_config=None):
    """Запускает Gemini с retry логикой и таймаутом. content может быть файлом или текстом"""
    retries = 0
    last_exception = None
    request_options = {"timeout": 180}  # Таймаут 3 минуты

    while retries < MAX_RETRIES:
        try:
            logger.info(f"[USER_ID: {user_id}] - Gemini API call attempt {retries + 1}")
            if generation_config:
                response = await model.generate_content_async(
                    [prompt, content], 
                    generation_config=generation_config, 
                    request_options=request_options
                )
            else:
                response = await model.generate_content_async(
                    [prompt, content], 
                    request_options=request_options
                )
            return response
        except Exception as e:
            last_exception = e
            # Расширенная проверка на временные ошибки
            is_retryable = (
                "500" in str(e) or 
                "internal error" in str(e).lower() or
                "InternalServerError" in str(e) or
                "ServiceUnavailable" in str(e) or
                "TooManyRequests" in str(e) or
                "DeadlineExceeded" in str(e)
            )
            
            if is_retryable:
                retries += 1
                wait_time = 5 * (2 ** (retries - 1))
                logger.warning(f"[USER_ID: {user_id}] - Server error ({e}). Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"[USER_ID: {user_id}] - Non-retryable error: {e}")
                raise e
    
    logger.error(f"[USER_ID: {user_id}] - All {MAX_RETRIES} retry attempts failed")
    raise last_exception

def convert_file_sharing_url(url: str) -> str:
    """
    Конвертирует ссылки популярных файлообменников в прямые ссылки для скачивания.
    """
    # Google Drive
    if "drive.google.com" in url:
        # Извлекаем ID файла из ссылки
        match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
        if match:
            file_id = match.group(1)
            return f"https://drive.google.com/uc?export=download&id={file_id}"
    
    # Яндекс.Диск
    elif "disk.yandex" in url:
        # Для Яндекс.Диска нужен специальный API-запрос
        return url  # Обрабатываем отдельно
    
    # Dropbox
    elif "dropbox.com" in url:
        # Заменяем dl=0 на dl=1 для прямого скачивания
        return url.replace("dl=0", "dl=1").replace("?dl=0", "?dl=1")
    
    # WeTransfer и другие
    return url

async def download_file_from_url(url: str, user_id: int) -> bytes:
    """
    Скачивает файл по ссылке с поддержкой различных файлообменников.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    # Конвертируем ссылку если необходимо
    download_url = convert_file_sharing_url(url)
    
    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
        # Сначала проверяем размер файла
        try:
            head_response = await client.head(download_url, headers=headers)
            content_length = head_response.headers.get('content-length')
            if content_length and int(content_length) > 50 * 1024 * 1024:  # 50 MB лимит
                raise ValueError(f"Файл слишком большой ({int(content_length) / 1024 / 1024:.1f} МБ). Максимум 50 МБ.")
        except Exception:
            # Если не удалось проверить размер, продолжаем
            pass
        
        # Скачиваем файл
        response = await client.get(download_url, headers=headers)
        response.raise_for_status()
        
        # Проверяем размер после скачивания
        if len(response.content) > 50 * 1024 * 1024:
            raise ValueError(f"Файл слишком большой ({len(response.content) / 1024 / 1024:.1f} МБ). Максимум 50 МБ.")
        
        return response.content

def is_valid_file_url(text: str) -> bool:
    """
    Проверяет, является ли текст валидной ссылкой на Dropbox.
    """
    url_pattern = r'https?://[^\s]+'
    if not re.match(url_pattern, text):
        return False
    
    return 'dropbox.com' in text.lower()

# --- Google Cloud Storage функции ---

def prepare_telegram_image(page, user_id: int) -> io.BytesIO:
    """
    Подготавливает изображение страницы для отправки в Telegram
    с правильными размерами (10x10 - 10000x10000 пикселей)
    """
    # Создаем изображение с базовым DPI
    pix = page.get_pixmap(dpi=200)
    
    # Проверяем размеры и корректируем для соответствия требованиям Telegram
    max_attempts = 3
    attempt = 0
    
    while attempt < max_attempts:
        width, height = pix.width, pix.height
        
        if width < 10 or height < 10:
            # Размеры слишком маленькие - увеличиваем DPI
            new_dpi = min(600, int(200 * (20 / min(width, height))))
            pix = page.get_pixmap(dpi=new_dpi)
            logger.info(f"[USER_ID: {user_id}] - Image too small ({width}x{height}), increasing DPI to {new_dpi}")
        elif width > 10000 or height > 10000:
            # Размеры слишком большие - уменьшаем DPI
            scale_factor = min(9999 / width, 9999 / height)
            new_dpi = max(50, int(200 * scale_factor))
            pix = page.get_pixmap(dpi=new_dpi)
            logger.info(f"[USER_ID: {user_id}] - Image too large ({width}x{height}), reducing DPI to {new_dpi}")
        else:
            # Размеры в норме
            break
            
        attempt += 1
    
    # Используем PIL для дополнительной проверки и оптимизации
    png_bytes = pix.tobytes("png")
    image = Image.open(io.BytesIO(png_bytes))
    
    # Проверяем финальные размеры и корректируем через PIL если нужно
    if image.width > 10000 or image.height > 10000:
        # Масштабируем через PIL
        image.thumbnail((9999, 9999), Image.Resampling.LANCZOS)
        logger.info(f"[USER_ID: {user_id}] - Final resize via PIL to {image.width}x{image.height}")
    elif image.width < 10 or image.height < 10:
        # Увеличиваем через PIL
        scale = max(2, 15 / min(image.width, image.height))
        new_size = (int(image.width * scale), int(image.height * scale))
        image = image.resize(new_size, Image.Resampling.LANCZOS)
        logger.info(f"[USER_ID: {user_id}] - Final upscale via PIL to {image.width}x{image.height}")
    
    img_buffer = io.BytesIO()
    image.save(img_buffer, format='PNG', optimize=True)
    img_buffer.seek(0)
    
    # Финальная проверка размеров
    final_size_mb = len(img_buffer.getvalue()) / 1024 / 1024
    logger.info(f"[USER_ID: {user_id}] - Final Telegram image: {image.width}x{image.height}, {final_size_mb:.1f}MB")
    
    # Telegram также имеет лимит по размеру файла ~10MB
    if final_size_mb > 10:
        logger.warning(f"[USER_ID: {user_id}] - Image too large for Telegram ({final_size_mb:.1f}MB), compressing...")
        img_buffer = io.BytesIO()
        # Уменьшаем качество для больших изображений
        image.save(img_buffer, format='JPEG', quality=70, optimize=True)
        img_buffer.seek(0)
        final_size_mb = len(img_buffer.getvalue()) / 1024 / 1024
        logger.info(f"[USER_ID: {user_id}] - Compressed to JPEG: {final_size_mb:.1f}MB")
    
    return img_buffer

def clean_filename(filename: str) -> str:
    """Очищает имя файла для использования в GCS"""
    if not filename:
        return "unknown"
    
    # Убираем расширение
    name = os.path.splitext(filename)[0]
    # Заменяем пробелы на подчеркивания
    name = name.replace(" ", "_")
    # Оставляем только алфавит, цифры и подчеркивания
    name = re.sub(r'[^a-zA-Zа-яёА-ЯЁ0-9_]', '', name)
    
    return name or "unknown"

def format_utc_timestamp() -> str:
    """Форматирует текущее время в UTC для имени папки"""
    now = datetime.now(timezone.utc)
    # Формат: 2025-08-02T14-30-45Z (двоеточия заменены на дефисы)
    return now.strftime("%Y-%m-%dT%H-%M-%SZ")

async def save_to_gcs(
    user_id: int,
    pdf_name: str,
    page_image_bytes: bytes,
    ocr_html: str,
    corrected_json: dict,
    find_prompt: str,
    extract_prompt: str
) -> bool:
    """
    Сохраняет все данные в Google Cloud Storage или локально для тестирования
    """
    if not GCS_BUCKET:
        logger.warning("GCS_BUCKET not configured, skipping archive")
        return False
    
    try:
        # Инициализируем клиент GCS
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        
        # Формируем базовый путь
        timestamp = format_utc_timestamp()
        clean_pdf_name = clean_filename(pdf_name)
        base_path = f"user_{user_id}/{clean_pdf_name}_{timestamp}"
        
        logger.info(f"[USER_ID: {user_id}] - Saving to GCS: {base_path}")
        
        # 1. Сохраняем input.webp (конвертируем в WebP lossless)
        webp_buffer = io.BytesIO()
        image = Image.open(io.BytesIO(page_image_bytes))
        image.save(webp_buffer, format='WEBP', lossless=True)
        webp_bytes = webp_buffer.getvalue()
        
        blob = bucket.blob(f"{base_path}/input.webp")
        blob.upload_from_string(webp_bytes, content_type='image/webp')
        
        # 2. Сохраняем ocr_raw.html.gz
        html_gzipped = gzip.compress(ocr_html.encode('utf-8'))
        blob = bucket.blob(f"{base_path}/ocr_raw.html.gz")
        blob.upload_from_string(html_gzipped, content_type='application/gzip')
        
        # 3. Сохраняем corrected.json
        json_data = json.dumps(corrected_json, ensure_ascii=False, indent=2)
        blob = bucket.blob(f"{base_path}/corrected.json")
        blob.upload_from_string(json_data, content_type='application/json; charset=utf-8')
        
        # 4. Сохраняем find_prompt.txt (промпт для поиска таблиц)
        blob = bucket.blob(f"{base_path}/find_prompt.txt")
        blob.upload_from_string(find_prompt, content_type='text/plain; charset=utf-8')
        
        # 5. Сохраняем extract_prompt.txt (промпт для извлечения данных)
        blob = bucket.blob(f"{base_path}/extract_prompt.txt")
        blob.upload_from_string(extract_prompt, content_type='text/plain; charset=utf-8')
        
        # 6. Сохраняем meta.json
        meta_data = {
            "user_id": user_id,
            "pdf_name": pdf_name,
            "clean_pdf_name": clean_pdf_name,
            "timestamp": timestamp,
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
            "find_prompt_length": len(find_prompt),
            "extract_prompt_length": len(extract_prompt),
            "processing_id": str(uuid.uuid4())
        }
        meta_json = json.dumps(meta_data, ensure_ascii=False, indent=2)
        blob = bucket.blob(f"{base_path}/meta.json")
        blob.upload_from_string(meta_json, content_type='application/json')
        
        logger.info(f"[USER_ID: {user_id}] - Successfully saved to GCS: {base_path}")
        
        # Добавляем в суточный Parquet (async task)
        asyncio.create_task(add_to_daily_parquet(
            user_id, clean_pdf_name, webp_bytes, ocr_html, corrected_json, find_prompt, extract_prompt
        ))
        
        return True
        
    except Exception as e:
        logger.error(f"[USER_ID: {user_id}] - Failed to save to GCS: {e}", exc_info=True)
        return False

async def add_to_daily_parquet(
    user_id: int,
    pdf_name: str, 
    webp_bytes: bytes,
    ocr_html: str,
    corrected_json: dict,
    find_prompt: str,
    extract_prompt: str
):
    """
    Добавляет запись в суточный Parquet файл
    """
    if not GCS_BUCKET:
        return
        
    try:
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        
        # Формируем имя файла для сегодня
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        parquet_path = f"dataset/{today}.parquet"
        
        # Создаем новую запись
        new_record = {
            "png_webp": [webp_bytes],
            "ocr_html": [ocr_html],
            "corrected": [json.dumps(corrected_json, ensure_ascii=False)],
            "find_prompt": [find_prompt],
            "extract_prompt": [extract_prompt],
            "user_id": [user_id],
            "pdf_name": [pdf_name],
            "ts": [datetime.now(timezone.utc)]
        }
        
        new_df = pd.DataFrame(new_record)
        
        # Проверяем, существует ли уже файл
        blob = bucket.blob(parquet_path)
        
        if blob.exists():
            # Читаем существующий файл
            existing_data = blob.download_as_bytes()
            existing_df = pd.read_parquet(io.BytesIO(existing_data))
            
            # Объединяем данные
            combined_df = pd.concat([existing_df, new_df], ignore_index=True)
        else:
            combined_df = new_df
        
        # Сохраняем обратно
        parquet_buffer = io.BytesIO()
        combined_df.to_parquet(parquet_buffer, compression='zstd', index=False)
        parquet_buffer.seek(0)
        
        blob.upload_from_file(parquet_buffer, content_type='application/octet-stream')
        
        logger.info(f"[USER_ID: {user_id}] - Added to daily parquet: {parquet_path}")
        
    except Exception as e:
        logger.error(f"[USER_ID: {user_id}] - Failed to add to parquet: {e}", exc_info=True)

# --- Основная логика --- 

async def process_specification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user_id = update.effective_user.id
    try:
        pdf_bytes = context.user_data["pdf_bytes"]
        page_number = context.user_data.get("manual_page_number") or context.user_data.get("found_page_number")

        await chat.send_message("🔍 Распознаю текст на странице...")
        pdf_document = fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf")
        
        if page_number > len(pdf_document):
            pdf_document.close()
            await chat.send_message(f"Ошибка: страница {page_number} не существует. Документ содержит только {len(pdf_document)} страниц.")
            return
        
        page_to_ocr = pdf_document.load_page(page_number - 1)
        
        dpi = 300
        max_file_size = 4 * 1024 * 1024
        
        while dpi >= 150:
            pix = page_to_ocr.get_pixmap(dpi=dpi)
            png_bytes = pix.tobytes("png")
            if len(png_bytes) <= max_file_size:
                break
            dpi -= 50
        
        if len(png_bytes) > max_file_size:
            pdf_document.close()
            await chat.send_message("Ошибка: страница слишком большая для обработки.")
            return
            
        pdf_document.close()

        async with DocumentIntelligenceClient(endpoint=AZURE_ENDPOINT, credential=AzureKeyCredential(AZURE_KEY)) as client:
            poller = await client.begin_analyze_document("prebuilt-layout", png_bytes, content_type="application/octet-stream")
            result = await poller.result()
        if not result.tables:
            await chat.send_message("Не удалось найти таблицу на указанной странице.")
            return

        all_tables_html_parts = [table_to_html(table) for table in result.tables]
        full_html_content = "\n<hr>\n".join(all_tables_html_parts)

        await chat.send_message("🤖 Анализирую структуру таблицы... Это может занять до 3 минут.")
        prompt = get_prompt("extract_and_correct.txt")
        model = genai.GenerativeModel(model_name=GEMINI_MODEL_NAME)
        response = await run_gemini_with_retry(
            model, 
            prompt, 
            full_html_content, 
            user_id, 
            generation_config=GenerationConfig(response_mime_type="application/json")
        )
        
        json_data = json.loads(response.text)

        await chat.send_message("📊 Создаю отчеты...")
        df = flatten_json_to_dataframe(json_data)
        txt_buffer = io.BytesIO(df.to_string(index=False).encode('utf-8'))
        xlsx_buffer = io.BytesIO()
        df.to_excel(xlsx_buffer, index=False, engine='openpyxl')
        xlsx_buffer.seek(0)

        pdf_file_name = context.user_data.get("pdf_file_name", "unknown")
        find_prompt = get_prompt("find_and_validate.txt")
        extract_prompt = get_prompt("extract_and_correct.txt")
        
        pdf_document = fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf")
        page_for_archive = pdf_document.load_page(page_number - 1)
        archive_pix = page_for_archive.get_pixmap(dpi=300)
        archive_png_bytes = archive_pix.tobytes("png")
        pdf_document.close()
        
        await save_to_gcs(
            user_id=user_id,
            pdf_name=pdf_file_name,
            page_image_bytes=archive_png_bytes,
            ocr_html=full_html_content,
            corrected_json=json_data,
            find_prompt=find_prompt,
            extract_prompt=extract_prompt
        )

        await chat.send_message("✅ Готово! Ваша спецификация обработана:")
        await chat.send_document(document=InputFile(xlsx_buffer, filename="specification.xlsx"))
        await chat.send_document(document=InputFile(txt_buffer, filename="specification.txt"))
        logger.info(f"[USER_ID: {user_id}] - FINAL: Reports sent.")

    except Exception as e:
        logger.error(f"[USER_ID: {user_id}] - Error in process_specification: {e}", exc_info=True)
        await chat.send_message("Произошла непредвиденная ошибка при обработке.")
    finally:
        context.user_data.clear()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_message = """👋 Добро пожаловать!

📎 **Загрузите PDF-файл** (до 20 МБ) или 
🔗 **Отправьте ссылку с Dropbox**

💡 Dropbox: https://dropbox.com"""
    
    await update.message.reply_text(welcome_message)
    return SELECTING_ACTION

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not update.message.document:
        return

    # --- Проверка размера файла ПЕРЕД скачиванием ---
    if update.message.document.file_size > 20 * 1024 * 1024: # 20 MB limit
        file_size_mb = update.message.document.file_size / 1024 / 1024
        logger.warning(f"[USER_ID: {user_id}] - PDF rejected: file too large ({file_size_mb:.2f} MB).")
        
        # Красивое сообщение с предложением альтернативы
        message = f"""📁 Файл слишком большой ({file_size_mb:.1f} МБ)

🚫 Telegram позволяет отправлять файлы до 20 МБ
✅ Но мы можем помочь!

🔗 **Загрузите файл на Dropbox:**
👉 https://dropbox.com

📤 **Затем отправьте мне ссылку** и я обработаю ваш документ

💡 **Совет:** Убедитесь, что ссылка открыта для общего доступа

👇 **Отправьте ссылку с Dropbox прямо сейчас:**"""

        await update.message.reply_text(message)
        return AWAITING_URL

    file_id = update.message.document.file_id
    file_name = update.message.document.file_name
    
    # Сохраняем имя файла для использования в GCS
    context.user_data["pdf_file_name"] = file_name
    
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

    await update.message.reply_text("✅ Файл принят. Ищу страницу со спецификацией...")

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
        
        # Подготавливаем изображение для Telegram
        img_buffer = prepare_telegram_image(page, user_id)
        pdf_document.close()

        keyboard = [[InlineKeyboardButton("✅ Да", callback_data="yes"), InlineKeyboardButton("❌ Нет", callback_data="no")]]
        
        try:
            await update.message.reply_photo(
                photo=img_buffer,
                caption=f"Это верная таблица (страница {page_number})?",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except telegram.error.BadRequest as e:
            if "Photo_invalid_dimensions" in str(e):
                # Если не удалось отправить изображение, отправляем текстовое сообщение
                logger.warning(f"[USER_ID: {user_id}] - Failed to send photo, sending text message instead: {e}")
                await update.message.reply_text(
                    f"Найдена страница {page_number}. Это верная таблица?",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                raise e
                
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
        # Пробуем отредактировать caption, если не получается - отправляем новое сообщение
        try:
            await query.edit_message_caption(caption="Отлично! Начинаю обработку...")
        except telegram.error.BadRequest as e:
            if "There is no caption in the message to edit" in str(e):
                logger.info(f"No caption to edit, using edit_message_text instead: {e}")
                await query.edit_message_text(text="Отлично! Начинаю обработку...")
            else:
                raise e
        
        await process_specification(update, context)
        return ConversationHandler.END
    else:
        # Пробуем отредактировать caption, если не получается - отправляем новое сообщение
        try:
            await query.edit_message_caption(caption="Введите правильный номер страницы:")
        except telegram.error.BadRequest as e:
            if "There is no caption in the message to edit" in str(e):
                logger.info(f"No caption to edit, using edit_message_text instead: {e}")
                await query.edit_message_text(text="Введите правильный номер страницы:")
            else:
                raise e
        
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

async def handle_file_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обрабатывает ссылку на файл с файлообменника.
    """
    user_id = update.effective_user.id
    url = update.message.text.strip()
    
    # Извлекаем имя файла из URL для Dropbox
    file_name_from_url = "unknown"
    try:
        # Для Dropbox ссылок пытаемся извлечь имя файла из пути
        import urllib.parse
        parsed_url = urllib.parse.urlparse(url)
        path_parts = parsed_url.path.split('/')
        for part in path_parts:
            if part.endswith('.pdf'):
                file_name_from_url = part
                break
    except:
        file_name_from_url = "dropbox_file.pdf"
    
    # Сохраняем имя файла для использования в GCS
    context.user_data["pdf_file_name"] = file_name_from_url
    
    # Проверяем валидность ссылки
    if not is_valid_file_url(url):
        supported_services = """❌ Поддерживается только Dropbox

🔗 Загрузите файл на Dropbox:
👉 https://dropbox.com

💡 Убедитесь, что ссылка открыта для общего доступа"""
        await update.message.reply_text(supported_services)
        return AWAITING_URL
    
    await update.message.reply_text("🔄 Скачиваю файл по ссылке...")
    
    try:
        # Скачиваем файл
        pdf_bytes = await download_file_from_url(url, user_id)
        logger.info(f"[USER_ID: {user_id}] - File downloaded from URL: {len(pdf_bytes)} bytes")
        
        # Проверяем, что это PDF
        try:
            pdf_document = fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf")
            num_pages = len(pdf_document)
            pdf_document.close()
        except Exception:
            await update.message.reply_text("❌ Файл не является корректным PDF-документом.")
            return AWAITING_URL
        
        # Проверяем количество страниц
        if num_pages > 100:
            await update.message.reply_text(f"❌ Документ слишком большой ({num_pages} страниц). Максимум 100 страниц.")
            return AWAITING_URL
        
        # Сохраняем данные и продолжаем обработку
        context.user_data["pdf_bytes"] = pdf_bytes
        await update.message.reply_text(f"✅ Файл успешно загружен! Ищу страницу со спецификацией...")
        
        # Продолжаем обработку как обычно
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
            
            # Подготавливаем изображение для Telegram
            img_buffer = prepare_telegram_image(page, user_id)
            pdf_document.close()

            keyboard = [[InlineKeyboardButton("✅ Да", callback_data="yes"), InlineKeyboardButton("❌ Нет", callback_data="no")]]
            
            try:
                await update.message.reply_photo(
                    photo=img_buffer,
                    caption=f"Это верная таблица (страница {page_number})?",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            except telegram.error.BadRequest as e:
                if "Photo_invalid_dimensions" in str(e):
                    # Если не удалось отправить изображение, отправляем текстовое сообщение
                    logger.warning(f"[USER_ID: {user_id}] - Failed to send photo, sending text message instead: {e}")
                    await update.message.reply_text(
                        f"Найдена страница {page_number}. Это верная таблица?",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                else:
                    raise e
                    
            return AWAITING_CONFIRMATION

        except Exception as e:
            logger.error(f"[USER_ID: {user_id}] - Error in handle_file_url: {e}", exc_info=True)
            await update.message.reply_text("Ошибка при анализе документа.")
            return ConversationHandler.END
        finally:
            if temp_pdf_path and os.path.exists(temp_pdf_path):
                os.remove(temp_pdf_path)
        
    except ValueError as e:
        # Ошибки размера файла
        await update.message.reply_text(f"❌ {str(e)}")
        return AWAITING_URL
    except Exception as e:
        logger.error(f"[USER_ID: {user_id}] - Error downloading file from URL: {e}", exc_info=True)
        await update.message.reply_text("❌ Не удалось скачать файл. Проверьте ссылку и убедитесь, что файл доступен для скачивания.")
        return AWAITING_URL

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Действие отменено.")
    context.user_data.clear()
    return ConversationHandler.END


# --- Инициализация Telegram приложения в глобальной области ---
logger.info("Initializing Telegram Application...")
genai.configure(api_key=GEMINI_API_KEY)

# Создаем ConversationHandler
conv_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        SELECTING_ACTION: [
            MessageHandler(filters.Document.PDF, handle_document),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_file_url)
        ],
        AWAITING_CONFIRMATION: [CallbackQueryHandler(handle_confirmation_choice)],
        AWAITING_MANUAL_PAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_manual_page_input)],
        AWAITING_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_file_url)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)

# Создаем и настраиваем Telegram приложение
telegram_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
telegram_app.add_handler(conv_handler)

# Инициализируем бота для webhook режима
try:
    import asyncio
    # Создаем постоянный event loop для всего приложения
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Инициализируем синхронно
    loop.run_until_complete(telegram_app.initialize())
    loop.run_until_complete(telegram_app.start())
    
    logger.info("Telegram Application initialized with persistent event loop.")
except Exception as e:
    logger.error(f"Failed to initialize Telegram App: {e}", exc_info=True)
    raise e

# --- Инициализация Flask ---
flask_app = Flask(__name__)

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    """Синхронный обработчик webhook от Telegram"""
    try:
        update = Update.de_json(request.get_json(force=True), telegram_app.bot)
        
        # Используем существующий event loop вместо создания нового
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Если loop уже запущен, используем create_task
            asyncio.create_task(telegram_app.process_update(update))
        else:
            # Если loop не запущен, используем run_until_complete
            loop.run_until_complete(telegram_app.process_update(update))
        
        return "OK", 200
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return "Error", 500

@flask_app.route('/health', methods=['GET'])
def health():
    """Health check для Cloud Run"""
    return "OK", 200

@flask_app.route('/', methods=['GET'])
def root():
    """Root endpoint"""
    return "SmetaI Telegram Bot is running!", 200

# --- Локальный режим (только для разработки) ---
if __name__ == "__main__":
    # Если запускаем локально - используем polling
    logger.info("--- Starting in local polling mode ---")
    telegram_app.run_polling()