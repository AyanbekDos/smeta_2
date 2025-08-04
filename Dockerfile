# Многоэтапная сборка для оптимизации
FROM python:3.11-slim as builder

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    build-essential \
    libgl1-mesa-glx \
    libglib2.0-0 \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Копируем requirements и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Финальный образ
FROM python:3.11-slim

# Устанавливаем только рантайм зависимости
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Копируем установленные Python пакеты из builder
COPY --from=builder /root/.local /root/.local

# Рабочая директория
WORKDIR /app

# Копируем только нужные файлы приложения
COPY main_bot.py .
COPY extract_and_correct.txt .
COPY find_and_validate.txt .

# Создаем директории
RUN mkdir -p temp_bot_files

# Переменные окружения
ENV PORT=8080
ENV PYTHONPATH=/app
ENV PATH=/root/.local/bin:$PATH
ENV CLOUD_RUN=true

# Запуск
CMD ["python", "main_bot.py"]