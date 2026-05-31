import os
import time
import logging
from pathlib import Path

import cloudinary
import cloudinary.uploader
import requests
import google.oauth2.credentials
import googleapiclient.discovery
import googleapiclient.http

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# CDN — Instagram public URL için Cloudinary'e yükle
# ══════════════════════════════════════════════════════════════════

def upload_to_cdn(video_path: str) -> str:
    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    )
    result = cloudinary.uploader.upload_large(
        video_path, resource_type="video", folder="yacht_viral"
    )
    logger.info(f"Cloudinary URL: {result['secure_url']}")
    return result["secure_url"]


# ══════════════════════════════════════════════════════════════════
# TikTok — Content Posting API v2
# ══════════════════════════════════════════════════════════════════

def post_to_tiktok(video_path: str, caption: str) -> dict:
    access_token = os.getenv("TIKTOK_ACCESS_TOKEN")
    video_size   = Path(video_path).stat().st_size

    # 1. Upload başlat
    init_r = requests.post(
        "https://open.tiktokapis.com/v2/post/publish/video/init/",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8"
        },
        json={
            "post_info": {
                "title": caption[:150],
                "privacy_level": "PUBLIC_TO_EVERYONE",
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
                "video_cover_timestamp_ms": 1000
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": video_size,
                "total_chunk_count": 1
            }
        },
        timeout=30
    )
    init_r.raise_for_status()
    data       = init_r.json()["data"]
    upload_url = data["upload_url"]
    publish_id = data["publish_id"]

    # 2. Videoyu yükle
    with open(video_path, "rb") as f:
        video_bytes = f.read()

    up_r = requests.put(
        upload_url,
        headers={
            "Content-Type": "video/mp4",
            "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
            "Content-Length": str(video_size)
        },
        data=video_bytes,
        timeout=120
    )
    up_r.raise_for_status()

    # 3. Yayın durumunu kontrol et
    for _ in range(24):  # maks 2 dakika
        time.sleep(5)
        status_r = requests.post(
            "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            },
            json={"publish_id": publish_id},
            timeout=30
        )
        status = status_r.json().get("data", {}).get("status", "")
        logger.info(f"TikTok status: {status}")
        if status == "PUBLISH_COMPLETE":
            return {"success": True, "url": "https://tiktok.com (yayınlandı)"}
        if status in ("FAILED", "ERROR"):
            return {"success": False, "error": f"TikTok hata: {status}"}

    return {"success": False, "error": "TikTok zaman aşımı"}


# ══════════════════════════════════════════════════════════════════
# Instagram — Graph API Reels
# ══════════════════════════════════════════════════════════════════

def post_to_instagram(video_path: str, caption: str) -> dict:
    access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    ig_user_id   = os.getenv("INSTAGRAM_USER_ID")

    # Instagram public URL gerektiriyor
    cdn_url = upload_to_cdn(video_path)

    # 1. Media container oluştur
    c_r = requests.post(
        f"https://graph.facebook.com/v21.0/{ig_user_id}/media",
        params={
            "media_type": "REELS",
            "video_url": cdn_url,
            "caption": caption,
            "share_to_feed": "true",
            "access_token": access_token
        },
        timeout=30
    )
    c_r.raise_for_status()
    container_id = c_r.json()["id"]

    # 2. İşlenmeyi bekle
    for _ in range(36):  # maks 3 dakika
        time.sleep(5)
        s_r = requests.get(
            f"https://graph.facebook.com/v21.0/{container_id}",
            params={"fields": "status_code", "access_token": access_token},
            timeout=30
        )
        status = s_r.json().get("status_code", "")
        logger.info(f"Instagram status: {status}")
        if status == "FINISHED":
            break
        if status == "ERROR":
            return {"success": False, "error": "Instagram işleme hatası"}

    # 3. Yayınla
    p_r = requests.post(
        f"https://graph.facebook.com/v21.0/{ig_user_id}/media_publish",
        params={"creation_id": container_id, "access_token": access_token},
        timeout=30
    )
    p_r.raise_for_status()
    media_id = p_r.json()["id"]
    return {"success": True, "url": f"https://instagram.com/p/{media_id}"}


# ══════════════════════════════════════════════════════════════════
# YouTube — Data API v3 Shorts
# ══════════════════════════════════════════════════════════════════

def post_to_youtube(video_path: str, caption: str) -> dict:
    creds = google.oauth2.credentials.Credentials(
        token=os.getenv("YOUTUBE_ACCESS_TOKEN"),
        refresh_token=os.getenv("YOUTUBE_REFRESH_TOKEN"),
        client_id=os.getenv("YOUTUBE_CLIENT_ID"),
        client_secret=os.getenv("YOUTUBE_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token"
    )
    yt = googleapiclient.discovery.build("youtube", "v3", credentials=creds)

    # Başlık: caption'ın ilk satırı (max 100 karakter) + #Shorts
    first_line = caption.split("\n")[0][:90]
    title = first_line if "#Shorts" in first_line else first_line + " #Shorts"

    request = yt.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": caption,
                "tags": ["yat", "yacht", "lüks yat", "satılık yat", "Shorts"],
                "categoryId": "19"  # Travel & Events
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False
            }
        },
        media_body=googleapiclient.http.MediaFileUpload(
            video_path, mimetype="video/mp4", resumable=True
        )
    )

    response = None
    while response is None:
        _, response = request.next_chunk()

    video_id = response["id"]
    return {"success": True, "url": f"https://youtube.com/shorts/{video_id}"}


# ══════════════════════════════════════════════════════════════════
# Ana fonksiyon
# ══════════════════════════════════════════════════════════════════

def publish_to_all_platforms(video_path: str, captions: dict) -> dict:
    results = {}

    for platform, fn, key in [
        ("TikTok",    post_to_tiktok,    "tiktok"),
        ("Instagram", post_to_instagram, "instagram"),
        ("YouTube",   post_to_youtube,   "youtube"),
    ]:
        try:
            logger.info(f"{platform}'a yükleniyor...")
            results[platform] = fn(video_path, captions.get(key, ""))
        except Exception as e:
            logger.error(f"{platform} hatası: {e}", exc_info=True)
            results[platform] = {"success": False, "error": str(e)[:200]}

    return results
