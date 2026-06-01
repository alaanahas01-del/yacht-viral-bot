"""
AI drone/hero video üretimi — fal.ai üzerinden Kling 2.5 Turbo Pro (image-to-video).
FAL_KEY yoksa veya hata olursa "" döner → video_editor Ken Burns hero ile devam eder.
"""
import os
import io
import time
import base64
import logging

import requests

logger = logging.getLogger(__name__)

FAL_MODEL = "fal-ai/kling-video/v2.5-turbo/pro/image-to-video"
FAL_QUEUE = "https://queue.fal.run"

DEFAULT_PROMPT = (
    "Cinematic aerial drone shot of a luxury motor yacht on calm blue Mediterranean "
    "sea, smooth slow orbit, gentle water reflections, soft golden hour light, "
    "subtle moving clouds, photorealistic, ultra sharp, no warping, no distortion"
)


def _resize_for_upload(photo_bytes: bytes, max_dim: int = 1280) -> bytes:
    try:
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(photo_bytes))
        img = ImageOps.exif_transpose(img).convert("RGB")
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception as e:
        logger.warning("Upload resize hata (%s)", e)
        return photo_bytes


def generate_drone_video(photo_bytes: bytes, prompt: str = "", duration: int = 5) -> str:
    """
    fal.ai Kling ile fotoğraftan sinematik video üretir. Video URL döner.
    FAL_KEY yoksa "" döner (drone'suz devam).
    """
    fal_key = os.getenv("FAL_KEY", "").strip()
    if not fal_key:
        logger.info("FAL_KEY yok — drone adımı atlanıyor (Ken Burns hero kullanılacak)")
        return ""

    prompt = prompt or DEFAULT_PROMPT
    img = _resize_for_upload(photo_bytes)
    image_uri = "data:image/jpeg;base64," + base64.b64encode(img).decode()

    headers = {"Authorization": f"Key {fal_key}", "Content-Type": "application/json"}
    payload = {
        "prompt": prompt,
        "image_url": image_uri,
        "duration": str(duration),
        "negative_prompt": "blur, distort, warp, low quality, cartoon, watermark",
    }

    try:
        r = requests.post(f"{FAL_QUEUE}/{FAL_MODEL}", headers=headers, json=payload, timeout=60)
        if r.status_code not in (200, 201):
            logger.error("fal submit hata %d: %s", r.status_code, r.text[:400])
            return ""
        data = r.json()
        status_url = data.get("status_url")
        response_url = data.get("response_url")
        req_id = data.get("request_id")
        logger.info("fal Kling görev kuyrukta: %s", req_id)

        # Poll (max ~5 dk)
        deadline = time.time() + 300
        while time.time() < deadline:
            time.sleep(8)
            s = requests.get(status_url, headers=headers, timeout=30)
            st = s.json().get("status")
            logger.info("fal durum: %s", st)
            if st == "COMPLETED":
                break
            if st in ("FAILED", "ERROR"):
                logger.error("fal başarısız: %s", s.text[:300])
                return ""
        else:
            logger.warning("fal zaman aşımı")
            return ""

        res = requests.get(response_url, headers=headers, timeout=60).json()
        # Kling çıktısı: {"video": {"url": "..."}}
        video = res.get("video") or {}
        url = video.get("url") or (res.get("video_url"))
        if not url:
            logger.error("fal çıktısında video yok: %s", str(res)[:300])
            return ""
        logger.info("fal Kling video hazır")
        return url
    except Exception as e:
        logger.warning("fal Kling exception: %s", e)
        return ""
