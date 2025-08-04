# Инструкция по деплою на Google Cloud Run

## Подготовка

1. **Установите Google Cloud CLI**:
   ```bash
   # Для Ubuntu/Debian
   curl https://sdk.cloud.google.com | bash
   exec -l $SHELL
   ```

2. **Авторизуйтесь в Google Cloud**:
   ```bash
   gcloud auth login
   gcloud auth configure-docker
   ```

3. **Создайте проект или выберите существующий**:
   ```bash
   gcloud projects create your-project-id
   gcloud config set project your-project-id
   ```

## Создание GCS Bucket

Создайте bucket для хранения данных:

```bash
# Создаем bucket для архивирования данных
gsutil mb gs://your-dataset-bucket-name

# Устанавливаем правильные права доступа
gsutil iam ch serviceAccount:your-service-account@your-project.iam.gserviceaccount.com:objectCreator gs://your-dataset-bucket-name
```

## Настройка секретов

Создайте секреты в Google Secret Manager:

```bash
# Telegram Bot Token
echo "YOUR_BOT_TOKEN" | gcloud secrets create telegram-bot-token --data-file=-

# Gemini API Key  
echo "YOUR_GEMINI_KEY" | gcloud secrets create gemini-api-key --data-file=-

# Azure Document Intelligence
echo "YOUR_AZURE_ENDPOINT" | gcloud secrets create azure-endpoint --data-file=-
echo "YOUR_AZURE_KEY" | gcloud secrets create azure-key --data-file=-

# GCS Bucket name
echo "your-dataset-bucket" | gcloud secrets create gcs-bucket --data-file=-

# Создаем секрет со всеми ключами
gcloud secrets create telegram-bot-secrets \
    --data-file=<(cat <<EOF
bot-token: $(gcloud secrets versions access latest --secret=telegram-bot-token)
gemini-key: $(gcloud secrets versions access latest --secret=gemini-api-key)
azure-endpoint: $(gcloud secrets versions access latest --secret=azure-endpoint)
azure-key: $(gcloud secrets versions access latest --secret=azure-key)
gcs-bucket: $(gcloud secrets versions access latest --secret=gcs-bucket)
EOF
)
```

## Деплой

1. **Установите переменные окружения**:
   ```bash
   export PROJECT_ID=your-project-id
   export REGION=us-central1  # опционально
   ```

2. **Запустите деплой**:
   ```bash
   ./deploy.sh
   ```

## Настройка Telegram Webhook

После деплоя настройте webhook для бота:

```bash
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{"url": "https://your-service-url/webhook"}'
```

## Мониторинг

Просмотр логов:
```bash
gcloud logs tail --service=telegram-pdf-bot --region=$REGION --project=$PROJECT_ID
```

Просмотр метрик:
```bash
gcloud run services describe telegram-pdf-bot --region=$REGION --project=$PROJECT_ID
```

## Обновление

Для обновления просто повторите деплой:
```bash
./deploy.sh
```

## Удаление

Удаление сервиса:
```bash
gcloud run services delete telegram-pdf-bot --region=$REGION --project=$PROJECT_ID
```