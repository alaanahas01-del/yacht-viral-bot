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
    photo_bytes: bytes,
    notify
):
    loop = asyncio.get_event_loop()

    try:
        # ── 1. Viral hook araştır ───────────────────────────────────
        await notify(chat_id, "🔍 <b>[1/6]</b> Viral hooklar araştırılıyor...")
        hook_data = await loop.run_in_executor(None, generate_viral_hook, yacht_info)

        await notify(chat_id,
            f"✅ Hook seçildi:\n\n"
            f"<i>«{hook_data['hook']}»</i>\n\n"
            f"📊 Teknik: <b>{hook_data['technique']}</b>  |  Skor: <b>{hook_data['viral_score']}/100</b>"
        )

        # ── 2. Hook sesi üret (ElevenLabs) ─────────────────────────
        await notify(chat_id, "🎙️ <b>[2/6]</b> Hook sesi üretiliyor (ElevenLabs)...")
        audio_bytes = await loop.run_in_executor(None, generate_voiceover, hook_data["hook"])

        # ── 3. Drone videosu üret (Runway Gen-3) ───────────────────
        await notify(chat_id,
            "🎬 <b>[3/6]</b> Drone videosu üretiliyor...\n"
            "⏳ Runway Gen-3 ~2-3 dakika sürer, bekliyorum."
        )
        drone_video_url = await loop.run_in_executor(
            None, generate_drone_video, photo_bytes, hook_data["video_prompt"]
        )
        await notify(chat_id, "✅ Drone videosu hazır!")

        # ── 4. Video kurgusu (FFmpeg) ───────────────────────────────
        await notify(chat_id, "✂️ <b>[4/6]</b> Hook + drone video birleştiriliyor...")
        final_video_path = await loop.run_in_executor(
            None, assemble_final_video,
            photo_bytes, audio_bytes, drone_video_url, hook_data["hook"]
        )

        # ── 5. Caption + hashtag üretildi (hook_agent'tan gelir) ───
        await notify(chat_id, "✍️ <b>[5/6]</b> Platform captionları hazır. Yükleniyor...")

        # ── 6. Üç platforma yayınla ─────────────────────────────────
        await notify(chat_id, "🚀 <b>[6/6]</b> TikTok · Instagram · YouTube'a yükleniyor...")
        results = await loop.run_in_executor(
            None, publish_to_all_platforms, final_video_path, hook_data["captions"]
        )

        # ── Sonuç raporu ────────────────────────────────────────────
        report = "🎉 <b>Tamamlandı!</b>\n\n"
        for platform, res in results.items():
            icon = "✅" if res["success"] else "❌"
            report += f"{icon} <b>{platform}</b>\n"
            if res.get("url"):
                report += f"🔗 {res['url']}\n"
            if not res["success"]:
                report += f"⚠️ {res.get('error', 'Bilinmeyen hata')}\n"
            report += "\n"

        await notify(chat_id, report)

        # temizle
        Path(final_video_path).unlink(missing_ok=True)

    except Exception as e:
        logger.error("Pipeline hatası", exc_info=True)
        await notify(chat_id,
            f"❌ <b>Hata oluştu:</b>\n<code>{str(e)[:300]}</code>\n\n"
            "Loglara bak: <code>docker logs yacht-agent</code>"
        )
