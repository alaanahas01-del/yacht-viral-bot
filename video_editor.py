import io
import logging
import shutil
import subprocess
import tempfile
import textwrap
import uuid
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("output_videos")
OUTPUT_DIR.mkdir(exist_ok=True)

# Türkçe destekleyen font yolları (Docker'da fonts-dejavu-core ile gelir)
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]


def _find_font() -> str:
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return f
    logger.warning("Türkçe font bulunamadı, metin overlay atlanabilir")
    return ""


def _normalize_jpeg(photo_bytes: bytes) -> bytes:
    """Her fotoğrafı standart RGB JPEG'e çevir (FFmpeg uyumlu)."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(photo_bytes))
        img = img.convert("RGB")
        max_dim = 2160
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()
    except Exception as e:
        logger.warning("JPEG normalize başarısız (%s), orijinal kullanılıyor", e)
        return photo_bytes


def _run(cmd: list, label: str):
    """FFmpeg komutunu çalıştırır, hata varsa loglar ve exception fırlatır."""
    logger.info("FFmpeg [%s] başlıyor", label)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = result.stderr or ""
        logger.error("FFmpeg [%s] rc=%d stderr:\n%s", label, result.returncode, stderr[-4000:])
        # Hem baş hem son kısmı mesaja koy (gerçek hata genelde sonda)
        raise RuntimeError(
            f"FFmpeg [{label}] rc={result.returncode}: ...{stderr[-600:]}"
        )
    logger.info("FFmpeg [%s] tamam", label)


def _wrap_hook(hook_text: str, width: int = 20) -> str:
    """Hook metnini satırlara böl (drawtext otomatik sarmıyor)."""
    clean = " ".join(hook_text.split())[:90]
    lines = textwrap.wrap(clean, width=width)
    return "\n".join(lines[:4])  # max 4 satır


def _build_hook_segment(photo_paths, audio_path, hook_text, out_path, font, with_text):
    """
    Tüm fotoğrafları slaytshow yapıp (concat), üstüne hook metni + ses ekler.
    Tek FFmpeg komutu — daha az hata noktası.
    """
    n = len(photo_paths)
    per = 4.0 if n == 1 else 3.0
    total = max(per * n, 4.0)

    tmp_dir = out_path.parent
    hook_txt = tmp_dir / "hook.txt"
    hook_txt.write_text(_wrap_hook(hook_text), encoding="utf-8")

    # Inputlar: her foto -loop 1 -t per -i  (input-side -t = güvenilir slaytshow)
    cmd = ["ffmpeg", "-y"]
    for p in photo_paths:
        cmd += ["-loop", "1", "-t", str(per), "-i", str(p)]
    # Ses: varsa mp3, yoksa sessiz ses (pipeline sesi üretemezse video yine çıksın)
    if audio_path and Path(audio_path).exists():
        cmd += ["-i", str(audio_path)]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]

    # filter_complex
    parts = []
    for i in range(n):
        parts.append(
            f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop=1080:1920,setsar=1,fps=30,format=yuv420p[v{i}]"
        )
    concat_inputs = "".join(f"[v{i}]" for i in range(n))
    parts.append(f"{concat_inputs}concat=n={n}:v=1:a=0[slides]")

    if with_text and font:
        # textfile kullan — Türkçe karakter + escape sorununu tamamen çözer
        drawtext = (
            f"[slides]drawtext=textfile={hook_txt}:fontfile={font}"
            ":fontsize=52:fontcolor=white"
            ":x=(w-text_w)/2:y=h*0.58"
            ":line_spacing=10"
            ":shadowcolor=black@0.8:shadowx=2:shadowy=2"
            ":box=1:boxcolor=black@0.5:boxborderw=20[v]"
        )
        parts.append(drawtext)
        vmap = "[v]"
    else:
        vmap = "[slides]"

    filter_complex = ";".join(parts)

    cmd += [
        "-filter_complex", filter_complex,
        "-map", vmap, "-map", f"{n}:a",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-t", str(total),
        str(out_path)
    ]
    _run(cmd, f"hook-segment(text={with_text},n={n})")


def assemble_final_video(
    photos_bytes: list,   # list of bytes (1-10 fotoğraf)
    audio_bytes: bytes,
    drone_video_url: str,
    hook_text: str
) -> str:
    """
    Final video:
      Segment 1 → fotoğraf slaytshow + hook metni + ses
      Segment 2 → Runway drone videosu (sessiz)
    Tüm adımlarda fallback var; en kötü durumda en azından slaytshow döner.
    Döner: final video dosya yolu
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        font = _find_font()

        audio_path = tmp / "hook.mp3"
        drone_path = tmp / "drone.mp4"
        hook_seg   = tmp / "seg_hook.mp4"
        drone_seg  = tmp / "seg_drone.mp4"
        final_tmp  = tmp / "final.mp4"

        if audio_bytes:
            audio_path.write_bytes(audio_bytes)
        else:
            audio_path = None  # sessiz devam

        # ── fotoğrafları normalize edip yaz ────────────────────────
        photo_paths = []
        for i, pb in enumerate(photos_bytes):
            p = tmp / f"photo_{i}.jpg"
            p.write_bytes(_normalize_jpeg(pb))
            photo_paths.append(p)
        if not photo_paths:
            raise ValueError("Hiç fotoğraf yok")

        # ── Segment 1: hook (önce metinli, olmazsa metinsiz) ──────
        try:
            _build_hook_segment(photo_paths, audio_path, hook_text, hook_seg, font, with_text=True)
        except Exception as e:
            logger.warning("Metinli hook segment başarısız (%s), metinsiz deneniyor", e)
            _build_hook_segment(photo_paths, audio_path, hook_text, hook_seg, font, with_text=False)

        # ── Drone videosu indir + segment yap (başarısız olursa atla) ─
        drone_ok = False
        try:
            if not drone_video_url:
                raise ValueError("drone URL yok")
            logger.info("Drone video indiriliyor...")
            resp = requests.get(drone_video_url, timeout=120)
            resp.raise_for_status()
            drone_path.write_bytes(resp.content)

            _run([
                "ffmpeg", "-y",
                "-i", str(drone_path),
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-vf", ("scale=1080:1920:force_original_aspect_ratio=increase,"
                        "crop=1080:1920,setsar=1,fps=30,format=yuv420p"),
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
                "-t", "8", "-shortest", "-pix_fmt", "yuv420p",
                str(drone_seg)
            ], "drone-segment")
            drone_ok = True
        except Exception as e:
            logger.warning("Drone segment başarısız (%s), sadece slaytshow kullanılacak", e)

        # ── Birleştir (drone varsa) ────────────────────────────────
        if drone_ok:
            try:
                _run([
                    "ffmpeg", "-y",
                    "-i", str(hook_seg),
                    "-i", str(drone_seg),
                    "-filter_complex",
                    "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
                    "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
                    str(final_tmp)
                ], "concat")
            except Exception as e:
                logger.warning("Final concat başarısız (%s), sadece hook segment", e)
                shutil.copy(hook_seg, final_tmp)
        else:
            shutil.copy(hook_seg, final_tmp)

        # ── kalıcı konuma taşı ─────────────────────────────────────
        dest = OUTPUT_DIR / f"yacht_{uuid.uuid4().hex[:8]}.mp4"
        shutil.copy(final_tmp, dest)
        logger.info("Final video: %s (%d KB)", dest, dest.stat().st_size // 1024)
        return str(dest)
