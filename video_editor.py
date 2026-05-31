import logging
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("output_videos")
OUTPUT_DIR.mkdir(exist_ok=True)


def _run(cmd: list, label: str):
    """FFmpeg komutunu çalıştırır, hata varsa loglar."""
    logger.info(f"FFmpeg [{label}]: {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"FFmpeg stderr [{label}]:\n{result.stderr[-1000:]}")
        raise RuntimeError(f"FFmpeg başarısız [{label}]: {result.stderr[-300:]}")


def assemble_final_video(
    photo_bytes: bytes,
    audio_bytes: bytes,
    drone_video_url: str,
    hook_text: str
) -> str:
    """
    Final video montajı:
      Segment 1 (3 sn)  → yat fotoğrafı arka plan + hook metni overlay + sesli hook
      Segment 2 (10 sn) → Runway drone videosu (sessiz) 
      Toplam             → ~13 saniyelik 1080×1920 MP4
    Döner: final video dosya yolu
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        photo_path  = tmp / "photo.jpg"
        audio_path  = tmp / "hook.mp3"
        drone_path  = tmp / "drone.mp4"
        hook_seg    = tmp / "seg_hook.mp4"
        drone_seg   = tmp / "seg_drone.mp4"
        final_tmp   = tmp / "final.mp4"

        # ── dosyaları yaz ──────────────────────────────────────────
        photo_path.write_bytes(photo_bytes)
        audio_path.write_bytes(audio_bytes)
        logger.info("Drone video indiriliyor...")
        drone_path.write_bytes(requests.get(drone_video_url, timeout=120).content)

        # ── hook metni: tek tırnak ve özel karakterleri temizle ────
        safe_hook = (
            hook_text
            .replace("\\", "")
            .replace("'", "\u2019")   # düz tırnak → sağ tırnak
            .replace(":", "\\:")
            .replace("[", "\\[")
            .replace("]", "\\]")
            .replace(",", "\\,")
        )[:80]  # max 80 karakter

        # ── Segment 1: fotoğraf + hook metni + ses (3 sn) ──────────
        drawtext = (
            f"drawtext=text='{safe_hook}'"
            ":fontsize=54"
            ":fontcolor=white"
            ":x=(w-text_w)/2"
            ":y=(h*0.55-text_h/2)"
            ":shadowcolor=black@0.8"
            ":shadowx=2:shadowy=2"
            ":box=1"
            ":boxcolor=black@0.45"
            ":boxborderw=18"
        )

        vf_hook = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,"
            f"{drawtext}"
        )

        _run([
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(photo_path),
            "-i", str(audio_path),
            "-vf", vf_hook,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-r", "30", "-t", "3",
            "-pix_fmt", "yuv420p",
            str(hook_seg)
        ], "hook-segment")

        # ── Segment 2: drone video (ölçekle + sessiz ses ekle) ─────
        vf_drone = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920"
        )

        _run([
            "ffmpeg", "-y",
            "-i", str(drone_path),
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-vf", vf_drone,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-r", "30", "-t", "10",
            "-pix_fmt", "yuv420p",
            "-shortest",
            str(drone_seg)
        ], "drone-segment")

        # ── Birleştir ───────────────────────────────────────────────
        _run([
            "ffmpeg", "-y",
            "-i", str(hook_seg),
            "-i", str(drone_seg),
            "-filter_complex",
            "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "aac", "-b:a", "128k",
            "-pix_fmt", "yuv420p",
            str(final_tmp)
        ], "concat")

        # ── kalıcı konuma taşı ─────────────────────────────────────
        dest = OUTPUT_DIR / f"yacht_{uuid.uuid4().hex[:8]}.mp4"
        shutil.copy(final_tmp, dest)
        logger.info(f"Final video: {dest} ({dest.stat().st_size // 1024} KB)")
        return str(dest)
