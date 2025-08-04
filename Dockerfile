# Используем официальный Python runtime как базовый образ
FROM python:3.11-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Устанавливаем системные зависимости, необходимые для библиотек
# libgl1-mesa-glx и libglib2.0-0 часто требуются для GUI-библиотек типа PyMuPDF/Pillow
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Копируем файл с зависимостями
COPY requirements.txt .

# Устанавливаем Python-зависимости
# --no-cache-dir для уменьшения размера слоя
RUN pip install --no-cache-dir -r requirements.txt

# Копируем все файлы приложения в рабочую директорию
COPY . .

# Указываем порт, который будет слушать Cloud Run
ENV PORT=8080
# Устанавливаем флаг для запуска в режиме веб-сервера
ENV CLOUD_RUN=true

# Создаем директорию для временных файлов
RUN mkdir -p temp_bot_files

# Запускаем через gunicorn для продакшна
CMD exec gunicorn --bind 0.0.0.0:$PORT --workers 1 --timeout 3600 main_bot:flask_app
