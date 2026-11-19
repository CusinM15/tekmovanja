# Tekmovanja — šolsko atletsko tekmovanje (FastAPI + SQLite)
FROM python:3.12-slim

WORKDIR /app

# Najprej requirements (dokler se ne spremenijo, se layer kešira)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Koda
COPY run.py .
COPY app/ app/
COPY static/ static/

# Podatki (SQLite) — volumen, da ostanejo po restartu containerja
VOLUME ["/app/data"]
ENV TEKMOVANJA_DATA_DIR=/app/data

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
