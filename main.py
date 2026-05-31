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

BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "yacht2025")
YOUR_CHAT_ID  = os.getenv("YOUR_CHAT_ID")
TELEGRAM_API  = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = FastAPI(title="Yacht Viral Agent")

# ── helpers ────────────────────────────────────────────────────────

async def send_message(chat_id: int, text: str):
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id, "text": text, "parse_mode": "HTML"
        })

async def download_photo(file_id: str) -> bytes:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id})
        file_path = r.json()["result"]["file_path"]
        dl = await client.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}")
        return dl.content

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

    # güvenlik: sadece senin chat_id'nden gelen mesajları işle
    if str(chat_id) != str(YOUR_CHAT_ID):
        await send_message(chat_id, "❌ Yetkisiz erişim.")
        return {"ok": True}

    text    = message.get("text", "")
    caption = message.get("caption", "")
    photos  = message.get("photo", [])

    if text.startswith("/start"):
        await send_message(chat_id,
            "🚢 <b>Yat Viral Ajan</b>\n\n"
            "Yat fotoğrafını şu formatı caption olarak gönder:\n\n"
            "<code>Model: Azimut 55\n"
            "Fiyat: €1.200.000\n"
            "Konum: Bodrum, Türkiye\n"
            "Uzunluk: 16.8m\n"
            "Kabin: 4\n"
            "Özellikler: flybridge, geniş güverte, şef mutfağı</code>"
        )
        return {"ok": True}

    if photos and caption:
        await send_message(chat_id, "✅ Alındı! Pipeline başlatılıyor...")
        best = max(photos, key=lambda x: x.get("file_size", 0))
        photo_bytes = await download_photo(best["file_id"])
        asyncio.create_task(
            process_yacht_submission(chat_id, caption, photo_bytes, send_message)
        )

    elif photos and not caption:
        await send_message(chat_id, "📝 Fotoğrafın geldi ama bilgiler eksik. Caption'a yat bilgilerini yaz.")
    elif text and not photos:
        await send_message(chat_id, "📸 Bir de yat fotoğrafı gönder!")

    return {"ok": True}

@app.get("/health")
async def health():
    return {"status": "ok"}
