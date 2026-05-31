import os
import time
import base64
import logging
import requests

logger = logging.getLogger(__name__)

RUNWAY_API_KEY  = os.getenv("RUNWAY_API_KEY")
RUNWAY_BASE     = "https://api.dev.runwayml.com/v1"
RUNWAY_HEADERS  = {
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

def generate_drone_video(photo_bytes: bytes, prompt: str = "") -> str:
    """
    Yat fotoğrafından Runway Gen-3 Alpha ile 10 saniyelik drone videosu üretir.
    Döner: video download URL (string)
    """
    prompt = prompt or DEFAULT_PROMPT

    # Fotoğrafı base64 data URI'ye çevir
    b64 = base64.b64encode(photo_bytes).decode("utf-8")
    image_uri = f"data:image/jpeg;base64,{b64}"

    logger.info("Runway Gen-3 görevi başlatılıyor...")
    payload = {
        "model": "gen3a_turbo",
        "promptImage": image_uri,
        "promptText": prompt,
        "duration": 10,          # 10 saniye (Runway: 5 veya 10)
        "ratio": "768:1280",     # 9:16 — TikTok/Reels/Shorts formatı
        "watermark": False
    }

    r = requests.post(f"{RUNWAY_BASE}/image_to_video",
                      headers=RUNWAY_HEADERS, json=payload, timeout=30)
    r.raise_for_status()
    task_id = r.json()["id"]
    logger.info(f"Runway task_id: {task_id}")

    # Tamamlanana kadar bekle (maks 5 dakika)
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
        logger.info(f"Runway durum ({elapsed}s): {status}")

        if status == "SUCCEEDED":
            output = task.get("output", [])
            if not output:
                raise ValueError("Runway çıktısı boş")
            logger.info("Drone videosu hazır!")
            return output[0]  # video URL

        if status in ("FAILED", "CANCELLED"):
            reason = task.get("failure") or task.get("failureCode", "bilinmiyor")
            raise RuntimeError(f"Runway başarısız: {reason}")

    raise TimeoutError(f"Runway {max_wait_sec}s içinde tamamlanamadı")
