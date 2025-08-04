# Деплой Telegram бота на Google Cloud Run 🚀

Пошаговая инструкция "для чайника" по деплою бота с архивированием в GCS.

## 📋 Что нужно подготовить

### 1. Аккаунт Google Cloud
- Зарегистрируйтесь на [Google Cloud Console](https://console.cloud.google.com/)
- Создайте новый проект или используйте существующий

### 2. Установка Google Cloud CLI
```bash
# Скачайте и установите gcloud CLI
curl https://sdk.cloud.google.com | bash
exec -l $SHELL
gcloud init
```

### 3. Аутентификация
```bash
# Войдите в ваш аккаунт Google
gcloud auth login

# Установите проект (замените YOUR_PROJECT_ID на реальный ID проекта)
gcloud config set project YOUR_PROJECT_ID

# Включите необходимые API
gcloud services enable run.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable storage.googleapis.com
```

## 🏗️ Подготовка к деплою

### 1. Создание Docker образа

Файл `Dockerfile` уже готов в проекте. Проверьте его содержимое:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    && rm -rf /var/lib/apt/lists/*

# Копируем файлы
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Указываем порт
ENV PORT=8080
EXPOSE 8080

# Запускаем бота
CMD ["python", "main_bot.py"]
```

### 2. Настройка переменных окружения

Создайте файл `.env.production` на основе `.env`:

```bash
cp .env .env.production
```

**ВАЖНО:** Удалите из `.env.production` строку с `GOOGLE_APPLICATION_CREDENTIALS`, так как на Cloud Run будем использовать IAM роли.

### 3. Создание Google Cloud Storage bucket

```bash
# Создайте уникальное имя для bucket (замените YOUR_UNIQUE_BUCKET_NAME)
export BUCKET_NAME="pdf-bot-dataset-$(date +%s)"

# Создайте bucket
gsutil mb gs://$BUCKET_NAME

# Дайте доступ сервисному аккаунту Cloud Run (будет создан автоматически)
# Пока пропускаем, настроим после деплоя
```

## 🚀 Деплой на Cloud Run

### 1. Деплой с помощью gcloud

```bash
# Перейдите в папку проекта
cd /home/imort/smeta_2

# Деплой на Cloud Run (замените значения на свои)
gcloud run deploy telegram-pdf-bot \
  --source . \
  --platform managed \
  --region europe-west1 \
  --allow-unauthenticated \
  --set-env-vars "TELEGRAM_BOT_TOKEN=ВАШ_ТОКЕН_БОТА" \
  --set-env-vars "GEMINI_API_KEY=ВАШ_КЛЮЧ_GEMINI" \
  --set-env-vars "GEMINI_MODEL_NAME=gemini-2.5-pro" \
  --set-env-vars "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=ВАШ_ENDPOINT" \
  --set-env-vars "AZURE_DOCUMENT_INTELLIGENCE_KEY=ВАШ_КЛЮЧ_AZURE" \
  --set-env-vars "GCS_BUCKET=$BUCKET_NAME" \
  --set-env-vars "PROMPT_VERSION=v1.0" \
  --memory 2Gi \
  --cpu 1 \
  --timeout 3600 \
  --concurrency 10 \
  --max-instances 5
```

### 2. Настройка прав доступа к GCS

После деплоя нужно дать права сервисному аккаунту:

```bash
# Получите email сервисного аккаунта Cloud Run
SERVICE_ACCOUNT=$(gcloud run services describe telegram-pdf-bot \
  --region=europe-west1 \
  --format="value(spec.template.spec.serviceAccountName)")

echo "Service Account: $SERVICE_ACCOUNT"

# Дайте права на работу с GCS bucket
gsutil iam ch serviceAccount:$SERVICE_ACCOUNT:objectAdmin gs://$BUCKET_NAME
gsutil iam ch serviceAccount:$SERVICE_ACCOUNT:legacyBucketReader gs://$BUCKET_NAME
```

### 3. Настройка webhook для Telegram

```bash
# Получите URL вашего сервиса
SERVICE_URL=$(gcloud run services describe telegram-pdf-bot \
  --region=europe-west1 \
  --format="value(status.url)")

echo "Service URL: $SERVICE_URL"

# Установите webhook для Telegram бота (замените TOKEN на ваш токен)
curl -X POST "https://api.telegram.org/botВАШ_ТОКЕН/setWebhook" \
  -H "Content-Type: application/json" \
  -d "{\"url\": \"$SERVICE_URL/telegram-webhook\"}"
```

## 🔧 Важные настройки

### 1. Логирование

Логи можно посмотреть командой:
```bash
gcloud logs tail "projects/YOUR_PROJECT_ID/logs/run.googleapis.com%2Fstdout" \
  --filter="resource.labels.service_name=telegram-pdf-bot"
```

### 2. Мониторинг

В Google Cloud Console перейдите в:
- Cloud Run → telegram-pdf-bot → Metrics
- Там увидите статистику запросов, ошибок, использования памяти

### 3. Масштабирование

Текущие настройки:
- Максимум 5 экземпляров
- 1 CPU, 2GB RAM на экземпляр
- Таймаут 60 минут (для долгих PDF)
- До 10 одновременных запросов на экземпляр

## 🔒 Безопасность

### 1. Секреты

Вместо переменных окружения лучше использовать Secret Manager:

```bash
# Создайте секреты
echo "ВАШ_ТОКЕН_БОТА" | gcloud secrets create telegram-bot-token --data-file=-
echo "ВАШ_КЛЮЧ_GEMINI" | gcloud secrets create gemini-api-key --data-file=-
echo "ВАШ_КЛЮЧ_AZURE" | gcloud secrets create azure-doc-key --data-file=-

# Дайте права сервисному аккаунту
gcloud secrets add-iam-policy-binding telegram-bot-token \
  --member="serviceAccount:$SERVICE_ACCOUNT" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding gemini-api-key \
  --member="serviceAccount:$SERVICE_ACCOUNT" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding azure-doc-key \
  --member="serviceAccount:$SERVICE_ACCOUNT" \
  --role="roles/secretmanager.secretAccessor"
```

### 2. Обновление с секретами

```bash
gcloud run services update telegram-pdf-bot \
  --region=europe-west1 \
  --set-secrets="TELEGRAM_BOT_TOKEN=telegram-bot-token:latest" \
  --set-secrets="GEMINI_API_KEY=gemini-api-key:latest" \
  --set-secrets="AZURE_DOCUMENT_INTELLIGENCE_KEY=azure-doc-key:latest"
```

## 🔄 Обновление бота

Для обновления кода:

```bash
# Зайдите в папку проекта
cd /home/imort/smeta_2

# Сделайте git pull если код в репозитории
git pull

# Задеплойте новую версию
gcloud run deploy telegram-pdf-bot \
  --source . \
  --region=europe-west1
```

## 🧪 Тестирование

После деплоя:

1. Откройте Telegram
2. Найдите вашего бота
3. Отправьте команду `/start`
4. Отправьте PDF файл для обработки
5. Проверьте в Google Cloud Storage bucket появление файлов

## ❗ Возможные проблемы

### 1. Ошибка прав доступа к GCS
```bash
# Проверьте права
gsutil iam get gs://$BUCKET_NAME

# Добавьте недостающие права
gsutil iam ch serviceAccount:$SERVICE_ACCOUNT:objectAdmin gs://$BUCKET_NAME
```

### 2. Превышение лимитов памяти
```bash
# Увеличьте память до 4GB
gcloud run services update telegram-pdf-bot \
  --region=europe-west1 \
  --memory=4Gi
```

### 3. Таймауты обработки
```bash
# Увеличьте таймаут
gcloud run services update telegram-pdf-bot \
  --region=europe-west1 \
  --timeout=3600
```

### 4. Проблемы с webhook
```bash
# Проверьте статус webhook
curl "https://api.telegram.org/botВАШ_ТОКЕН/getWebhookInfo"

# Удалите webhook если нужно
curl -X POST "https://api.telegram.org/botВАШ_ТОКЕН/deleteWebhook"
```

## 💰 Оценка стоимости

При обработке 100 PDF в день:
- Cloud Run: ~$5-10/месяц
- Cloud Storage: ~$1-3/месяц  
- Исходящий трафик: ~$1-5/месяц

**Итого: ~$7-18/месяц**

## 📞 Полезные команды

```bash
# Посмотреть все сервисы
gcloud run services list

# Посмотреть логи в реальном времени
gcloud logs tail "projects/YOUR_PROJECT_ID/logs/run.googleapis.com%2Fstdout"

# Получить URL сервиса
gcloud run services describe telegram-pdf-bot --region=europe-west1 --format="value(status.url)"

# Удалить сервис
gcloud run services delete telegram-pdf-bot --region=europe-west1

# Посмотреть использование GCS
gsutil du -sh gs://$BUCKET_NAME
```

## 🎯 Следующие шаги

1. Настройте мониторинг и алерты
2. Добавьте CI/CD pipeline через GitHub Actions
3. Настройте бэкапы GCS bucket
4. Добавьте дашборд для аналитики обработанных файлов

---

**Готово!** Ваш бот работает в облаке и архивирует все данные для машинного обучения! 🎉