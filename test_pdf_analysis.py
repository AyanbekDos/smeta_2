#!/usr/bin/env python3
"""
Тест первой части бота - анализ PDF для поиска страницы со спецификацией
Тестируем на файле: /home/imort/smeta_3_codex/test/Ангар 24x40 КМ.pdf
"""

import asyncio
import os
import sys
import time
from datetime import datetime

# Добавляем путь к основному коду
sys.path.insert(0, '/home/imort/smeta_3_codex')

# Импортируем необходимые функции из main_bot.py
from main_bot import (
    create_gemini_model, 
    get_prompt, 
    run_gemini_with_retry, 
    parse_gemini_json,
    USE_VERTEX_AI,
    GEMINI_TIMEOUT_SECONDS
)

from google.generativeai.types import GenerationConfig
import logging

# Настраиваем логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s'
)
logger = logging.getLogger(__name__)

async def test_pdf_analysis():
    """Тестирует первую часть - поиск страницы со спецификацией в PDF"""
    
    pdf_path = "/home/imort/smeta_3_codex/test/Ангар 24x40 КМ.pdf"
    user_id = 999999  # Тестовый user_id
    
    print("🔍 ТЕСТИРОВАНИЕ ПЕРВОЙ ЧАСТИ БОТА - АНАЛИЗ PDF")
    print(f"📄 Файл: {pdf_path}")
    print(f"📏 Размер: {os.path.getsize(pdf_path) / (1024*1024):.1f} MB")
    print(f"⏱️ Таймаут: {GEMINI_TIMEOUT_SECONDS} сек")
    print()
    
    try:
        # Проверяем существование файла
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF файл не найден: {pdf_path}")
        
        # Загружаем промпт
        print("📋 Загружаем промпт...")
        prompt = get_prompt("find_and_validate.txt")
        if not prompt:
            raise ValueError("Не удалось загрузить промпт find_and_validate.txt")
        
        print(f"✅ Промпт загружен, длина: {len(prompt)} символов")
        
        # Создаем модель
        print("🤖 Создаем модель Gemini...")
        model = create_gemini_model()
        print(f"✅ Модель создана: {model.model_name}")
        
        # Подготавливаем файл для анализа
        start_time = time.time()
        
        if USE_VERTEX_AI:
            print("🔄 Используем Vertex AI...")
            from vertexai.generative_models import Part as VPart
            with open(pdf_path, 'rb') as f:
                pdf_data = f.read()
            file_part = VPart.from_data(pdf_data, mime_type="application/pdf")
            
            print("📤 Отправляем запрос к Gemini через Vertex AI...")
            response = await run_gemini_with_retry(
                model,
                prompt,
                file_part,
                user_id,
                generation_config=GenerationConfig(response_mime_type="application/json")
            )
        else:
            print("🔄 Используем прямой Gemini API...")
            import google.generativeai as genai
            gemini_file = genai.upload_file(path=pdf_path)
            print(f"📤 Файл загружен: {gemini_file.name}")
            
            # Ждем активации файла
            print("⏳ Ожидаем активацию файла...")
            await asyncio.sleep(2)
            
            print("📤 Отправляем запрос к Gemini...")
            response = await run_gemini_with_retry(
                model,
                prompt,
                gemini_file,
                user_id,
                generation_config=GenerationConfig(response_mime_type="application/json")
            )
            
            # Удаляем файл
            genai.delete_file(gemini_file.name)
            print("🗑️ Временный файл удален")
        
        # Засекаем время
        analysis_time = time.time() - start_time
        print(f"⏱️ Время анализа: {analysis_time:.2f} секунд")
        
        # Парсим результат
        print("🔍 Парсим результат...")
        result = parse_gemini_json(response, user_id, debug_tag="test_pdf")
        
        # Выводим результат
        print("\n" + "="*50)
        print("📋 РЕЗУЛЬТАТ АНАЛИЗА:")
        print("="*50)
        
        if isinstance(result, dict):
            page_number = result.get("page", 0)
            print(f"📄 Найденная страница: {page_number}")
            
            if page_number > 0:
                print("✅ УСПЕХ! Страница со спецификацией найдена!")
            else:
                print("❌ Страница не найдена (page: 0)")
                
            # Показываем полный результат
            print("\n🔍 Полный ответ от Gemini:")
            for key, value in result.items():
                print(f"  {key}: {value}")
        else:
            print(f"❌ Неожиданный формат результата: {type(result)}")
            print(f"Результат: {result}")
        
        print(f"\n⏱️ Общее время выполнения: {analysis_time:.2f} сек")
        print("🎉 ТЕСТ ЗАВЕРШЕН УСПЕШНО!")
        
        return result
        
    except Exception as e:
        print(f"\n💥 ОШИБКА: {e}")
        import traceback
        print("📋 Полная трассировка:")
        traceback.print_exc()
        return None

if __name__ == "__main__":
    print("🚀 Запуск теста анализа PDF...")
    result = asyncio.run(test_pdf_analysis())
    
    if result:
        print(f"\n✅ Результат теста: {result}")
    else:
        print("\n❌ Тест завершился с ошибкой")