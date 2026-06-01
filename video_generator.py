import os
import io
import time
import base64
import logging
import requests

logger = logging.getLogger(__name__)

RUNWAY_BASE = "https://api.dev.runwayml.com/v1"

def _runway_headers():
    return {
        "Authorization": f"Bearer {os.getenv('RUNWAY_API_KEY', '')}",
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
    except Exception as e:
        logger.warning("Görsel küçültme başarısız (%s), orijinal kullanılıyor", e)
        return photo_bytes


def _submit_and_wait(image_uri: str, prompt: str) -> str:
    """Tek bir Runway task'ı gönderip tamamlanmasını bekler."""
    payload = {
        "model": "gen3a_turbo",
        "promptImage": image_uri,
        "promptText": prompt,
        "duration": 5,
        "ratio": "768:1280",
        "watermark": False
    }

    r = requests.post(f"{RUNWAY_BASE}/image_to_video",
                      headers=_runway_headers(), json=payload, timeout=60)

    if r.status_code != 200:
        error_detail = r.text[:500]
        logger.error("Runway API hata: %d - %s", r.status_code, error_detail)
        raise RuntimeError(f"Runway API {r.status_code}: {error_detail}")

    task_id = r.json()["id"]
    logger.info("Runway task_id: %s", task_id)

    max_wait_sec = 300
    elapsed = 0

    while elapsed < max_wait_sec:
        time.sleep(10)
        elapsed += 10

        status_r = requests.get(f"{RUNWAY_BASE}/tasks/{task_id}",
                                headers=_runway_headers(), timeout=30)
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
            failure = task.get("failure", "")
            failure_code = task.get("failureCode", "")
            logger.error("Runway FAIL — code: %s | msg: %s", failure_code, failure)
            raise RuntimeError(f"Runway başarısız [{failure_code or 'NO_CODE'}]: {failure or 'mesaj yok'}")

    raise TimeoutError(f"Runway {max_wait_sec}s içinde tamamlanamadı")


def generate_drone_video(photo_bytes: bytes, prompt: str = "") -> str:
    """
    Yat fotoğrafından Runway Gen-3 Alpha Turbo ile drone videosu üretir.
    Otomatik retry: INTERNAL hatalarında 3 deneme.
    Döner: video download URL (string)
    """
    prompt = prompt or DEFAULT_PROMPT

    # Fotoğrafı her zaman küçült/sıkıştır (Runway için)
    photo_bytes = _resize_image(photo_bytes)

    b64 = base64.b64encode(photo_bytes).decode("utf-8")
    image_uri = f"data:image/jpeg;base64,{b64}"

    logger.info("Runway Gen-3 görevi başlatılıyor... (image: %d KB)", len(photo_bytes) // 1024)

    last_error = None
    for attempt in range(1, 4):
        try:
            return _submit_and_wait(image_uri, prompt)
        except RuntimeError as e:
            err = str(e)
            last_error = e
            # SAFETY veya ASSET.INVALID gibi kalıcı hatalarda retry yapma
            if "SAFETY" in err or "ASSET.INVALID" in err or "INPUT_PREPROCESSING.SAFETY" in err:
                raise
            logger.warning("Runway deneme %d/3 başarısız: %s — tekrar denenecek", attempt, err)
            if attempt < 3:
                time.sleep(15)

    raise last_error
