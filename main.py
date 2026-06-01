import os
import json
import uuid
import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
import httpx
from dotenv import load_dotenv

from pipeline import process_yacht_submission
from publisher import publish_to_all_platforms

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "yacht2025")
YOUR_CHAT_ID   = os.getenv("YOUR_CHAT_ID")
TELEGRAM_API   = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = FastAPI(title="Yacht Viral Agent")

media_groups: dict = {}     # media_group_id -> {...}
PENDING: dict = {}          # token -> {video_path, captions, hook}

# ── Telegram helpers ────────────────────────────────────────────────

async def send_message(chat_id: int, text: str):
    text = text[:4000]
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{TELEGRAM_API}/sendMessage",
                             json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
            if r.status_code != 200:
                await c.post(f"{TELEGRAM_API}/sendMessage",
                             json={"chat_id": chat_id, "text": text})
    except Exception as e:
        logger.error("send_message: %s", e)

async def send_video(chat_id: int, video_path: str, caption: str = "", reply_markup: dict = None):
    caption = (caption or "")[:1000]
    data = {"chat_id": str(chat_id), "caption": caption, "supports_streaming": "true"}
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    try:
        async with httpx.AsyncClient(timeout=300) as c:
            with open(video_path, "rb") as f:
                r = await c.post(f"{TELEGRAM_API}/sendVideo", data=data,
                                 files={"video": ("reel.mp4", f, "video/mp4")})
            if r.status_code != 200:
                logger.warning("sendVideo %d: %s", r.status_code, r.text[:200])
                with open(video_path, "rb") as f:
                    await c.post(f"{TELEGRAM_API}/sendDocument",
                                 data={"chat_id": str(chat_id), "caption": caption},
                                 files={"document": ("reel.mp4", f, "video/mp4")})
    except Exception as e:
        logger.error("send_video: %s", e)
        await send_message(chat_id, "⚠️ Video oluşturuldu ama gönderilemedi.")

async def answer_callback(callback_id: str, text: str = ""):
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            await c.post(f"{TELEGRAM_API}/answerCallbackQuery",
                         json={"callback_query_id": callback_id, "text": text})
    except Exception as e:
        logger.error("answer_callback: %s", e)

async def download_photo(file_id: str) -> bytes:
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id})
        j = r.json()
        if not j.get("ok"):
            raise RuntimeError(f"getFile: {j}")
        fp = j["result"]["file_path"]
        dl = await c.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fp}")
        dl.raise_for_status()
        return dl.content

# ── Onaya sunma (deliver) ───────────────────────────────────────────

async def deliver_for_approval(chat_id: int, video_path: str, hook_data: dict):
    token = uuid.uuid4().hex[:10]
    PENDING[token] = {
        "video_path": video_path,
        "captions": hook_data.get("captions", {}),
        "hook": hook_data.get("hook", ""),
    }
    kb = {"inline_keyboard": [[
        {"text": "✅ Onayla & Yayınla", "callback_data": f"pub:{token}"},
        {"text": "❌ İptal", "callback_data": f"cancel:{token}"},
    ]]}
    caption = (f"🎬 <b>Reel hazır!</b>\n\n«{hook_data.get('hook','')}»\n\n"
               f"Onaylarsan TikTok · Instagram · YouTube'a caption'larıyla yüklerim.")
    await send_video(chat_id, video_path, caption=caption, reply_markup=kb)

async def do_publish(chat_id: int, token: str):
    item = PENDING.pop(token, None)
    if not item:
        await send_message(chat_id, "⏳ Bu video artık geçerli değil, tekrar üret.")
        return
    await send_message(chat_id, "🚀 Yayınlanıyor: TikTok · Instagram · YouTube...")
    loop = asyncio.get_event_loop()
    try:
        results = await loop.run_in_executor(
            None, publish_to_all_platforms, item["video_path"], item["captions"])
        report = "📊 <b>Yayın sonucu:</b>\n\n"
        for platform, res in results.items():
            icon = "✅" if res.get("success") else "❌"
            report += f"{icon} <b>{platform}</b>"
            if res.get("url"):
                report += f" — {res['url']}"
            elif not res.get("success"):
                report += f" — {res.get('error','hata')[:80]}"
            report += "\n"
        await send_message(chat_id, report)
    except Exception as e:
        logger.error("publish hata", exc_info=True)
        await send_message(chat_id, f"❌ Yayın hatası: <code>{str(e)[:300]}</code>")
    finally:
        try:
            Path(item["video_path"]).unlink(missing_ok=True)
        except Exception:
            pass

# ── Media group toplama ─────────────────────────────────────────────

async def process_media_group(group_id: str):
    await asyncio.sleep(2)
    group = media_groups.pop(group_id, None)
    if not group:
        return
    chat_id, caption, file_ids = group["chat_id"], group["caption"], group["file_ids"]
    if not caption:
        await send_message(chat_id, "📝 Fotoğraflar geldi ama bilgi eksik. Caption'a yat bilgilerini yaz.")
        return
    await send_message(chat_id, f"✅ {len(file_ids)} fotoğraf alındı! İşleniyor...")
    photos = []
    for fid in file_ids:
        try:
            photos.append(await download_photo(fid))
        except Exception as e:
            logger.warning("foto indirilemedi: %s", e)
    if not photos:
        await send_message(chat_id, "❌ Fotoğraflar indirilemedi.")
        return
    asyncio.create_task(process_yacht_submission(
        chat_id, caption, photos, send_message, deliver_for_approval))

# ── Webhook ─────────────────────────────────────────────────────────

@app.post(f"/webhook/{WEBHOOK_SECRET}")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad JSON")

    # 1) Buton tıklamaları (onay/iptal)
    cq = data.get("callback_query")
    if cq:
        chat_id = cq["message"]["chat"]["id"]
        await answer_callback(cq["id"])
        if str(chat_id) != str(YOUR_CHAT_ID):
            return {"ok": True}
        cdata = cq.get("data", "")
        if cdata.startswith("pub:"):
            asyncio.create_task(do_publish(chat_id, cdata[4:]))
        elif cdata.startswith("cancel:"):
            item = PENDING.pop(cdata[7:], None)
            if item:
                try: Path(item["video_path"]).unlink(missing_ok=True)
                except Exception: pass
            await send_message(chat_id, "❌ İptal edildi.")
        return {"ok": True}

    # 2) Normal mesaj
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
            "Yat fotoğraflarını (en iyisi: bir tanesi dıştan/denizde) + bilgileri "
            "caption olarak gönder:\n\n"
            "<code>Model: Azimut 55\nKonum: Bodrum\nUzunluk: 16.8m\n"
            "Kabin: 4\nÖzellikler: flybridge, geniş güverte</code>\n\n"
            "Profesyonel reel üretip onayına sunacağım.")
        return {"ok": True}

    if photos:
        best = max(photos, key=lambda x: x.get("file_size", 0))
        file_id = best["file_id"]
        if media_group_id:
            if media_group_id not in media_groups:
                media_groups[media_group_id] = {
                    "chat_id": chat_id, "caption": caption, "file_ids": [], "task": None}
                t = asyncio.create_task(process_media_group(media_group_id))
                media_groups[media_group_id]["task"] = t
            g = media_groups[media_group_id]
            g["file_ids"].append(file_id)
            if caption:
                g["caption"] = caption
        else:
            if not caption:
                await send_message(chat_id, "📝 Caption'a yat bilgilerini yaz.")
                return {"ok": True}
            await send_message(chat_id, "✅ Fotoğraf alındı! İşleniyor...")
            try:
                pb = await download_photo(file_id)
            except Exception as e:
                logger.error("foto indirilemedi: %s", e)
                await send_message(chat_id, "❌ Fotoğraf indirilemedi.")
                return {"ok": True}
            asyncio.create_task(process_yacht_submission(
                chat_id, caption, [pb], send_message, deliver_for_approval))
    elif text and not photos:
        await send_message(chat_id, "📸 Bir de yat fotoğrafı gönder!")

    return {"ok": True}

@app.get("/health")
async def health():
    return {"status": "ok"}
