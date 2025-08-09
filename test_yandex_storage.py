#!/usr/bin/env python3
"""
Тест Yandex Object Storage
"""
import os
import json
from datetime import datetime

# Загружаем переменные окружения СНАЧАЛА
from dotenv import load_dotenv
if os.path.exists('.env.local'):
    load_dotenv('.env.local', override=True)
else:
    load_dotenv()

# ТЕПЕРЬ импортируем yandex_storage
from yandex_storage import yandex_storage

def test_yandex_storage():
    """Тестируем основные функции Yandex Storage"""
    
    print("=" * 50)
    print("Тест Yandex Object Storage")
    print("=" * 50)
    
    if not yandex_storage.client:
        print("❌ Yandex Storage не настроен!")
        return False
    
    print("✅ Yandex Storage клиент инициализирован")
    
    # Тест 1: Загрузка строки
    print("\n📤 Тест 1: Загрузка строки")
    test_content = "Тест от " + datetime.now().isoformat()
    test_path = f"test/string_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    
    if yandex_storage.upload_string(test_content, test_path, "text/plain"):
        print(f"✅ Строка загружена: {test_path}")
    else:
        print(f"❌ Ошибка загрузки строки: {test_path}")
        return False
    
    # Тест 2: Загрузка JSON
    print("\n📤 Тест 2: Загрузка JSON")
    test_data = {
        "test": True,
        "timestamp": datetime.now().isoformat(),
        "message": "Тест JSON для Yandex Storage"
    }
    json_path = f"test/json_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    if yandex_storage.upload_json(test_data, json_path):
        print(f"✅ JSON загружен: {json_path}")
    else:
        print(f"❌ Ошибка загрузки JSON: {json_path}")
        return False
    
    # Тест 3: Загрузка gzip
    print("\n📤 Тест 3: Загрузка gzipped строки")
    gzip_content = "Это большой текст который будет сжат с помощью gzip" * 100
    gzip_path = f"test/gzip_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt.gz"
    
    if yandex_storage.upload_gzipped_string(gzip_content, gzip_path, "text/plain"):
        print(f"✅ Gzipped строка загружена: {gzip_path}")
    else:
        print(f"❌ Ошибка загрузки gzipped строки: {gzip_path}")
        return False
    
    # Тест 4: Проверка существования файлов
    print("\n🔍 Тест 4: Проверка существования файлов")
    
    if yandex_storage.file_exists(test_path):
        print(f"✅ Файл существует: {test_path}")
    else:
        print(f"❌ Файл не найден: {test_path}")
        return False
    
    if yandex_storage.file_exists(json_path):
        print(f"✅ JSON файл существует: {json_path}")
    else:
        print(f"❌ JSON файл не найден: {json_path}")
        return False
    
    # Тест 5: Список файлов
    print("\n📋 Тест 5: Список файлов")
    files = yandex_storage.list_files("test/")
    print(f"✅ Найдено файлов в test/: {len(files)}")
    for file in files[-3:]:  # Показываем последние 3 файла
        print(f"   - {file}")
    
    # Тест 6: Скачивание файла
    print("\n📥 Тест 6: Скачивание файла")
    temp_download = f"/tmp/downloaded_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    
    if yandex_storage.download_file(test_path, temp_download):
        print(f"✅ Файл скачан: {temp_download}")
        
        # Проверяем содержимое
        with open(temp_download, 'r', encoding='utf-8') as f:
            downloaded_content = f.read()
        
        if downloaded_content == test_content:
            print("✅ Содержимое файла совпадает с оригиналом")
        else:
            print("❌ Содержимое файла не совпадает!")
            print(f"Оригинал: {test_content}")
            print(f"Скачано: {downloaded_content}")
        
        os.remove(temp_download)
    else:
        print(f"❌ Ошибка скачивания файла: {test_path}")
        return False
    
    print("\n" + "=" * 50)
    print("🎉 Все тесты Yandex Storage прошли успешно!")
    print("=" * 50)
    
    return True

if __name__ == "__main__":
    success = test_yandex_storage()
    if not success:
        exit(1)