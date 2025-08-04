# Используем официальный Python runtime как базовый образ
FROM python:3.11-slim

# Устанавливаем рабочую директорию в контейнере
WORKDIR /app

# Устанавливаем системные зависимости для PyMuPDF и других библиотек
RUN apt-get update && apt-get install -y \
    build-essential \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Копируем файл requirements.txt
COPY requirements.txt .

# Устанавливаем Python зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем все файлы приложения
COPY . .

# Создаем директорию для временных файлов
RUN mkdir -p temp_bot_files

# Указываем переменные окружения
ENV PORT=8080
ENV PYTHONPATH=/app

# Устанавливаем права на выполнение для Python файлов
RUN chmod +x main_bot.py

# Запускаем приложение
CMD ["python", "main_bot.py"]