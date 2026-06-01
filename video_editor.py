import io
import logging
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


def _normalize_jpeg(photo_bytes: bytes) -> bytes:
    """Her fotoğrafı standart RGB JPEG'e çevir (FFmpeg uyumlu)."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(photo_bytes))
        img = img.convert("RGB")
        # Max boyut sınırla
        max_dim = 2160
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()
    except Exception as e:
        logger.warning("JPEG normalize başarısız (%s), orijinal kullanılıyor", e)
        return photo_bytes

OUTPUT_DIR = Path("output_videos")
OUTPUT_DIR.mkdir(exist_ok=True)


def _run(cmd: list, label: str):
    """FFmpeg komutunu çalıştırır, hata varsa loglar."""
    logger.info("FFmpeg [%s]: %s", label, " ".join(str(c) for c in cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("FFmpeg stderr [%s]:\n%s", label, result.stderr[-1000:])
        raise RuntimeError(f"FFmpeg başarısız [{label}]: {result.stderr[-300:]}")


def assemble_final_video(
    photos_bytes: list,   # list of bytes (1-10 fotoğraf)
    audio_bytes: bytes,
    drone_video_url: str,
    hook_text: str
) -> str:
    """
    Final video montajı:
      Segment 1  → fotoğraf slaytshow + hook metni overlay + sesli hook
      Segment 2 (10 sn) → Runway drone videosu (sessiz)
      Toplam             → ~13+ saniyelik 1080x1920 MP4
    Döner: final video dosya yolu
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        audio_path = tmp / "hook.mp3"
        drone_path = tmp / "drone.mp4"
        hook_seg   = tmp / "seg_hook.mp4"
        drone_seg  = tmp / "seg_drone.mp4"
        final_tmp  = tmp / "final.mp4"

        # ── dosyaları yaz ──────────────────────────────────────────
        audio_path.write_bytes(audio_bytes)
        logger.info("Drone video indiriliyor...")
        drone_path.write_bytes(requests.get(drone_video_url, timeout=120).content)

        # ── fotoğrafları yaz (Pillow ile normalize ederek) ────────
        photo_paths = []
        for i, pb in enumerate(photos_bytes):
            p = tmp / f"photo_{i}.jpg"
            p.write_bytes(_normalize_jpeg(pb))
            photo_paths.append(p)

        n = len(photo_paths)
        slide_duration = max(3.0, 3.0 * n) / n  # her fotoğrafa eşit süre, toplam min 3 sn
        total_duration = slide_duration * n

        # ── hook metni: özel karakterleri temizle ──────────────────
        safe_hook = (
            hook_text
            .replace("\\", "")
            .replace("'", "’")
            .replace(":", "\\:")
            .replace("[", "\\[")
            .replace("]", "\\]")
            .replace(",", "\\,")
        )[:80]

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

        # ── Segment 1: slaytshow + hook metni + ses ────────────────
        if n == 1:
            # Tek fotoğraf
            vf_hook = (
                "scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,"
                f"{drawtext}"
            )
            _run([
                "ffmpeg", "-y",
                "-loop", "1",
                "-framerate", "30",
                "-i", str(photo_paths[0]),
                "-i", str(audio_path),
                "-vf", vf_hook,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-r", "30",
                "-t", str(total_duration),
                "-pix_fmt", "yuv420p",
                str(hook_seg)
            ], "hook-segment")
        else:
            # Çoklu fotoğraf — her fotoğrafı ayrı video'ya çevir, sonra birleştir
            slide_segs = []
            for i, p in enumerate(photo_paths):
                seg = tmp / f"slide_{i}.mp4"
                logger.info("Slide %d: %s (%d KB)", i, p, p.stat().st_size // 1024)
                _run([
                    "ffmpeg", "-y",
                    "-loop", "1",
                    "-framerate", "30",
                    "-i", str(p),
                    "-vf", (
                        "scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,"
                        "crop=1080:1920,"
                        "setsar=1,"
                        "format=yuv420p"
                    ),
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-r", "30",
                    "-t", str(slide_duration),
                    "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart",
                    str(seg)
                ], f"slide-{i}")
                slide_segs.append(seg)

            # concat filter ile birleştir
            inputs = []
            for seg in slide_segs:
                inputs += ["-i", str(seg)]
            n_segs = len(slide_segs)
            filter_str = "".join(f"[{i}:v]" for i in range(n_segs))
            filter_str += f"concat=n={n_segs}:v=1:a=0[v]"

            slideshow_raw = tmp / "slideshow_raw.mp4"
            _run([
                "ffmpeg", "-y",
                *inputs,
                "-filter_complex", filter_str,
                "-map", "[v]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-r", "30", "-pix_fmt", "yuv420p",
                str(slideshow_raw)
            ], "slideshow")

            # hook metni + ses ekle
            vf_text = (
                "scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,"
                f"{drawtext}"
            )
            _run([
                "ffmpeg", "-y",
                "-i", str(slideshow_raw),
                "-i", str(audio_path),
                "-vf", vf_text,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-r", "30", "-t", str(total_duration),
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
        logger.info("Final video: %s (%d KB)", dest, dest.stat().st_size // 1024)
        return str(dest)
