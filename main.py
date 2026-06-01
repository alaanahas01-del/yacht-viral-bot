import os
import asyncio
import logging
from fastapi import FastAPI, Request, HTTPException
import httpx
from dotenv import load_dotenv
from pipeline import process_yacht_submission

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "yacht2025")
YOUR_CHAT_ID   = os.getenv("YOUR_CHAT_ID")
TELEGRAM_API   = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = FastAPI(title="Yacht Viral Agent")

# media_group_id -> {"caption": str, "file_ids": [str], "chat_id": int, "task": asyncio.Task}
media_groups: dict = {}

# ── helpers ────────────────────────────────────────────────────────

async def send_message(chat_id: int, text: str):
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id, "text": text, "parse_mode": "HTML"
        })

async def send_video(chat_id: int, video_path: str, caption: str = ""):
    async with httpx.AsyncClient(timeout=120) as client:
        with open(video_path, "rb") as f:
            await client.post(
                f"{TELEGRAM_API}/sendVideo",
                data={"chat_id": chat_id, "caption": caption, "supports_streaming": "true"},
                files={"video": ("video.mp4", f, "video/mp4")}
            )

async def download_photo(file_id: str) -> bytes:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id})
        file_path = r.json()["result"]["file_path"]
        dl = await client.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}")
        return dl.content

async def process_media_group(group_id: str):
    """2 saniye bekle (tüm fotoğraflar gelsin), sonra pipeline'ı başlat."""
    await asyncio.sleep(2)

    group = media_groups.pop(group_id, None)
    if not group:
        return

    chat_id = group["chat_id"]
    caption = group["caption"]
    file_ids = group["file_ids"]

    if not caption:
        await send_message(chat_id, "📝 Fotoğraflar geldi ama bilgiler eksik. Caption'a yat bilgilerini yaz.")
        return

    await send_message(chat_id, f"✅ {len(file_ids)} fotoğraf alındı! Pipeline başlatılıyor...")

    # Tüm fotoğrafları indir
    photos_bytes = []
    for fid in file_ids:
        try:
            photos_bytes.append(await download_photo(fid))
        except Exception as e:
            logger.warning(f"Fotoğraf indirilemedi {fid}: {e}")

    if not photos_bytes:
        await send_message(chat_id, "❌ Fotoğraflar indirilemedi.")
        return

    asyncio.create_task(
        process_yacht_submission(chat_id, caption, photos_bytes, send_message, send_video)
    )

# ── webhook ────────────────────────────────────────────────────────

@app.post(f"/webhook/{WEBHOOK_SECRET}")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad JSON")

    message = data.get("message", {})
    if not message:
        return {"ok": True}

    chat_id = message["chat"]["id"]

    if str(chat_id) != str(YOUR_CHAT_ID):
        await send_message(chat_id, "❌ Yetkisiz erişim.")
        return {"ok": True}

    text           = message.get("text", "")
    caption        = message.get("caption", "")
    photos         = message.get("photo", [])
    media_group_id = message.get("media_group_id")

    if text.startswith("/start"):
        await send_message(chat_id,
            "🚢 <b>Yat Viral Ajan</b>\n\n"
            "Yat fotoğraflarını (max 10) şu formatı caption olarak gönder:\n\n"
            "<code>Model: Azimut 55\n"
            "Konum: Bodrum, Türkiye\n"
            "Uzunluk: 16.8m\n"
            "Kabin: 4\n"
            "Özellikler: flybridge, geniş güverte, şef mutfağı</code>"
        )
        return {"ok": True}

    if photos:
        best = max(photos, key=lambda x: x.get("file_size", 0))
        file_id = best["file_id"]

        if media_group_id:
            # Çoklu fotoğraf — gruba ekle
            if media_group_id not in media_groups:
                media_groups[media_group_id] = {
                    "chat_id": chat_id,
                    "caption": caption,
                    "file_ids": [],
                    "task": None
                }
                # 2 sn sonra işlemeye başlayacak task oluştur
                task = asyncio.create_task(process_media_group(media_group_id))
                media_groups[media_group_id]["task"] = task

            group = media_groups[media_group_id]
            group["file_ids"].append(file_id)
            if caption:  # caption genelde ilk mesajda gelir
                group["caption"] = caption

        else:
            # Tek fotoğraf
            if not caption:
                await send_message(chat_id, "📝 Fotoğrafın geldi ama bilgiler eksik. Caption'a yat bilgilerini yaz.")
                return {"ok": True}

            await send_message(chat_id, "✅ Fotoğraf alındı! Pipeline başlatılıyor...")
            photo_bytes = await download_photo(file_id)
            asyncio.create_task(
                process_yacht_submission(chat_id, caption, [photo_bytes], send_message)
            )

    elif text and not photos:
        await send_message(chat_id, "📸 Bir de yat fotoğrafı gönder!")

    return {"ok": True}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/debug-keys")
async def debug_keys():
    """API key'lerin ilk 8 karakterini göster (debug için geçici)"""
    def mask(key: str) -> str:
        return key[:8] + "..." if key and len(key) > 8 else f"BOŞ({len(key) if key else 0})"
    el_key = os.getenv("ELEVENLABS_API_KEY", "")
    return {
        "TELEGRAM_BOT_TOKEN": mask(os.getenv("TELEGRAM_BOT_TOKEN", "")),
        "ANTHROPIC_API_KEY": mask(os.getenv("ANTHROPIC_API_KEY", "")),
        "ELEVENLABS_API_KEY": mask(el_key),
        "ELEVENLABS_API_KEY_len": len(el_key),
        "ELEVENLABS_API_KEY_repr": repr(el_key[:12]),
        "ELEVENLABS_API_KEY_end": repr(el_key[-4:]),
        "ELEVENLABS_VOICE_ID": os.getenv("ELEVENLABS_VOICE_ID", ""),
        "RUNWAY_API_KEY": mask(os.getenv("RUNWAY_API_KEY", "")),
    }
