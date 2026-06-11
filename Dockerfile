FROM python:3.11-alpine

# Устанавливаем зависимости
RUN pip install --no-cache-dir requests prometheus-client

# Создаём пользователя для безопасности
RUN addgroup -g 1000 -S xui && \
    adduser -u 1000 -S xui -G xui

# Создаём рабочую директорию
WORKDIR /app

# Копируем скрипт
COPY xui-exporter.py .

# Меняем владельца
RUN chown -R xui:xui /app

# Переключаемся на xui пользователя
USER xui

# Открываем порт для метрик
EXPOSE 9090

# Запускаем экспортер
CMD ["python", "xui-exporter.py"]
