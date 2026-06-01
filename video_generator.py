import os
import io
import time
import base64
import logging
import requests

logger = logging.getLogger(__name__)

RUNWAY_API_KEY = os.getenv("RUNWAY_API_KEY")
RUNWAY_BASE    = "https://api.dev.runwayml.com/v1"
RUNWAY_HEADERS = {
    "Authorization": f"Bearer {RUNWAY_API_KEY}",
    "Content-Type": "application/json",
    "X-Runway-Version": "2024-11-06"
}

DEFAULT_PROMPT = (
    "Cinematic luxury yacht drone footage, smooth aerial 360 orbit shot, "
    "crystal blue Mediterranean water, golden hour lighting, "
    "4K ultra sharp, photorealistic, DJI Mavic Pro style, "
    "no people, calm sea"
)

MAX_IMAGE_SIZE = 4 * 1024 * 1024  # 4MB


def _resize_image(photo_bytes: bytes) -> bytes:
    """Fotoğrafı JPEG olarak sıkıştır, max 4MB altına düşür."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(photo_bytes))
        img = img.convert("RGB")

        # Boyutu küçült
        max_dim = 1920
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)

        quality = 85
        while quality >= 30:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            data = buf.getvalue()
            if len(data) <= MAX_IMAGE_SIZE:
                return data
            quality -= 10

        return data
    except ImportError:
        logger.warning("Pillow yok, orijinal fotoğraf kullanılıyor")
        return photo_bytes


def generate_drone_video(photo_bytes: bytes, prompt: str = "") -> str:
    """
    Yat fotoğrafından Runway Gen-3 Alpha ile 10 saniyelik drone videosu üretir.
    Döner: video download URL (string)
    """
    prompt = prompt or DEFAULT_PROMPT

    # Fotoğrafı küçült
    if len(photo_bytes) > MAX_IMAGE_SIZE:
        photo_bytes = _resize_image(photo_bytes)

    b64 = base64.b64encode(photo_bytes).decode("utf-8")
    image_uri = f"data:image/jpeg;base64,{b64}"

    logger.info("Runway Gen-3 görevi başlatılıyor... (image: %d KB)", len(photo_bytes) // 1024)
    payload = {
        "model": "gen3a_turbo",
        "promptImage": image_uri,
        "promptText": prompt,
        "duration": 5,
        "ratio": "768:1280",
        "watermark": False
    }

    r = requests.post(f"{RUNWAY_BASE}/image_to_video",
                      headers=RUNWAY_HEADERS, json=payload, timeout=60)

    if r.status_code != 200:
        error_detail = r.text[:500]
        logger.error("Runway hata: %d - %s", r.status_code, error_detail)
        raise RuntimeError(f"Runway API {r.status_code}: {error_detail}")

    task_id = r.json()["id"]
    logger.info("Runway task_id: %s", task_id)

    max_wait_sec = 300
    elapsed = 0

    while elapsed < max_wait_sec:
        time.sleep(10)
        elapsed += 10

        status_r = requests.get(f"{RUNWAY_BASE}/tasks/{task_id}",
                                headers=RUNWAY_HEADERS, timeout=30)
        status_r.raise_for_status()
        task = status_r.json()
        status = task.get("status")
        logger.info("Runway durum (%ds): %s", elapsed, status)

        if status == "SUCCEEDED":
            output = task.get("output", [])
            if not output:
                raise ValueError("Runway çıktısı boş")
            logger.info("Drone videosu hazır!")
            return output[0]

        if status in ("FAILED", "CANCELLED"):
            reason = task.get("failure") or task.get("failureCode", "bilinmiyor")
            raise RuntimeError(f"Runway başarısız: {reason}")

    raise TimeoutError(f"Runway {max_wait_sec}s içinde tamamlanamadı")
