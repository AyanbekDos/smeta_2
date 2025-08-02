#!/bin/bash

# Скрипт для деплоя Telegram бота на Google Cloud Run

set -e

# Проверяем наличие необходимых переменных
if [ -z "$PROJECT_ID" ]; then
    echo "Ошибка: установите переменную PROJECT_ID"
    echo "Пример: export PROJECT_ID=your-project-id"
    exit 1
fi

if [ -z "$REGION" ]; then
    echo "Используется регион по умолчанию: us-central1"
    REGION="us-central1"
fi

SERVICE_NAME="telegram-pdf-bot"
IMAGE_NAME="gcr.io/$PROJECT_ID/$SERVICE_NAME"

echo "🚀 Начинаем деплой на Google Cloud Run..."
echo "Проект: $PROJECT_ID"
echo "Регион: $REGION"
echo "Сервис: $SERVICE_NAME"
echo ""

# Включаем необходимые API
echo "📡 Включаем необходимые API..."
gcloud services enable cloudbuild.googleapis.com --project=$PROJECT_ID
gcloud services enable run.googleapis.com --project=$PROJECT_ID

# Собираем Docker образ
echo "🔨 Собираем Docker образ..."
gcloud builds submit --tag $IMAGE_NAME --project=$PROJECT_ID

# Обновляем deploy.yaml с правильным PROJECT_ID
sed "s/PROJECT_ID/$PROJECT_ID/g" deploy.yaml > deploy-configured.yaml

# Деплоим на Cloud Run
echo "🌊 Деплоим на Cloud Run..."
gcloud run services replace deploy-configured.yaml \
    --region=$REGION \
    --project=$PROJECT_ID

# Делаем сервис публично доступным (если нужно)
echo "🔓 Настраиваем доступ к сервису..."
gcloud run services add-iam-policy-binding $SERVICE_NAME \
    --member="allUsers" \
    --role="roles/run.invoker" \
    --region=$REGION \
    --project=$PROJECT_ID

# Получаем URL сервиса
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME \
    --region=$REGION \
    --project=$PROJECT_ID \
    --format="value(status.url)")

echo ""
echo "✅ Деплой завершен!"
echo "🔗 URL сервиса: $SERVICE_URL"
echo ""
echo "📝 Не забудьте:"
echo "1. Создать секреты в Google Secret Manager"
echo "2. Настроить webhook для Telegram бота"
echo "3. Проверить логи: gcloud logs tail --service=$SERVICE_NAME --region=$REGION --project=$PROJECT_ID"

# Очищаем временный файл
rm -f deploy-configured.yaml