import os
import requests
import logging

logger = logging.getLogger(__name__)

def generate_voiceover(text: str) -> bytes:
    """
    ElevenLabs multilingual v2 ile Türkçe hook metni seslendirme.
    Döner: MP3 bytes
    """
    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY environment variable is not set")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key
    }

    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.4,
            "similarity_boost": 0.85,
            "style": 0.35,
            "use_speaker_boost": True
        }
    }

    logger.info("ElevenLabs TTS: %s...", text[:60])
    logger.info("ElevenLabs key len=%d prefix=%s", len(api_key), api_key[:8])
    r = requests.post(url, json=payload, headers=headers, timeout=30)

    if r.status_code != 200:
        logger.error("ElevenLabs hata %d: %s", r.status_code, r.text[:500])
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:300]}")

    logger.info("Ses üretildi: %d byte", len(r.content))
    return r.content
