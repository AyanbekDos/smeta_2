# 🚂 Деплой на Railway

## 📋 Подготовка к деплою

### 1. Файлы конфигурации Railway

Созданы следующие файлы:
- `railway.json` - конфигурация деплоя
- `Procfile` - команда запуска
- `runtime.txt` - версия Python
- `requirements.txt` - зависимости

### 2. Поддержка webhook режима

Бот автоматически определяет режим работы:
- **WEBHOOK** режим: если задана переменная `WEBHOOK_URL`
- **POLLING** режим: если `WEBHOOK_URL` не задана

## 🚀 Деплой

### Шаг 1: Подключение репозитория

1. Зайдите на [Railway](https://railway.app)
2. Нажмите "New Project" → "Deploy from GitHub repo"
3. Выберите ваш репозиторий

### Шаг 2: Настройка переменных окружения

Добавьте следующие переменные в Railway Dashboard:

```env
# Telegram Bot
TELEGRAM_BOT_TOKEN=your_bot_token_here

# Gemini API
GEMINI_API_KEY=your_gemini_key_here
GEMINI_MODEL_NAME=gemini-1.5-pro-latest

# Azure Document Intelligence
AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=your_azure_endpoint
AZURE_DOCUMENT_INTELLIGENCE_KEY=your_azure_key

# Google Cloud Storage
GCS_BUCKET=your-gcs-bucket-name

# Railway-specific (автоматически)
PORT=8080
WEBHOOK_URL=https://your-app-name.up.railway.app

# Additional
PROMPT_VERSION=v1.0
```

### Шаг 3: Настройка GCS

1. Создайте Service Account в Google Cloud Console
2. Скачайте JSON ключ
3. Добавьте содержимое JSON ключа как переменную `GOOGLE_APPLICATION_CREDENTIALS_JSON`

Или добавьте в код для Railway:

```python
# В main_bot.py уже добавлена поддержка JSON из переменной окружения
if os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON"):
    # Railway поддержка
    import tempfile
    import json
    credentials = json.loads(os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON"))
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
        json.dump(credentials, f)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = f.name
```

## 🔧 После деплоя

### 1. Настройка webhook Telegram

После успешного деплоя настройте webhook:

```bash
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{"url": "https://your-app-name.up.railway.app"}'
```

### 2. Проверка логов

В Railway Dashboard → Logs можно увидеть:
- Режим работы (webhook/polling)
- Статус подключения к внешним API
- Ошибки обработки

## 📊 Мониторинг

### Полезные логи для отладки:

```
🤖 === BOT INITIALIZED SUCCESSFULLY ===
🌐 Starting in WEBHOOK mode...
🔗 Webhook URL: https://your-app.up.railway.app
✅ Загружен .env.production (webhook режим)
```

### Переменные для отладки:

```env
# Для подробных логов
LOGGING_LEVEL=DEBUG

# Для тестирования локально
BOT_MODE=POLLING  # отключает webhook
```

## 🆘 Решение проблем

### Проблема: Бот не отвечает
- Проверьте webhook: `https://api.telegram.org/bot<TOKEN>/getWebhookInfo`
- Проверьте логи Railway на ошибки

### Проблема: Ошибки GCS
- Проверьте правильность `GOOGLE_APPLICATION_CREDENTIALS_JSON`
- Убедитесь что bucket существует и доступен

### Проблема: Ошибки Gemini
- Проверьте `GEMINI_API_KEY`
- Проверьте лимиты API

## 🚀 Готово!

После настройки всех переменных Railway автоматически:
1. Соберет Docker образ
2. Запустит приложение
3. Предоставит URL для webhook

Ваш бот с новой оптимизированной системой обратной связи готов к работе! 🎉

## ✨ Новые возможности

- **Оптимизированное хранение**: Одна папка вместо трех
- **Умный timeout**: 30 минут на обратную связь
- **Три статуса**: good/bad/timeout
- **Parquet аналитика**: Создается только после получения feedback