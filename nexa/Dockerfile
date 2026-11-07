FROM python:3.12-slim
WORKDIR /app
ENV DJANGO_SETTINGS_MODULE=nexa.settings PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY nexa ./nexa
COPY start.sh .
RUN groupadd --gid 10001 nexa && useradd --uid 10001 --gid 10001 nexa \
    && install -d -o nexa -g nexa /data /app/static
USER nexa
CMD ["sh", "start.sh"]
