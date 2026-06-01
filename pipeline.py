import asyncio
import logging
from pathlib import Path

from hook_agent      import generate_viral_hook
from audio_generator import generate_voiceover
from video_generator import generate_drone_video
from video_editor    import assemble_final_video
from publisher       import publish_to_all_platforms

logger = logging.getLogger(__name__)

async def process_yacht_submission(
    chat_id: int,
    yacht_info: str,
    photos_bytes: list,   # list of bytes (1-10 fotoğraf)
    notify,
    send_video=None       # opsiyonel: videoyu Telegram'a gönder
):
    loop = asyncio.get_event_loop()

    try:
        # ── 1. Viral hook araştır ───────────────────────────────────
        await notify(chat_id, "🔍 <b>[1/5]</b> Viral hooklar araştırılıyor...")
        hook_data = await loop.run_in_executor(None, generate_viral_hook, yacht_info)

        await notify(chat_id,
            f"✅ Hook seçildi:\n\n"
            f"<i>«{hook_data['hook']}»</i>\n\n"
            f"📊 Teknik: <b>{hook_data['technique']}</b>  |  Skor: <b>{hook_data['viral_score']}/100</b>"
        )

        # ── 2. Hook sesi üret (ElevenLabs) ─────────────────────────
        await notify(chat_id, "🎙️ <b>[2/5]</b> Hook sesi üretiliyor (ElevenLabs)...")
        try:
            audio_bytes = await loop.run_in_executor(None, generate_voiceover, hook_data["hook"])
        except Exception as e:
            logger.warning("Ses üretilemedi: %s", e)
            await notify(chat_id, "⚠️ Ses üretilemedi (kota/hata), video sessiz devam edecek.")
            audio_bytes = None

        # ── 3. Drone videosu üret (Runway Gen-3) ───────────────────
        await notify(chat_id,
            "🎬 <b>[3/5]</b> Drone videosu üretiliyor...\n"
            "⏳ Runway Gen-3 ~2-3 dakika sürer, bekliyorum."
        )
        try:
            drone_video_url = await loop.run_in_executor(
                None, generate_drone_video, photos_bytes[0], hook_data["video_prompt"]
            )
            await notify(chat_id, "✅ Drone videosu hazır!")
        except Exception as e:
            logger.warning("Drone videosu üretilemedi: %s", e)
            await notify(chat_id, "⚠️ Drone videosu üretilemedi, sadece fotoğraflarla devam.")
            drone_video_url = ""

        # ── 4. Video kurgusu (FFmpeg) ───────────────────────────────
        await notify(chat_id, "✂️ <b>[4/5]</b> Hook + drone video birleştiriliyor...")
        final_video_path = await loop.run_in_executor(
            None, assemble_final_video,
            photos_bytes, audio_bytes, drone_video_url, hook_data["hook"]
        )

        # ── 5. Videoyu Telegram'a gönder ───────────────────────────
        await notify(chat_id, "📤 <b>[5/5]</b> Video hazır, gönderiliyor...")
        if send_video:
            await send_video(
                chat_id,
                final_video_path,
                caption=f"🎬 {hook_data['hook']}\n\n📊 Viral skor: {hook_data['viral_score']}/100"
            )
        await notify(chat_id, "🎉 <b>Video tamamlandı!</b>\n\n📋 Captions:\n\n"
            f"<b>TikTok:</b>\n{hook_data['captions'].get('tiktok','')}\n\n"
            f"<b>Instagram:</b>\n{hook_data['captions'].get('instagram','')}\n\n"
            f"<b>YouTube:</b>\n{hook_data['captions'].get('youtube','')}"
        )

        # temizle
        Path(final_video_path).unlink(missing_ok=True)

    except Exception as e:
        logger.error("Pipeline hatası", exc_info=True)
        await notify(chat_id,
            f"❌ <b>Hata oluştu:</b>\n<code>{str(e)[:400]}</code>"
        )
