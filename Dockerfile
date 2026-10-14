FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PRODUCTION=1 TRUST_PROXY=1 DATABASE=/dados/copa.db UPLOADS_DIR=/dados/uploads INSTANCE_DIR=/dados PORT=8000
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /dados
EXPOSE 8000
CMD ["gunicorn", "app:app", "-c", "gunicorn.conf.py"]
