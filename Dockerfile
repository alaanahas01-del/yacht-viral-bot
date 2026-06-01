FROM python:3.11-slim

# FFmpeg + fontlar kur (video montajı ve drawtext için şart)
# fonts-dejavu-core: Türkçe karakterleri (ç ş ğ ı İ ö ü) destekler
RUN apt-get update && apt-get install -y ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p output_videos

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
