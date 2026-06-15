FROM python:3.11-alpine

RUN pip install --no-cache-dir requests prometheus-client

RUN addgroup -g 1000 -S xui && \
    adduser -u 1000 -S xui -G xui

WORKDIR /app

COPY xui-exporter.py .

RUN chown -R xui:xui /app

USER xui

CMD ["python", "-u", "xui-exporter.py"]
