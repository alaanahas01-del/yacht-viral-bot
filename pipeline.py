import os
import asyncio
import logging

from hook_agent      import generate_viral_hook
from audio_generator import generate_voiceover, generate_outro_voice
from music_generator import generate_music
from video_generator import generate_drone_video
from video_editor    import assemble_final_video, PHOTO_DUR, ENDCARD_DUR, XFADE, MAX_PHOTOS

logger = logging.getLogger(__name__)


def _estimate_duration(n_photos: int, has_drone: bool) -> float:
    n = min(n_photos, MAX_PHOTOS)
    clips = (1 if has_drone else 0) + n + 1  # +endcard
    base = (5.0 if has_drone else 0.0) + PHOTO_DUR * n + ENDCARD_DUR
    return max(8.0, base - XFADE * (clips - 1))


async def process_yacht_submission(chat_id, yacht_info, photos_bytes, notify, deliver):
    """
    Profesyonel reel üretir ve onaya sunar (deliver callback).
    deliver(chat_id, video_path, hook_data) -> Telegram'a video + onay butonları gönderir.
    """
    loop = asyncio.get_event_loop()
    try:
        # 1. Hook + caption (Claude)
        await notify(chat_id, "🧠 <b>[1/5]</b> Viral hook ve caption'lar hazırlanıyor...")
        hook_data = await loop.run_in_executor(None, generate_viral_hook, yacht_info)
        await notify(chat_id,
            f"✅ Hook: <i>«{hook_data['hook']}»</i>\n"
            f"📊 {hook_data['technique']} · skor {hook_data['viral_score']}/100")

        # 2. Seslendirme (hook + outro) — opsiyonel
        await notify(chat_id, "🎙️ <b>[2/5]</b> Seslendirme üretiliyor...")
        try:
            hook_voice = await loop.run_in_executor(None, generate_voiceover, hook_data["hook"])
        except Exception as e:
            logger.warning("Hook sesi yok: %s", e)
            hook_voice = None
        outro_voice = await loop.run_in_executor(None, generate_outro_voice)
        outro_voice = outro_voice or None

        # 3. Sinematik drone görüntüsü (fal.ai Kling) — opsiyonel
        await notify(chat_id, "🎬 <b>[3/5]</b> Sinematik görüntü üretiliyor (1-3 dk)...")
        drone_url = await loop.run_in_executor(
            None, generate_drone_video, photos_bytes[0], hook_data.get("video_prompt", ""))
        if not drone_url:
            await notify(chat_id, "ℹ️ AI görüntü atlandı — fotoğraflar sinematik hareketle işlenecek.")

        # 4. Müzik (ElevenLabs Music / prosedürel yedek)
        est = _estimate_duration(len(photos_bytes), bool(drone_url))
        try:
            music = await loop.run_in_executor(None, generate_music, est + 3)
            music = music or None
        except Exception as e:
            logger.warning("Müzik yok: %s", e)
            music = None

        # 5. Profesyonel montaj
        await notify(chat_id, "✂️ <b>[4/5]</b> Profesyonel montaj yapılıyor...")
        contact = os.getenv("CONTACT_HANDLE", "")
        video_path = await loop.run_in_executor(
            None, assemble_final_video,
            photos_bytes, hook_data["hook"], music, hook_voice, outro_voice, drone_url, contact)

        # 6. Onaya sun
        await notify(chat_id, "📤 <b>[5/5]</b> Video hazır! Onayına sunuluyor...")
        await deliver(chat_id, video_path, hook_data)

    except Exception as e:
        logger.error("Pipeline hatası", exc_info=True)
        await notify(chat_id, f"❌ <b>Hata:</b> <code>{str(e)[:400]}</code>")
