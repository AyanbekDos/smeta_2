# Используем официальный Python runtime как базовый образ
FROM python:3.11-slim

# Устанавливаем рабочую директорию в контейнере
WORKDIR /app

# Устанавливаем системные зависимости для PyMuPDF
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Копируем файл requirements.txt
COPY requirements.txt .

# Устанавливаем Python зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем остальные файлы приложения
COPY main_bot.py .
COPY extract_and_correct.txt .
COPY find_and_validate.txt .

# Создаем директорию для временных файлов
RUN mkdir -p temp_bot_files

# Указываем переменную окружения для порта
ENV PORT=8080

# Запускаем приложение
CMD ["python", "main_bot.py"]