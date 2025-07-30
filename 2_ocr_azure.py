#!/usr/bin/env python3
"""
Скрипт 2: OCR изображения через Azure Document Intelligence

Входные данные:
- PNG изображение таблицы

Выходные данные:
- HTML с разметкой таблицы
"""

import sys
import time
from pathlib import Path
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential

# Добавляем корень проекта в путь для импортов
sys.path.append(str(Path(__file__).parent.parent))

from config.config import (
    AZURE_ENDPOINT, AZURE_KEY, REQUEST_TIMEOUT, 
    MAX_RETRIES, TEMP_DIR, validate_config, ensure_directories
)
from utils.logger import setup_logger, log_step
from utils.file_utils import ensure_file_exists, save_text

logger = setup_logger(__name__)

def retry_with_backoff(func, max_retries: int = MAX_RETRIES):
    """
    Выполняет функцию с повторными попытками и экспоненциальной задержкой
    
    Args:
        func: Функция для выполнения
        max_retries: Максимальное количество попыток
        
    Returns:
        Результат выполнения функции
    """
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            
            wait_time = 2 ** attempt
            logger.warning(f"Попытка {attempt + 1} неудачна: {e}")
            logger.info(f"Повтор через {wait_time} секунд...")
            time.sleep(wait_time)

@log_step("OCR через Azure Document Intelligence", logger)
def image_to_html(image_path: str, output_path: str = None) -> str:
    """
    Выполняет OCR изображения и возвращает HTML с таблицей
    
    Args:
        image_path: Путь к PNG изображению
        output_path: Путь для сохранения HTML (опционально)
        
    Returns:
        Путь к сохраненному HTML файлу
        
    Raises:
        FileNotFoundError: Если изображение не найдено
        Exception: При ошибках Azure API
    """
    image_path = Path(image_path)
    
    # Проверяем существование файла
    if not ensure_file_exists(image_path):
        raise FileNotFoundError(f"Изображение не найдено: {image_path}")
    
    # Проверяем конфигурацию
    validate_config()
    
    # Определяем путь для сохранения
    if output_path is None:
        ensure_directories()
        output_path = TEMP_DIR / f"{image_path.stem}_ocr.html"
    else:
        output_path = Path(output_path)
    
    logger.info(f"OCR изображения: {image_path.name}")
    logger.debug(f"Размер файла: {image_path.stat().st_size / 1024:.1f} KB")
    
    # Создаем клиент Azure
    client = DocumentIntelligenceClient(
        endpoint=AZURE_ENDPOINT,
        credential=AzureKeyCredential(AZURE_KEY)
    )
    
    def perform_ocr():
        # Читаем изображение
        with open(image_path, "rb") as f:
            image_data = f.read()
        
        logger.debug("Отправка запроса в Azure Document Intelligence")
        
        # Запускаем анализ документа с моделью для таблиц
        poller = client.begin_analyze_document(
            model_id="prebuilt-layout",  # Используем layout модель для таблиц
            analyze_request=image_data,
            content_type="application/octet-stream"
        )
        
        logger.info("Ожидание завершения анализа...")
        result = poller.result()
        
        return result
    
    try:
        # Выполняем OCR с повторными попытками
        result = retry_with_backoff(perform_ocr)
        
        # Извлекаем HTML содержимое
        html_content = extract_html_from_result(result)
        
        # Сохраняем результат
        save_text(html_content, output_path)
        
        logger.info(f"HTML сохранен: {len(html_content)} символов")
        logger.debug(f"Сохранено в: {output_path}")
        
        return str(output_path)
        
    except Exception as e:
        logger.error(f"Ошибка OCR: {e}")
        raise

def extract_html_from_result(result) -> str:
    """
    Извлекает HTML разметку из результата Azure Document Intelligence
    
    Args:
        result: Результат анализа документа
        
    Returns:
        HTML строка с таблицами
    """
    html_parts = ['<html><body>']
    
    # Обрабатываем таблицы
    if hasattr(result, 'tables') and result.tables:
        logger.info(f"Найдено таблиц: {len(result.tables)}")
        
        for i, table in enumerate(result.tables):
            html_parts.append(f'<h3>Таблица {i + 1}</h3>')
            html_parts.append('<table border="1" style="border-collapse: collapse;">')
            
            # Создаем сетку таблицы
            max_row = max((cell.row_index for cell in table.cells), default=0)
            max_col = max((cell.column_index for cell in table.cells), default=0)
            
            # Инициализируем сетку
            grid = [['' for _ in range(max_col + 1)] for _ in range(max_row + 1)]
            
            # Заполняем сетку
            for cell in table.cells:
                grid[cell.row_index][cell.column_index] = cell.content or ''
            
            # Генерируем HTML
            for row in grid:
                html_parts.append('<tr>')
                for cell_content in row:
                    html_parts.append(f'<td>{cell_content}</td>')
                html_parts.append('</tr>')
            
            html_parts.append('</table><br>')
    
    # Если таблиц нет, добавляем весь текст
    else:
        logger.warning("Таблицы не найдены, извлекаем весь текст")
        if hasattr(result, 'content'):
            html_parts.append(f'<pre>{result.content}</pre>')
    
    html_parts.append('</body></html>')
    
    html_content = '\n'.join(html_parts)
    logger.debug(f"Сгенерирован HTML размером {len(html_content)} символов")
    
    return html_content

def main():
    """
    Основная функция для запуска скрипта из командной строки
    
    Использование:
        python 2_ocr_azure.py <image_path> [output_path]
    """
    if len(sys.argv) < 2:
        print("Использование: python 2_ocr_azure.py <image_path> [output_path]")
        print("Пример: python 2_ocr_azure.py table.png")
        sys.exit(1)
    
    image_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    try:
        result_path = image_to_html(image_path, output_path)
        print(f"SUCCESS: {result_path}")
        
    except Exception as e:
        logger.error(f"Ошибка выполнения: {e}")
        print(f"ERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()