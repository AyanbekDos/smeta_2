# Используем официальный Python runtime как базовый образ
FROM python:3.11-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Копируем файл с зависимостями
COPY requirements.txt .

# Устанавливаем Python-зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем все файлы приложения
COPY . .

# Создаем директорию для временных файлов
RUN mkdir -p temp_bot_files

# Запускаем приложение через gunicorn с uvicorn воркерами
# Это стандартный способ для запуска ASGI-приложений в продакшене
CMD ["gunicorn", "--bind", "0.0.0.0:$PORT", "--workers", "1", "--worker-class", "uvicorn.workers.UvicornWorker", "main_bot:flask_app"]