FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["sh", "-c", "flask --app app db upgrade && gunicorn app:app --bind 0.0.0.0:${PORT:-8000} --workers 2 --threads 4"]
