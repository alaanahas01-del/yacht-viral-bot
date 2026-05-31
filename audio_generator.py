import os
import requests
import logging

logger = logging.getLogger(__name__)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
# Türkçe destekleyen ses — ElevenLabs voice library'den değiştirebilirsin
VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

def generate_voiceover(text: str) -> bytes:
    """
    ElevenLabs multilingual v2 ile Türkçe hook metni seslendirme.
    Döner: MP3 bytes
    """
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"

    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": ELEVENLABS_API_KEY
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

    logger.info(f"ElevenLabs TTS: {text[:60]}...")
    r = requests.post(url, json=payload, headers=headers, timeout=30)
    r.raise_for_status()

    logger.info(f"Ses üretildi: {len(r.content)} byte")
    return r.content  # MP3 bytes
