FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN DJANGO_DEBUG=true DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" python manage.py collectstatic --noinput
EXPOSE 8000
CMD ["gunicorn", "bookmyseat.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
