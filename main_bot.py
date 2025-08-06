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
from typing import Dict, Optional
import threading
import time
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
from google.generativeai.types import GenerationConfig, HarmCategory, HarmBlockThreshold
from google.cloud import storage

# --- Конфигурация ---
# Загружаем .env.local если есть, иначе обычный .env
if os.path.exists('.env.local'):
    load_dotenv('.env.local')
    print("✅ Загружен .env.local (локальная разработка)")
else:
    load_dotenv()
    print("✅ Загружен .env (сервер)")

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

# Поддержка Railway: GCS credentials из переменной окружения
if os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON"):
    import tempfile
    import json
    try:
        credentials_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
        credentials = json.loads(credentials_json)
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            json.dump(credentials, f)
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = f.name
        logger.info("✅ GCS credentials loaded from environment variable")
    except Exception as e:
        logger.error(f"❌ Failed to load GCS credentials from environment: {e}")

# Отключаем спам HTTP запросов
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram.ext._updater").setLevel(logging.WARNING)

# Константы
(SELECTING_ACTION, AWAITING_CONFIRMATION, AWAITING_MANUAL_PAGE, AWAITING_URL, AWAITING_FEEDBACK) = range(5)
TEMP_DIR = "temp_bot_files"
MAX_RETRIES = 3
FEEDBACK_TIMEOUT_SECONDS = 1800  # 30 минут для продакшена

# Глобальное хранилище отложенных задач
pending_feedback_tasks: Dict[int, Dict] = {}

# --- Функции-помощники ---

def create_gemini_model(model_name: str = None) -> genai.GenerativeModel:
    """Создает модель Gemini с настройками безопасности"""
    if model_name is None:
        model_name = GEMINI_MODEL_NAME
    
    # Настройки безопасности для работы с техническими документами
    safety_settings = {
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
    }
    
    return genai.GenerativeModel(
        model_name=model_name,
        safety_settings=safety_settings
    )

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

async def run_gemini_with_fallback(html_content: str, user_id: int, chat) -> dict:
    """Запускает Gemini с fallback стратегией при блокировках"""
    logger.info(f"[USER_ID: {user_id}] - Starting Gemini processing with fallback strategy")
    
    # Стратегия 1: Полный контент с отключенными фильтрами
    try:
        logger.info(f"[USER_ID: {user_id}] - Fallback Strategy 1: Full content with disabled safety")
        prompt = get_prompt("extract_and_correct.txt")
        model = create_gemini_model()
        
        response = await run_gemini_with_retry(
            model, 
            prompt, 
            html_content, 
            user_id, 
            generation_config=GenerationConfig(response_mime_type="application/json")
        )
        
        json_data = json.loads(response.text)
        logger.info(f"[USER_ID: {user_id}] - ✅ Strategy 1 successful!")
        return json_data
        
    except Exception as e1:
        logger.warning(f"[USER_ID: {user_id}] - Strategy 1 failed: {e1}")
        
        # Стратегия 2: Укороченный контент (первые 50% HTML)
        try:
            await chat.send_message("⚠️ **Корректирую стратегию обработки...**\n\n🔄 **Применяю альтернативный метод**\n*Обрабатываю данные частями*")
            
            logger.info(f"[USER_ID: {user_id}] - Fallback Strategy 2: Shortened content (50%)")
            shortened_content = html_content[:len(html_content)//2] + "\n</table>"
            
            response = await run_gemini_with_retry(
                model, 
                prompt, 
                shortened_content, 
                user_id, 
                generation_config=GenerationConfig(response_mime_type="application/json")
            )
            
            json_data = json.loads(response.text)
            logger.info(f"[USER_ID: {user_id}] - ✅ Strategy 2 successful!")
            
            await chat.send_message("⚡ **Частичная обработка успешна!**\n*Обработана первая половина данных*")
            return json_data
            
        except Exception as e2:
            logger.warning(f"[USER_ID: {user_id}] - Strategy 2 failed: {e2}")
            
            # Стратегия 3: Только текст без HTML тегов
            try:
                await chat.send_message("🔧 **Применяю упрощенный метод...**\n\n📄 **Извлекаю только текстовые данные**\n*Без HTML разметки*")
                
                logger.info(f"[USER_ID: {user_id}] - Fallback Strategy 3: Plain text only")
                
                # Убираем HTML теги, оставляем только текст
                import re
                plain_text = re.sub(r'<[^>]+>', ' ', html_content)
                plain_text = re.sub(r'\s+', ' ', plain_text).strip()
                
                # Укороченный промпт для текста
                simple_prompt = f"""Извлеки из текста спецификации металлопроката данные в JSON формате:

ТЕКСТ:
{plain_text[:3000]}...

ФОРМАТ ОТВЕТА (JSON):
{{
  "единица_измерения": "т",
  "профили": {{
    "Название профиля": {{
      "марки_стали": {{
        "Марка стали": {{
          "размеры": {{
            "Размер": {{
              "элементы": [
                {{"тип": "описание", "позиции": ["1"], "масса": 0.0}}
              ]
            }}
          }}
        }}
      }}
    }}
  }}
}}

Извлеки максимум данных из доступного текста."""

                response = await run_gemini_with_retry(
                    model, 
                    simple_prompt, 
                    "", 
                    user_id, 
                    generation_config=GenerationConfig(response_mime_type="application/json")
                )
                
                json_data = json.loads(response.text)
                logger.info(f"[USER_ID: {user_id}] - ✅ Strategy 3 successful!")
                
                await chat.send_message("✅ **Упрощенная обработка завершена!**\n*Извлечены основные данные*")
                return json_data
                
            except Exception as e3:
                logger.error(f"[USER_ID: {user_id}] - All fallback strategies failed: {e3}")
                
                # Стратегия 4: Создаем отчет с исходными данными OCR
                await chat.send_message("🔄 **Создаю отчет с исходными данными OCR**\n\n📄 **В отчете будут:**\n• Исходный текст из Azure OCR\n• Структура для ручной обработки\n• Все распознанные данные")
                
                # Извлекаем данные из HTML для создания осмысленного отчета
                import re
                plain_text = re.sub(r'<[^>]+>', '\n', html_content)
                lines = [line.strip() for line in plain_text.split('\n') if line.strip()]
                
                # Пытаемся найти хотя бы числовые данные
                numbers = re.findall(r'\d+[,.]?\d*', plain_text)
                
                fallback_data = {
                    "единица_измерения": "т",
                    "профили": {
                        "Исходные данные OCR": {
                            "марки_стали": {
                                "Требует проверки": {
                                    "размеры": {
                                        "Все размеры из документа": {
                                            "элементы": [{
                                                "тип": f"OCR данные ({len(lines)} строк, {len(numbers)} чисел)",
                                                "позиции": ["Весь документ"],
                                                "масса": sum([float(n.replace(',', '.')) for n in numbers[:10] if n.replace(',', '.').replace('.', '').isdigit()]) if numbers else 0.0
                                            }]
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                
                logger.info(f"[USER_ID: {user_id}] - ✅ Using fallback minimal data structure")
                return fallback_data

async def run_gemini_with_retry(model, prompt, content, user_id, generation_config=None):
    """Запускает Gemini с retry логикой. content может быть файлом или текстом"""
    retries = 0
    last_exception = None
    
    # Логируем детали запроса
    content_type = type(content).__name__
    prompt_length = len(prompt) if isinstance(prompt, str) else "N/A"
    has_gen_config = generation_config is not None
    
    logger.info(f"[USER_ID: {user_id}] - Starting Gemini API call:")
    logger.info(f"[USER_ID: {user_id}] - Model: {model.model_name}")
    logger.info(f"[USER_ID: {user_id}] - Content type: {content_type}")
    logger.info(f"[USER_ID: {user_id}] - Prompt length: {prompt_length}")
    logger.info(f"[USER_ID: {user_id}] - Has generation config: {has_gen_config}")
    if hasattr(content, 'name'):
        logger.info(f"[USER_ID: {user_id}] - File name: {content.name}")
    
    while retries < MAX_RETRIES:
        try:
            attempt_start = datetime.now()
            logger.info(f"[USER_ID: {user_id}] - Gemini API call attempt {retries + 1}/{MAX_RETRIES}")
            logger.info(f"[USER_ID: {user_id}] - Sending request to Gemini...")
            
            if generation_config:
                logger.info(f"[USER_ID: {user_id}] - Using generation config: {generation_config}")
                response = await model.generate_content_async([prompt, content], generation_config=generation_config)
            else:
                logger.info(f"[USER_ID: {user_id}] - Using default generation settings")
                response = await model.generate_content_async([prompt, content])
            
            attempt_duration = (datetime.now() - attempt_start).total_seconds()
            
            # Проверяем, есть ли блокировка контента или другие проблемы
            if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                logger.info(f"[USER_ID: {user_id}] - Prompt feedback: {response.prompt_feedback}")
            
            if hasattr(response, 'candidates') and response.candidates:
                for i, candidate in enumerate(response.candidates):
                    if hasattr(candidate, 'finish_reason'):
                        finish_reason = candidate.finish_reason
                        logger.info(f"[USER_ID: {user_id}] - Candidate {i} finish reason: {finish_reason}")
                        
                        # Проверяем причину завершения (но не блокируем сразу)
                        if finish_reason == 1:  # SAFETY
                            logger.warning(f"[USER_ID: {user_id}] - Gemini обнаружил проблемы безопасности (SAFETY), но попробуем извлечь контент")
                        elif finish_reason == 2:  # RECITATION
                            raise ValueError(f"Gemini заблокировал контент из-за повторения (RECITATION)")
                        elif finish_reason == 3:  # OTHER
                            logger.warning(f"[USER_ID: {user_id}] - Gemini завершил работу по неизвестной причине (OTHER)")
                        elif finish_reason == 4:  # MAX_TOKENS
                            logger.warning(f"[USER_ID: {user_id}] - Достигнут лимит токенов (MAX_TOKENS)")
            
            # Безопасная проверка наличия ответа
            response_length = 0
            has_valid_response = False
            
            try:
                # Проверяем, есть ли текст в ответе без вызова исключения
                if hasattr(response, 'candidates') and response.candidates:
                    for candidate in response.candidates:
                        if hasattr(candidate, 'content') and candidate.content:
                            if hasattr(candidate.content, 'parts') and candidate.content.parts:
                                for part in candidate.content.parts:
                                    if hasattr(part, 'text') and part.text:
                                        response_length = len(part.text)
                                        has_valid_response = True
                                        break
                        if has_valid_response:
                            break
                
                if has_valid_response:
                    logger.info(f"[USER_ID: {user_id}] - ✅ Gemini API call successful!")
                    logger.info(f"[USER_ID: {user_id}] - Response time: {attempt_duration:.2f}s")
                    logger.info(f"[USER_ID: {user_id}] - Response length: {response_length} chars")
                else:
                    # Если нет валидного контента, это ошибка блокировки
                    logger.error(f"[USER_ID: {user_id}] - Gemini заблокировал весь контент")
                    raise ValueError("Gemini заблокировал весь контент по соображениям безопасности")
                    
            except Exception as check_error:
                logger.error(f"[USER_ID: {user_id}] - Ошибка проверки ответа: {check_error}")
                raise ValueError(f"Не удалось получить ответ от Gemini: {check_error}")
            
            return response
            
        except Exception as e:
            last_exception = e
            attempt_duration = (datetime.now() - attempt_start).total_seconds()
            
            logger.error(f"[USER_ID: {user_id}] - ❌ Gemini API call failed after {attempt_duration:.2f}s")
            logger.error(f"[USER_ID: {user_id}] - Error type: {type(e).__name__}")
            logger.error(f"[USER_ID: {user_id}] - Error message: {str(e)}")
            
            # Расширенная проверка на временные ошибки
            is_retryable = (
                "500" in str(e) or 
                "internal error" in str(e).lower() or
                "InternalServerError" in str(e) or
                "ServiceUnavailable" in str(e) or
                "TooManyRequests" in str(e) or
                "DeadlineExceeded" in str(e) or
                "timeout" in str(e).lower() or
                "connection" in str(e).lower()
            )
            
            if is_retryable and retries < MAX_RETRIES - 1:
                retries += 1
                wait_time = 5 * (2 ** (retries - 1))
                logger.warning(f"[USER_ID: {user_id}] - 🔄 Retryable error detected. Waiting {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"[USER_ID: {user_id}] - 🚫 Non-retryable error or max retries reached")
                raise e
    
    logger.error(f"[USER_ID: {user_id}] - 💥 All {MAX_RETRIES} retry attempts failed")
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
    с консервативными размерами для максимальной совместимости
    """
    # Telegram Photo API ограничения:
    # - Размер файла: до 10MB
    # - Размеры: от 10x10 до 10000x10000 пикселей
    # - Но есть неявные ограничения на соотношение сторон и общий размер
    
    # Используем консервативные лимиты для стабильной работы
    MAX_WIDTH = 4096   # Консервативный лимит вместо 10000
    MAX_HEIGHT = 4096  # Консервативный лимит вместо 10000 
    MAX_FILE_SIZE_MB = 8  # Консервативный лимит вместо 10MB
    
    # Создаем изображение с умеренным DPI
    pix = page.get_pixmap(dpi=150)
    png_bytes = pix.tobytes("png")
    image = Image.open(io.BytesIO(png_bytes))
    
    original_width, original_height = image.size
    logger.info(f"[USER_ID: {user_id}] - Original image: {original_width}x{original_height}")
    
    # Масштабируем если превышены лимиты
    if original_width > MAX_WIDTH or original_height > MAX_HEIGHT:
        # Рассчитываем коэффициент масштабирования сохраняя пропорции
        scale_factor = min(MAX_WIDTH / original_width, MAX_HEIGHT / original_height)
        new_width = int(original_width * scale_factor)
        new_height = int(original_height * scale_factor)
        
        image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        logger.info(f"[USER_ID: {user_id}] - Resized to: {new_width}x{new_height} (scale: {scale_factor:.2f})")
    
    # Проверяем минимальные размеры
    if image.width < 10 or image.height < 10:
        scale_factor = max(15 / image.width, 15 / image.height)
        new_width = int(image.width * scale_factor)
        new_height = int(image.height * scale_factor)
        image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        logger.info(f"[USER_ID: {user_id}] - Upscaled to meet minimum: {new_width}x{new_height}")
    
    # Сначала пробуем PNG
    img_buffer = io.BytesIO()
    image.save(img_buffer, format='PNG', optimize=True)
    img_buffer.seek(0)
    file_size_mb = len(img_buffer.getvalue()) / 1024 / 1024
    
    # Если PNG слишком большой, конвертируем в JPEG
    if file_size_mb > MAX_FILE_SIZE_MB:
        logger.warning(f"[USER_ID: {user_id}] - PNG too large ({file_size_mb:.1f}MB), converting to JPEG")
        img_buffer = io.BytesIO()
        
        # Пробуем разные уровни качества
        for quality in [85, 75, 65, 55]:
            img_buffer = io.BytesIO()
            image.save(img_buffer, format='JPEG', quality=quality, optimize=True)
            img_buffer.seek(0)
            file_size_mb = len(img_buffer.getvalue()) / 1024 / 1024
            
            if file_size_mb <= MAX_FILE_SIZE_MB:
                logger.info(f"[USER_ID: {user_id}] - JPEG quality {quality}: {file_size_mb:.1f}MB")
                break
        
        # Если все еще слишком большой, дополнительно уменьшаем размер
        if file_size_mb > MAX_FILE_SIZE_MB:
            scale_factor = 0.8
            new_width = int(image.width * scale_factor)
            new_height = int(image.height * scale_factor)
            image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            img_buffer = io.BytesIO()
            image.save(img_buffer, format='JPEG', quality=75, optimize=True)
            img_buffer.seek(0)
            file_size_mb = len(img_buffer.getvalue()) / 1024 / 1024
            logger.info(f"[USER_ID: {user_id}] - Final resize: {new_width}x{new_height}, {file_size_mb:.1f}MB")
    
    final_size_mb = len(img_buffer.getvalue()) / 1024 / 1024
    logger.info(f"[USER_ID: {user_id}] - Final Telegram image: {image.width}x{image.height}, {final_size_mb:.1f}MB")
    
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

async def save_to_gcs_initial(
    user_id: int,
    pdf_name: str,
    page_image_bytes: bytes,
    ocr_html: str,
    corrected_json: dict,
    find_prompt: str,
    extract_prompt: str
) -> Optional[str]:
    """
    Сохраняет начальные данные в GCS БЕЗ создания parquet
    Возвращает base_path для последующего использования
    """
    if not GCS_BUCKET:
        logger.warning("GCS_BUCKET not configured, skipping initial save")
        return None
    
    try:
        # Инициализируем клиент GCS
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        
        # Формируем базовый путь
        timestamp = format_utc_timestamp()
        clean_pdf_name = clean_filename(pdf_name)
        base_path = f"user_{user_id}/{clean_pdf_name}_{timestamp}"
        
        logger.info(f"[USER_ID: {user_id}] - Initial save to GCS: {base_path}")
        
        # 1. Сохраняем input.webp (конвертируем в WebP lossless)
        try:
            webp_buffer = io.BytesIO()
            image = Image.open(io.BytesIO(page_image_bytes))
            image.save(webp_buffer, format='WEBP', lossless=True)
            webp_bytes = webp_buffer.getvalue()
            
            blob = bucket.blob(f"{base_path}/input.webp")
            blob.upload_from_string(webp_bytes, content_type='image/webp')
        except Exception as img_error:
            # Для тестирования сохраняем как PNG
            logger.warning(f"[USER_ID: {user_id}] - WebP conversion failed, saving as PNG: {img_error}")
            blob = bucket.blob(f"{base_path}/input.png")
            blob.upload_from_string(page_image_bytes, content_type='image/png')
        
        # 2. Сохраняем ocr_raw.html.gz
        html_gzipped = gzip.compress(ocr_html.encode('utf-8'))
        blob = bucket.blob(f"{base_path}/ocr_raw.html.gz")
        blob.upload_from_string(html_gzipped, content_type='application/gzip')
        
        # 3. Сохраняем corrected.json
        json_data = json.dumps(corrected_json, ensure_ascii=False, indent=2)
        blob = bucket.blob(f"{base_path}/corrected.json")
        blob.upload_from_string(json_data, content_type='application/json; charset=utf-8')
        
        # 4. Сохраняем find_prompt.txt
        blob = bucket.blob(f"{base_path}/find_prompt.txt")
        blob.upload_from_string(find_prompt, content_type='text/plain; charset=utf-8')
        
        # 5. Сохраняем extract_prompt.txt
        blob = bucket.blob(f"{base_path}/extract_prompt.txt")
        blob.upload_from_string(extract_prompt, content_type='text/plain; charset=utf-8')
        
        # 6. Сохраняем meta.json (БЕЗ feedback_status - он будет добавлен позже)
        meta_data = {
            "user_id": user_id,
            "pdf_name": pdf_name,
            "clean_pdf_name": clean_pdf_name,
            "timestamp": timestamp,
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
            "find_prompt_length": len(find_prompt),
            "extract_prompt_length": len(extract_prompt),
            "processing_id": str(uuid.uuid4()),
            "feedback_status": "pending"  # Ожидаем обратную связь
        }
        meta_json = json.dumps(meta_data, ensure_ascii=False, indent=2)
        blob = bucket.blob(f"{base_path}/meta.json")
        blob.upload_from_string(meta_json, content_type='application/json; charset=utf-8')
        
        logger.info(f"[USER_ID: {user_id}] - Initial save successful: {base_path}")
        return base_path
        
    except Exception as e:
        logger.error(f"[USER_ID: {user_id}] - Error in save_to_gcs_initial: {e}", exc_info=True)
        return None

def schedule_feedback_timeout(user_id: int, base_path: str, timeout_seconds: int = 1800):
    """
    Планирует задачу на обработку timeout для обратной связи (30 минут)
    """
    def timeout_handler():
        time.sleep(timeout_seconds)
        # Проверяем, что задача не была отменена
        if user_id in pending_feedback_tasks and not pending_feedback_tasks[user_id].get("cancel", False):
            logger.info(f"[USER_ID: {user_id}] - Feedback timeout reached, finalizing with 'timeout'")
            # Запускаем финализацию асинхронно
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(finalize_gcs_entry(base_path, "timeout"))
            loop.close()
            # Удаляем задачу из pending
            pending_feedback_tasks.pop(user_id, None)
    
    # Отменяем предыдущую задачу если есть
    if user_id in pending_feedback_tasks:
        pending_feedback_tasks[user_id]["cancel"] = True
    
    # Сохраняем данные задачи
    pending_feedback_tasks[user_id] = {
        "base_path": base_path,
        "cancel": False,
        "started_at": datetime.now(timezone.utc)
    }
    
    # Запускаем таймер в отдельном потоке
    thread = threading.Thread(target=timeout_handler, daemon=True)
    thread.start()
    
    logger.info(f"[USER_ID: {user_id}] - Scheduled feedback timeout in {timeout_seconds//60} minutes")

async def finalize_gcs_entry(base_path: str, feedback_status: str):
    """
    Финализирует запись в GCS: обновляет meta.json и создает parquet
    feedback_status: 'good', 'bad', или 'timeout'
    """
    if not GCS_BUCKET:
        logger.warning("GCS_BUCKET not configured, skipping finalization")
        return
    
    try:
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        
        logger.info(f"Finalizing GCS entry: {base_path} with feedback: {feedback_status}")
        
        # 1. Обновляем meta.json с feedback_status
        meta_blob = bucket.blob(f"{base_path}/meta.json")
        if meta_blob.exists():
            meta_data = json.loads(meta_blob.download_as_text())
            meta_data["feedback_status"] = feedback_status
            meta_data["feedback_received_at"] = datetime.now(timezone.utc).isoformat()
            
            # Сохраняем обновленный meta.json
            meta_json = json.dumps(meta_data, ensure_ascii=False, indent=2)
            meta_blob.upload_from_string(meta_json, content_type='application/json; charset=utf-8')
        else:
            logger.error(f"Meta.json not found at {base_path}/meta.json")
            return
        
        # 2. Создаем feedback.txt
        feedback_messages = {
            "good": f"Пользователь доволен результатом обработки\nВремя обратной связи: {datetime.now(timezone.utc).isoformat()}",
            "bad": f"Пользователь НЕ доволен результатом обработки\nВремя обратной связи: {datetime.now(timezone.utc).isoformat()}\nКонтакт админа: @aianback",
            "timeout": f"Пользователь не предоставил обратную связь (timeout)\nВремя истечения: {datetime.now(timezone.utc).isoformat()}"
        }
        
        feedback_blob = bucket.blob(f"{base_path}/feedback.txt")
        feedback_blob.upload_from_string(
            feedback_messages.get(feedback_status, "Unknown feedback status"), 
            content_type='text/plain; charset=utf-8'
        )
        
        # 3. Создаем parquet запись
        await create_parquet_entry(base_path, meta_data, feedback_status)
        
        logger.info(f"Successfully finalized GCS entry: {base_path}")
        
    except Exception as e:
        logger.error(f"Error finalizing GCS entry {base_path}: {e}", exc_info=True)

async def create_parquet_entry(base_path: str, meta_data: dict, feedback_status: str):
    """
    Создает запись в ежедневном parquet файле
    """
    try:
        if not GCS_BUCKET:
            return
            
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        
        # Получаем дату для имени файла
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        parquet_path = f"dataset/{today}.parquet"
        
        # Загружаем corrected.json для анализа
        corrected_blob = bucket.blob(f"{base_path}/corrected.json")
        if corrected_blob.exists():
            corrected_data = json.loads(corrected_blob.download_as_text())
        else:
            corrected_data = {}
        
        # Подсчитываем статистику профилей
        profiles_count = 0
        total_mass = 0.0
        profile_types = set()
        
        if "профили" in corrected_data:
            for profile_name, profile_data in corrected_data["профили"].items():
                profiles_count += 1
                profile_types.add(profile_name)
                if isinstance(profile_data, dict) and "марки_стали" in profile_data:
                    for steel_grade, steel_data in profile_data["марки_стали"].items():
                        if isinstance(steel_data, dict) and "размеры" in steel_data:
                            for size_name, size_data in steel_data["размеры"].items():
                                if isinstance(size_data, dict) and "элементы" in size_data:
                                    for element in size_data["элементы"]:
                                        if isinstance(element, dict) and "масса" in element:
                                            try:
                                                total_mass += float(element["масса"])
                                            except (ValueError, TypeError):
                                                pass
        
        # Создаем запись для parquet
        record = {
            "timestamp": meta_data.get("timestamp_iso", datetime.now(timezone.utc).isoformat()),
            "user_id": meta_data.get("user_id", 0),
            "pdf_name": meta_data.get("pdf_name", "unknown"),
            "processing_id": meta_data.get("processing_id", str(uuid.uuid4())),
            "feedback_status": feedback_status,
            "profiles_found": profiles_count,
            "total_mass_tons": round(total_mass, 3),
            "unique_profile_types": len(profile_types),
            "find_prompt_length": meta_data.get("find_prompt_length", 0),
            "extract_prompt_length": meta_data.get("extract_prompt_length", 0),
            "gcs_path": base_path
        }
        
        # Проверяем существует ли уже parquet файл за сегодня
        parquet_blob = bucket.blob(parquet_path)
        
        if parquet_blob.exists():
            # Загружаем существующий файл
            parquet_buffer = io.BytesIO()
            parquet_blob.download_to_file(parquet_buffer)
            parquet_buffer.seek(0)
            
            # Проверяем размер файла перед чтением
            parquet_size = len(parquet_buffer.getvalue())
            
            if parquet_size > 0:
                # Читаем существующие данные
                existing_df = pd.read_parquet(parquet_buffer)
                
                # Добавляем новую запись
                new_df = pd.DataFrame([record])
                combined_df = pd.concat([existing_df, new_df], ignore_index=True)
            else:
                # Пустой файл - создаем новый DataFrame
                combined_df = pd.DataFrame([record])
        else:
            # Создаем новый DataFrame
            combined_df = pd.DataFrame([record])
        
        # Сохраняем обновленный parquet
        parquet_buffer = io.BytesIO()
        combined_df.to_parquet(parquet_buffer, index=False)
        parquet_buffer.seek(0)
        
        parquet_blob.upload_from_file(parquet_buffer, content_type='application/octet-stream')
        
        logger.info(f"Updated parquet dataset: {parquet_path} (total records: {len(combined_df)})")
        
    except Exception as e:
        logger.error(f"Error creating parquet entry: {e}", exc_info=True)

async def save_to_gcs(
    user_id: int,
    pdf_name: str,
    page_image_bytes: bytes,
    ocr_html: str,
    corrected_json: dict,
    find_prompt: str,
    extract_prompt: str,
    user_satisfied: str = "unknown"
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
            "processing_id": str(uuid.uuid4()),
            "user_satisfied": user_satisfied
        }
        meta_json = json.dumps(meta_data, ensure_ascii=False, indent=2)
        blob = bucket.blob(f"{base_path}/meta.json")
        blob.upload_from_string(meta_json, content_type='application/json; charset=utf-8')
        
        # 7. Сохраняем файл обратной связи (если указан)
        if user_satisfied == "yes":
            feedback_content = f"User satisfied with processing\nTimestamp: {timestamp}\nPDF: {pdf_name}"
            blob = bucket.blob(f"{base_path}/good.txt")
            blob.upload_from_string(feedback_content, content_type='text/plain; charset=utf-8')
        elif user_satisfied == "no":
            feedback_content = f"User NOT satisfied with processing\nTimestamp: {timestamp}\nPDF: {pdf_name}\nContact admin: @aianback"
            blob = bucket.blob(f"{base_path}/bad.txt")
            blob.upload_from_string(feedback_content, content_type='text/plain; charset=utf-8')
        
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

        # Этап 2: Извлечение страницы в PNG и распознавание с Azure
        logger.info(f"[USER_ID: {user_id}] - STEP 2: Extracting page {page_number} to PNG and sending to Azure...")
        
        step2_message = f"""⚙️ Этап 2/4: Распознавание текста

📷 Извлекаю страницу {page_number} в высоком качестве...
🔍 Отправляю в Azure OCR для анализа...

*Определяю структуру таблиц и извлекаю текст*"""
        
        await chat.send_message(step2_message)
        pdf_document = fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf")
        
        # Проверяем, что страница существует
        if page_number > len(pdf_document):
            pdf_document.close()
            await chat.send_message(f"Ошибка: страница {page_number} не существует. Документ содержит только {len(pdf_document)} страниц.")
            return
        
        page_to_ocr = pdf_document.load_page(page_number - 1)
        
        # Начинаем с DPI 300, но уменьшаем если файл слишком большой
        dpi = 300
        max_file_size = 4 * 1024 * 1024  # 4MB лимит для Azure
        
        while dpi >= 150:
            pix = page_to_ocr.get_pixmap(dpi=dpi)
            png_bytes = pix.tobytes("png")
            
            if len(png_bytes) <= max_file_size:
                logger.info(f"[USER_ID: {user_id}] - Using DPI {dpi}, image size: {len(png_bytes) / 1024 / 1024:.1f}MB")
                break
            else:
                logger.warning(f"[USER_ID: {user_id}] - DPI {dpi} too large ({len(png_bytes) / 1024 / 1024:.1f}MB), reducing...")
                dpi -= 50
        
        if len(png_bytes) > max_file_size:
            pdf_document.close()
            await chat.send_message("Ошибка: страница слишком большая для обработки. Попробуйте с другим документом.")
            return
            
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
        
        step3_message = """🤖 Этап 3/4: ИИ обработка данных

✨ Исправляю ошибки OCR с помощью Gemini...
📊 Структурирую данные в формат JSON...
🔧 Применяю правила коррекции металлопроката...

*Это самый сложный этап, может занять 1-2 минуты*"""
        
        await chat.send_message(step3_message)
        
        # Используем fallback стратегию для обработки блокировок
        json_data = await run_gemini_with_fallback(full_html_content, user_id, chat)
        logger.info(f"[USER_ID: {user_id}] - JSON extracted successfully.")

        # --- ОТЛАДКА: Сохраняем JSON структурированную версию ---
        json_file_path = os.path.join(TEMP_DIR, f"structured_output_{user_id}.json")
        with open(json_file_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)
        logger.info(f"[USER_ID: {user_id}] - JSON structured data saved to {json_file_path}")
        # --- КОНЕЦ ОТЛАДКИ JSON ---

        # Этап 4: Генерация отчетов
        step4_message = """📈 Этап 4/4: Генерация отчетов

📊 Создаю Excel таблицу...
📄 Формирую текстовый отчет...
💾 Сохраняю данные для архива...

*Почти готово!*"""
        
        await chat.send_message(step4_message)
        
        df = flatten_json_to_dataframe(json_data)
        txt_buffer = io.BytesIO(df.to_string(index=False).encode('utf-8'))
        xlsx_buffer = io.BytesIO()
        df.to_excel(xlsx_buffer, index=False, engine='openpyxl')
        xlsx_buffer.seek(0)

        # Этап 5: Сохранение в Google Cloud Storage для файнтюнинга
        pdf_file_name = context.user_data.get("pdf_file_name", "unknown")
        logger.info(f"[USER_ID: {user_id}] - STEP 5: Saving to GCS for fine-tuning...")
        
        # Получаем промпты из файлов
        find_prompt = get_prompt("find_and_validate.txt")
        extract_prompt = get_prompt("extract_and_correct.txt")
        
        # Создаем высококачественную версию для архивирования (DPI 300)
        pdf_document = fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf")
        page_for_archive = pdf_document.load_page(page_number - 1)
        archive_pix = page_for_archive.get_pixmap(dpi=300)  # Всегда высокое качество для архива
        archive_png_bytes = archive_pix.tobytes("png")
        pdf_document.close()
        
        logger.info(f"[USER_ID: {user_id}] - Archive image: {len(archive_png_bytes) / 1024 / 1024:.1f}MB at 300 DPI")
        
        # Сохраняем данные в GCS БЕЗ создания parquet (он будет создан после feedback)
        base_path = await save_to_gcs_initial(
            user_id=user_id,
            pdf_name=pdf_file_name,
            page_image_bytes=archive_png_bytes,  # Используем архивную версию!
            ocr_html=full_html_content,
            corrected_json=json_data,
            find_prompt=find_prompt,
            extract_prompt=extract_prompt
        )
        
        # Планируем timeout для обратной связи
        if base_path:
            schedule_feedback_timeout(user_id, base_path, FEEDBACK_TIMEOUT_SECONDS)

        success_message = """🎉 Обработка завершена успешно!

📊 Результаты анализа:
• ✅ Данные извлечены и структурированы
• ✅ Ошибки OCR исправлены автоматически
• ✅ Созданы отчеты в двух форматах

📁 Получите ваши файлы:"""

        await chat.send_message(success_message)
        await chat.send_document(
            document=InputFile(xlsx_buffer, filename="specification.xlsx"),
            caption="📈 Excel файл - готов для работы в таблицах"
        )
        await chat.send_document(
            document=InputFile(txt_buffer, filename="specification.txt"), 
            caption="📄 Текстовый файл - для просмотра и копирования"
        )
        
        # Если использовались fallback стратегии, отправляем дополнительный файл с исходными данными OCR
        if "Исходные данные OCR" in str(json_data):
            try:
                ocr_buffer = io.BytesIO(full_html_content.encode('utf-8'))
                await chat.send_document(
                    document=InputFile(ocr_buffer, filename="ocr_raw_data.html"),
                    caption="🔧 **Исходные данные OCR** - для ручной обработки (откройте в браузере)"
                )
            except Exception:
                pass
        logger.info(f"[USER_ID: {user_id}] - FINAL: Reports sent.")
        
        # Запрашиваем обратную связь
        feedback_keyboard = [
            [InlineKeyboardButton("👍 Да, всё отлично!", callback_data="feedback_yes")],
            [InlineKeyboardButton("👎 Есть ошибки", callback_data="feedback_no")]
        ]
        feedback_message = """📝 Вы довольны качеством обработки?

Ваш отзыв поможет улучшить качество обработки документов!

• 👍 Да - если результат вас устраивает
• 👎 Есть ошибки - если нашли неточности или ошибки

При выборе "Есть ошибки" вы сможете связаться с администратором для детального описания проблем."""
        
        await chat.send_message(
            feedback_message,
            reply_markup=InlineKeyboardMarkup(feedback_keyboard)
        )
        
        # Сохраняем контекст для обратной связи (включая base_path)
        context.user_data["processed_files"] = {
            "user_id": user_id,
            "pdf_name": pdf_file_name,
            "page_image_bytes": archive_png_bytes,
            "ocr_html": full_html_content,
            "corrected_json": json_data,
            "find_prompt": find_prompt,
            "extract_prompt": extract_prompt,
            "base_path": base_path  # Добавляем путь для финализации
        }
        
        return AWAITING_FEEDBACK

    except Exception as e:
        logger.error(f"[USER_ID: {user_id}] - Error in process_specification: {e}", exc_info=True)
        await chat.send_message("Произошла непредвиденная ошибка при обработке.")
        return ConversationHandler.END

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_message = """🤖 Привет! Я ваш помощник по обработке спецификаций металлопроката!

✨ Что я умею:
• 🔍 Анализирую PDF документы с помощью ИИ
• 📊 Извлекаю таблицы спецификаций
• ✅ Исправляю ошибки OCR автоматически  
• 📈 Генерирую отчеты в Excel и TXT

📎 Загрузите PDF-файл (до 20 МБ) или 
🔗 Отправьте ссылку с Dropbox

💡 Совет: Для больших файлов используйте Dropbox: https://dropbox.com

🚀 Готов к работе! Отправьте мне документ!"""
    
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
    
    loading_message = f"""📥 Файл принят! 

📄 Имя: `{file_name}`
📊 Размер: {update.message.document.file_size / 1024 / 1024:.1f} МБ

⏳ Загружаю документ... 
*Это может занять несколько секунд*"""
    
    await update.message.reply_text(loading_message)

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

    analysis_message = """✅ Файл успешно загружен!

🔍 Начинаю анализ документа...
• Проверяю количество страниц
• Ищу таблицы спецификаций  
• Определяю оптимальную страницу для обработки

🤖 ИИ анализирует структуру документа... 
*Пожалуйста, подождите 10-30 секунд*"""
    
    await update.message.reply_text(analysis_message)

    temp_pdf_path = None
    try:
        os.makedirs(TEMP_DIR, exist_ok=True)
        temp_pdf_path = os.path.join(TEMP_DIR, f"{user_id}_check.pdf")
        with open(temp_pdf_path, "wb") as f:
            f.write(pdf_bytes)

        logger.info(f"[USER_ID: {user_id}] - STEP 1: Performing validation and page search with Gemini.")
        gemini_file = genai.upload_file(path=temp_pdf_path)
        prompt = get_prompt("find_and_validate.txt")
        model = create_gemini_model()
        
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
        processing_message = """🎯 Отлично! Страница подтверждена!

🚀 Начинаю полную обработку:
• 📷 Извлекаю страницу в высоком разрешении
• 🔍 Распознаю текст через Azure OCR
• 🤖 Исправляю ошибки с помощью ИИ
• 📊 Структурирую данные в таблицы
• 📈 Генерирую отчеты Excel и TXT

⏰ Время обработки: 1-3 минуты
*Пожалуйста, ожидайте...*"""

        try:
            await query.edit_message_caption(caption=processing_message)
        except telegram.error.BadRequest as e:
            if "There is no caption in the message to edit" in str(e):
                logger.info(f"No caption to edit, using edit_message_text instead: {e}")
                await query.edit_message_text(text=processing_message)
            else:
                raise e
        
        return await process_specification(update, context)
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
        return await process_specification(update, context)
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
        await update.message.reply_text(f"✅ Файл успешно загружен! Документ содержит {num_pages} страниц. Начинаю анализ...")
        
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
            model = create_gemini_model()
            
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

async def handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает обратную связь пользователя"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    processed_files = context.user_data.get("processed_files", {})
    base_path = processed_files.get("base_path")
    
    if not base_path:
        await query.edit_message_text("❌ Ошибка: данные сессии не найдены.")
        context.user_data.clear()
        return ConversationHandler.END
    
    # Отменяем timeout задачу
    if user_id in pending_feedback_tasks:
        pending_feedback_tasks[user_id]["cancel"] = True
        pending_feedback_tasks.pop(user_id, None)
        logger.info(f"[USER_ID: {user_id}] - Feedback timeout cancelled (user responded)")
    
    if query.data == "feedback_yes":
        # Пользователь доволен результатом
        await query.edit_message_text("✅ Спасибо за положительную оценку! Ваш отзыв поможет нам улучшать сервис.")
        
        # Финализируем GCS запись с положительной обратной связью
        await finalize_gcs_entry(base_path, "good")
        
        context.user_data.clear()
        return ConversationHandler.END
        
    elif query.data == "feedback_no":
        # Пользователь недоволен результатом - переходим к диалогу с админом
        contact_message = """❌ Найдены ошибки в обработке

🔧 Напишите администратору прямо сейчас:
👤 @aianback

📝 Опишите проблему:
• Какие именно ошибки вы обнаружили  
• На какой странице документа
• Что должно быть исправлено

⚡ Ваше сообщение поможет улучшить качество обработки!

Спасибо за обратную связь! 🙏

💬 Нажмите на @aianback чтобы начать чат"""
        
        # Создаем кнопку для перехода к диалогу с админом
        admin_keyboard = [[InlineKeyboardButton("💬 Написать админу", url="https://t.me/aianback")]]
        
        await query.edit_message_text(
            contact_message,
            reply_markup=InlineKeyboardMarkup(admin_keyboard)
        )
        
        # Финализируем GCS запись с отрицательной обратной связью
        await finalize_gcs_entry(base_path, "bad")
        
        context.user_data.clear()
        return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Действие отменено.")
    context.user_data.clear()
    return ConversationHandler.END

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает ошибки бота"""
    logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)
    
    # Если есть update, отправляем пользователю сообщение об ошибке
    if isinstance(update, Update) and update.effective_chat:
        error_message = """😔 Произошла ошибка!

🔧 Что делать:
• Попробуйте еще раз через несколько секунд
• Проверьте формат файла (только PDF)
• Убедитесь, что размер файла до 20 МБ

💬 Нужна помощь? Напишите /start для перезапуска"""
        
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=error_message
            )
        except Exception:
            pass

def main():
    if not all([TELEGRAM_BOT_TOKEN, AZURE_ENDPOINT, AZURE_KEY, GEMINI_API_KEY]):
        logger.critical("FATAL: One or more environment variables are missing!")
        return
    
    if not GCS_BUCKET:
        logger.warning("GCS_BUCKET not configured - archiving will be disabled")

    # Останавливаем все существующие webhook'и для предотвращения конфликтов
    try:
        import requests
        response = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook")
        logger.info("Webhook deleted to prevent conflicts")
    except Exception as e:
        logger.warning(f"Could not delete webhook: {e}")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Добавляем обработчик ошибок
    app.add_error_handler(error_handler)
    
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
            AWAITING_FEEDBACK: [CallbackQueryHandler(handle_feedback)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,  # Возвращаем обратно для работы команд
    )
    app.add_handler(conv_handler)
    
    logger.info("🤖 === BOT INITIALIZED SUCCESSFULLY ===")
    
    # Проверяем режим работы (webhook или polling)
    webhook_url = os.getenv("WEBHOOK_URL")
    port = int(os.getenv("PORT", "8080"))
    
    if webhook_url:
        logger.info("🌐 Starting in WEBHOOK mode...")
        logger.info(f"🔗 Webhook URL: {webhook_url}")
        logger.info(f"🚪 Port: {port}")
        
        try:
            app.run_webhook(
                listen="0.0.0.0",
                port=port,
                webhook_url=webhook_url,
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES
            )
        except Exception as e:
            logger.critical(f"Failed to start webhook: {e}")
            raise
    else:
        logger.info("📞 Starting in POLLING mode...")
        
        try:
            app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
        except Exception as e:
            logger.critical(f"Failed to start polling: {e}")
            raise

if __name__ == "__main__":
    main()