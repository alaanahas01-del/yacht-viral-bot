"""
Arka plan müziği üretimi.
1) ElevenLabs Music API (mevcut key ile enstrümantal sinematik müzik)
2) Başarısızsa: prosedürel FFmpeg pad (her zaman çalışır, royalty-free)
"""
import os
import logging
import subprocess
import tempfile
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

MUSIC_PROMPT = (
    "Cinematic luxury lifestyle background music, elegant and uplifting, "
    "smooth deep house with warm piano and soft pads, Mediterranean summer vibe, "
    "premium yacht commercial, no vocals, steady gentle beat"
)


def generate_music(duration_sec: float) -> bytes:
    """
    Enstrümantal müzik üret.
    Varsayılan: prosedürel (ücretsiz, kredini korur).
    USE_AI_MUSIC=1 ise: ElevenLabs Music (daha kaliteli) -> hata olursa prosedürel.
    """
    ms = int(max(3, min(duration_sec, 60)) * 1000)
    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    use_ai = os.getenv("USE_AI_MUSIC", "").strip() == "1"

    if api_key and use_ai:
        try:
            r = requests.post(
                "https://api.elevenlabs.io/v1/music",
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "prompt": MUSIC_PROMPT,
                    "music_length_ms": ms,
                    "force_instrumental": True,
                },
                timeout=180,
            )
            if r.status_code == 200 and len(r.content) > 1000:
                logger.info("ElevenLabs müzik üretildi: %d byte", len(r.content))
                return r.content
            logger.warning("ElevenLabs müzik hata %d: %s", r.status_code, r.text[:200])
        except Exception as e:
            logger.warning("ElevenLabs müzik exception: %s", e)

    # Fallback: prosedürel sinematik pad
    return _procedural_bed(duration_sec)


def _procedural_bed(duration_sec: float) -> bytes:
    """
    FFmpeg ile katmanlı sinematik pad (royalty-free, her zaman çalışır).
    Yumuşak akor + hafif tremolo + reverb benzeri. Müzik kadar zengin değil
    ama sessizlikten çok daha profesyonel.
    """
    dur = max(3.0, min(duration_sec, 60))
    # Akor: A minör pad (A2 110, C4 261.6, E4 329.6, A4 440) + bas
    freqs = [110.0, 164.81, 220.0, 261.63, 329.63]
    srcs, mixn = [], []
    for i, f in enumerate(freqs):
        srcs += ["-f", "lavfi", "-t", f"{dur}",
                 "-i", f"sine=frequency={f}:sample_rate=44100"]
        # her katmana hafif tremolo + ses seviyesi
        vol = 0.22 if f < 130 else 0.13
        mixn.append(f"[{i}:a]volume={vol},tremolo=f={4.5+i*0.4}:d=0.25[a{i}]")
    chain = ";".join(mixn)
    mixin = "".join(f"[a{i}]" for i in range(len(freqs)))
    # yumuşak giriş/çıkış + alçak geçiren filtre (sıcak ton) + hafif reverb (aecho)
    filt = (
        f"{chain};{mixin}amix=inputs={len(freqs)}:normalize=0[mx];"
        f"[mx]lowpass=f=2200,aecho=0.8:0.85:55:0.3,"
        f"afade=t=in:st=0:d=1.5,afade=t=out:st={dur-2:.2f}:d=2,"
        f"volume=1.6[out]"
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "bed.mp3"
        cmd = ["ffmpeg", "-y", *srcs, "-filter_complex", filt,
               "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "128k", str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            logger.error("Prosedürel müzik hata: %s", (r.stderr or "")[-400:])
            return b""
        data = out.read_bytes()
        logger.info("Prosedürel müzik üretildi: %d byte", len(data))
        return data
