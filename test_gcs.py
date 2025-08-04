#!/usr/bin/env python3
"""
Тестовый скрипт для проверки сохранения в Google Cloud Storage
"""

import os
import sys
import asyncio
import json
import gzip
from datetime import datetime, timezone
from PIL import Image, ImageDraw
import io

# Добавляем путь для импорта функций из main_bot.py
sys.path.insert(0, '/home/imort/smeta_2')

# Импортируем необходимые функции
from main_bot import save_to_gcs, clean_filename, format_utc_timestamp

async def test_gcs_functions():
    """Тестируем функции сохранения в GCS"""
    
    print("🧪 Тестируем функции GCS...")
    
    # 1. Тест clean_filename
    print("\n1️⃣ Тестируем clean_filename:")
    test_names = ["Спецификация №123.pdf", "file with spaces.pdf", "test@#$%.pdf"]
    for name in test_names:
        clean = clean_filename(name)
        print(f"   '{name}' → '{clean}'")
    
    # 2. Тест format_utc_timestamp
    print("\n2️⃣ Тестируем format_utc_timestamp:")
    timestamp = format_utc_timestamp()
    print(f"   Timestamp: {timestamp}")
    
    # 3. Создаем тестовые данные
    print("\n3️⃣ Создаем тестовые данные...")
    
    # Создаем тестовое изображение
    test_image = Image.new('RGB', (800, 600), color='white')
    img_buffer = io.BytesIO()
    test_image.save(img_buffer, format='PNG')
    test_image_bytes = img_buffer.getvalue()
    print(f"   Тестовое изображение: {len(test_image_bytes)} bytes")
    
    # Тестовый HTML
    test_html = """
    <table>
        <tr><td>Профиль</td><td>Размер</td><td>Масса</td></tr>
        <tr><td>Двутавр</td><td>20Ш1</td><td>100 кг</td></tr>
    </table>
    """
    print(f"   Тестовый HTML: {len(test_html)} chars")
    
    # Тестовый JSON
    test_json = {
        "единица_измерения": "кг",
        "профили": {
            "Двутавры стальные горячекатанные": {
                "марки_стали": {
                    "Ст3": {
                        "размеры": {
                            "20Ш1": {
                                "элементы": [
                                    {
                                        "тип": "балка",
                                        "позиции": ["Б1", "Б2"],
                                        "масса": 100.5
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        }
    }
    print(f"   Тестовый JSON: {json.dumps(test_json, ensure_ascii=False)[:100]}...")
    
    # 4. Тестируем сохранение в GCS с реальными данными  
    print("\n4️⃣ Тестируем сохранение в GCS с реальными данными...")
    
    # Реальный telegram user_id (пример)
    user_id = 987654321
    pdf_name = "6-8.pdf"  # Реальный файл из архива
    
    # Создаем более реалистичное изображение (имитация PDF страницы)
    real_image = Image.new('RGB', (1200, 800), color='white')
    draw = ImageDraw.Draw(real_image)
    
    # Рисуем имитацию технического чертежа
    draw.rectangle([50, 50, 1150, 750], outline='black', width=2)
    draw.text((100, 100), "КМ - Металлические конструкции", fill='black')
    draw.text((100, 150), "Стадион Строитель, Московская область", fill='black')
    draw.text((100, 200), "Трибуна", fill='black')
    draw.line([100, 300, 1100, 300], fill='black', width=1)
    draw.line([100, 400, 1100, 400], fill='black', width=1)
    
    # Конвертируем в WebP
    webp_buffer = io.BytesIO()
    real_image.save(webp_buffer, format='WEBP', quality=95, lossless=True)
    real_image_bytes = webp_buffer.getvalue()
    print(f"   Реалистичное изображение WebP: {len(real_image_bytes)} bytes")
    
    # Более реалистичный HTML (имитация OCR результата)
    real_html = """
    <div class="page">
        <table class="specification">
            <tr><th>Наименование</th><th>Размеры</th><th>Масса</th><th>Марка стали</th></tr>
            <tr><td>Двутавры стальные горячекатанные</td><td>I20Ш0</td><td>18.64</td><td>C255</td></tr>
            <tr><td>Швеллеры стальные гнутые</td><td>Гн.80x4</td><td>4.51</td><td>C255</td></tr>
            <tr><td>Уголки стальные равнополочные</td><td>L75x5</td><td>0.19</td><td>C255</td></tr>
            <tr><td>Профили стальные прямоугольные</td><td>Гн.60х4</td><td>5.67</td><td>C255</td></tr>
        </table>
        <div class="drawing-info">
            <p>КМ - Стадион Строитель, Московская область, г.о. Клин</p>
            <p>Трибуна - Общие данные</p>
            <p>Стадия: Р, Лист: 1, Листов: 7</p>
        </div>
    </div>
    """
    print(f"   Реалистичный HTML: {len(real_html)} chars")
    
    # Более реалистичный JSON (структура сметы)
    real_json = {
        "единица_измерения": "т",
        "проект": "Стадион Строитель",
        "местоположение": "Московская область, г.о. Клин, г. Клин, ул. Чайковского, д. 34",
        "профили": {
            "Двутавры стальные горячекатанные": {
                "марки_стали": {
                    "C255": {
                        "размеры": {
                            "I20Ш0": {
                                "элементы": [
                                    {
                                        "тип": "балка",
                                        "позиции": ["Б1"],
                                        "масса": 18.64,
                                        "количество": 1
                                    }
                                ]
                            }
                        }
                    }
                }
            },
            "Швеллеры стальные гнутые": {
                "марки_стали": {
                    "C255": {
                        "размеры": {
                            "Гн.80x4": {
                                "элементы": [
                                    {
                                        "тип": "швеллер",
                                        "позиции": ["Св1"],
                                        "масса": 4.51,
                                        "количество": 69
                                    }
                                ]
                            }
                        }
                    }
                }
            },
            "Уголки стальные равнополочные": {
                "марки_стали": {
                    "C255": {
                        "размеры": {
                            "L75x5": {
                                "элементы": [
                                    {
                                        "тип": "уголок",
                                        "позиции": ["д"],
                                        "масса": 0.19,
                                        "количество": 8
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        },
        "общая_масса": 82.6,
        "марки_стали": {
            "C255": 69.23,
            "C355": 13.37
        }
    }
    print(f"   Реалистичный JSON: {json.dumps(real_json, ensure_ascii=False)[:150]}...")
    
    # Тестовые промпты
    test_find_prompt = """
    Ты эксперт по анализу технических чертежей и спецификаций металлоконструкций.
    
    Твоя задача: найти и проанализировать спецификацию металлопроката в предоставленном изображении.
    
    Ищи таблицы со следующими признаками:
    - Заголовки: "Наименование", "Размеры", "Масса", "Марка стали"
    - Данные о металлических профилях: двутавры, швеллеры, уголки, трубы
    - Марки стали: С255, С345, С355 и др.
    
    Если найдешь таблицу - верни JSON с данными.
    Если не найдешь - верни {"error": "Спецификация не найдена"}
    """
    
    test_extract_prompt = """
    Ты эксперт по обработке данных спецификаций металлоконструкций.
    
    Твоя задача: извлечь и структурировать данные из OCR текста.
    
    Создай JSON структуру:
    {
        "единица_измерения": "т",
        "профили": {
            "тип_профиля": {
                "марки_стали": {
                    "марка": {
                        "размеры": {
                            "размер": {
                                "элементы": [{"тип": "", "позиции": [], "масса": 0}]
                            }
                        }
                    }
                }
            }
        }
    }
    """
    
    try:
        result = await save_to_gcs(
            user_id=user_id,
            pdf_name=pdf_name,
            page_image_bytes=real_image_bytes,
            ocr_html=real_html,
            corrected_json=real_json,
            find_prompt=test_find_prompt,
            extract_prompt=test_extract_prompt
        )
        
        if result:
            print("   ✅ Сохранение в GCS прошло успешно!")
            return True
        else:
            print("   ❌ Сохранение в GCS не удалось")
            return False
            
    except Exception as e:
        print(f"   ❌ Ошибка при сохранении: {e}")
        return False

async def check_gcs_bucket():
    """Проверяем содержимое bucket после сохранения"""
    try:
        from google.cloud import storage
        
        client = storage.Client()
        bucket = client.bucket('test-pdf-bot-dataset')
        
        print("\n5️⃣ Проверяем содержимое bucket:")
        
        blobs = list(bucket.list_blobs())
        if not blobs:
            print("   📁 Bucket пуст")
            return
            
        print(f"   📁 Найдено {len(blobs)} файлов:")
        for blob in blobs[:10]:  # показываем первые 10
            print(f"   📄 {blob.name} ({blob.size} bytes)")
            
        # Показываем структуру папок
        folders = set()
        for blob in blobs:
            if '/' in blob.name:
                folder = '/'.join(blob.name.split('/')[:-1])
                folders.add(folder)
        
        print(f"\n   📂 Структура папок:")
        for folder in sorted(folders):
            print(f"   📂 {folder}/")
            
    except Exception as e:
        print(f"   ❌ Ошибка при проверке bucket: {e}")

if __name__ == "__main__":
    print("🚀 Запуск тестов GCS функций...")
    
    # Проверяем переменные окружения
    gcs_bucket = os.getenv("GCS_BUCKET")
    gcs_creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    
    print(f"GCS_BUCKET: {gcs_bucket}")
    print(f"GOOGLE_APPLICATION_CREDENTIALS: {gcs_creds}")
    
    if not gcs_bucket:
        print("❌ GCS_BUCKET не настроен!")
        sys.exit(1)
        
    if not gcs_creds or not os.path.exists(gcs_creds):
        print("❌ GOOGLE_APPLICATION_CREDENTIALS не настроен или файл не существует!")
        sys.exit(1)
    
    # Запускаем тесты
    result = asyncio.run(test_gcs_functions())
    
    if result:
        # Проверяем результат
        asyncio.run(check_gcs_bucket())
        print("\n🎉 Все тесты пройдены успешно!")
    else:
        print("\n💥 Тесты провалились!")
        sys.exit(1)