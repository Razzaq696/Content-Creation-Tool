FROM python:3.13-slim

RUN apt-get update && apt-get install -y ffmpeg

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD gunicorn app:app --workers 2 --timeout 300 --bind 0.0.0.0:$PORT
