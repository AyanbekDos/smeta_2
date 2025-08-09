#!/usr/bin/env python3
"""
Простой тест подключения к Yandex Object Storage
"""
import os
import boto3
from dotenv import load_dotenv

# Загружаем переменные окружения
if os.path.exists('.env.local'):
    load_dotenv('.env.local', override=True)
else:
    load_dotenv()

def test_connection():
    """Тестируем подключение к Yandex Object Storage"""
    
    access_key = os.getenv("YANDEX_ACCESS_KEY")
    secret_key = os.getenv("YANDEX_SECRET_KEY")  
    bucket_name = os.getenv("YANDEX_BUCKET")
    
    print("Проверяем переменные окружения:")
    print(f"YANDEX_ACCESS_KEY: {access_key[:10] + '...' if access_key else None}")
    print(f"YANDEX_SECRET_KEY: {secret_key[:10] + '...' if secret_key else None}")
    print(f"YANDEX_BUCKET: {bucket_name}")
    
    if not all([access_key, secret_key, bucket_name]):
        print("❌ Не все переменные окружения настроены")
        return False
    
    try:
        # Создаем клиент
        client = boto3.client(
            's3',
            endpoint_url='https://storage.yandexcloud.net',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key
        )
        
        print("\n✅ Клиент создан")
        
        # Проверяем список бакетов
        print("🔍 Проверяем доступ к бакетам...")
        response = client.list_buckets()
        print(f"✅ Найдено бакетов: {len(response.get('Buckets', []))}")
        
        for bucket in response.get('Buckets', []):
            print(f"  - {bucket['Name']}")
        
        # Проверяем конкретный бакет
        print(f"\n🔍 Проверяем бакет {bucket_name}...")
        try:
            response = client.head_bucket(Bucket=bucket_name)
            print(f"✅ Бакет {bucket_name} доступен")
        except Exception as e:
            print(f"❌ Ошибка доступа к бакету: {e}")
            return False
        
        # Пробуем создать простой файл
        print("\n📤 Пробуем создать тестовый файл...")
        test_content = "test content"
        test_key = "test/connection_test.txt"
        
        try:
            client.put_object(
                Bucket=bucket_name,
                Key=test_key,
                Body=test_content.encode('utf-8'),
                ContentType='text/plain'
            )
            print(f"✅ Файл создан: {test_key}")
            
            # Проверяем, что файл создался
            try:
                obj = client.get_object(Bucket=bucket_name, Key=test_key)
                downloaded_content = obj['Body'].read().decode('utf-8')
                if downloaded_content == test_content:
                    print("✅ Содержимое файла корректно")
                else:
                    print("❌ Содержимое файла не совпадает")
            except Exception as e:
                print(f"❌ Ошибка чтения файла: {e}")
            
            # Удаляем тестовый файл
            client.delete_object(Bucket=bucket_name, Key=test_key)
            print("🗑️ Тестовый файл удален")
            
        except Exception as e:
            print(f"❌ Ошибка создания файла: {e}")
            return False
        
        print("\n🎉 Все тесты подключения прошли успешно!")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка подключения: {e}")
        return False

if __name__ == "__main__":
    test_connection()