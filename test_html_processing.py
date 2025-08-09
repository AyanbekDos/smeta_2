#!/usr/bin/env python3
"""
Тест второй части бота - обработка HTML для извлечения JSON спецификации
Тестируем на файле: /home/imort/smeta_3_codex/8.html
"""

import asyncio
import os
import sys
import time
import json
from datetime import datetime

# Добавляем путь к основному коду
sys.path.insert(0, '/home/imort/smeta_3_codex')

# Импортируем необходимые функции из main_bot.py
from main_bot import (
    create_gemini_model, 
    get_prompt, 
    run_gemini_with_retry, 
    parse_gemini_json,
    run_gemini_with_fallback,
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

class MockChat:
    """Мок объект для chat.send_message"""
    async def send_message(self, message):
        print(f"📱 [CHAT] {message}")

async def test_html_processing():
    """Тестирует вторую часть - обработка HTML для извлечения JSON спецификации"""
    
    html_path = "/home/imort/smeta_3_codex/8.html"
    user_id = 999999  # Тестовый user_id
    
    print("🔍 ТЕСТИРОВАНИЕ ВТОРОЙ ЧАСТИ БОТА - ОБРАБОТКА HTML")
    print(f"📄 Файл: {html_path}")
    
    # Проверяем размер файла
    if os.path.exists(html_path):
        file_size = os.path.getsize(html_path) / 1024
        print(f"📏 Размер: {file_size:.1f} KB")
    else:
        print(f"❌ Файл не найден: {html_path}")
        return None
    
    print(f"⏱️ Таймаут: {GEMINI_TIMEOUT_SECONDS} сек")
    print()
    
    try:
        # Читаем HTML файл
        print("📖 Читаем HTML файл...")
        with open(html_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        print(f"✅ HTML загружен, длина: {len(html_content)} символов")
        
        # Показываем начало содержимого
        print("📋 Первые 500 символов HTML:")
        print("-" * 50)
        print(html_content[:500])
        if len(html_content) > 500:
            print(f"... (еще {len(html_content) - 500} символов)")
        print("-" * 50)
        print()
        
        # Создаем мок объект для chat
        mock_chat = MockChat()
        
        # Запускаем обработку через основную функцию с fallback
        print("🚀 Запускаем обработку HTML через run_gemini_with_fallback...")
        start_time = time.time()
        
        result = await run_gemini_with_fallback(html_content, user_id, mock_chat)
        
        processing_time = time.time() - start_time
        print(f"⏱️ Время обработки: {processing_time:.2f} секунд")
        
        # Выводим результат
        print("\n" + "="*50)
        print("📋 РЕЗУЛЬТАТ ОБРАБОТКИ HTML:")
        print("="*50)
        
        if isinstance(result, dict):
            print("✅ УСПЕХ! JSON спецификация извлечена!")
            
            # Основные поля
            print(f"\n📊 Основные поля:")
            print(f"  единица_измерения: {result.get('единица_измерения', 'не найдено')}")
            
            # Анализируем профили
            profiles = result.get('профили', {})
            print(f"  профилей найдено: {len(profiles)}")
            
            total_mass = 0
            for profile_name, profile_data in profiles.items():
                print(f"\n🔧 Профиль: {profile_name}")
                
                steel_grades = profile_data.get('марки_стали', {})
                print(f"  марок стали: {len(steel_grades)}")
                
                for grade_name, grade_data in steel_grades.items():
                    sizes = grade_data.get('размеры', {})
                    print(f"    {grade_name}: {len(sizes)} размеров")
                    
                    for size_name, size_data in sizes.items():
                        elements = size_data.get('элементы', [])
                        if elements:
                            for element in elements:
                                mass = element.get('масса', 0)
                                total_mass += mass
            
            print(f"\n📏 Общая масса: {total_mass} т")
            
            # Сохраняем результат в файл
            output_path = "/home/imort/smeta_3_codex/test_html_result.json"
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"💾 Результат сохранен: {output_path}")
            
        else:
            print(f"❌ Неожиданный формат результата: {type(result)}")
            print(f"Результат: {result}")
        
        print(f"\n⏱️ Общее время выполнения: {processing_time:.2f} сек")
        print("🎉 ТЕСТ ЗАВЕРШЕН УСПЕШНО!")
        
        return result
        
    except Exception as e:
        print(f"\n💥 ОШИБКА: {e}")
        import traceback
        print("📋 Полная трассировка:")
        traceback.print_exc()
        return None

if __name__ == "__main__":
    print("🚀 Запуск теста обработки HTML...")
    result = asyncio.run(test_html_processing())
    
    if result:
        print(f"\n✅ Тест завершился успешно")
    else:
        print("\n❌ Тест завершился с ошибкой")