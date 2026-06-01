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

# Düşük bellek için ortak x264 ayarları (Railway 512MB free tier OOM olmasın)
X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-threads", "1"]
AAC = ["-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2"]
VF_FILL = ("scale=1080:1920:force_original_aspect_ratio=increase,"
           "crop=1080:1920,setsar=1,fps=30,format=yuv420p")


def _find_font() -> str:
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return f
    logger.warning("Türkçe font bulunamadı, metin overlay atlanacak")
    return ""


def _normalize_jpeg(photo_bytes: bytes) -> bytes:
    """Her fotoğrafı standart RGB JPEG'e çevir (FFmpeg uyumlu, düşük çözünürlük)."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(photo_bytes))
        img = img.convert("RGB")
        # Bellek için makul boyut: dikey reel zaten 1080x1920'ye küçülecek
        max_dim = 1920
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
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
        hint = ""
        if result.returncode == -9:
            hint = " (BELLEK YETERSİZ — OOM kill)"
        raise RuntimeError(f"FFmpeg [{label}] rc={result.returncode}{hint}: ...{stderr[-500:]}")
    logger.info("FFmpeg [%s] tamam", label)


def _wrap_hook(hook_text: str, width: int = 20) -> str:
    """Hook metnini satırlara böl (drawtext otomatik sarmıyor)."""
    clean = " ".join(hook_text.split())[:90]
    lines = textwrap.wrap(clean, width=width)
    return "\n".join(lines[:4])


def _build_hook_segment(photo_paths, audio_path, hook_text, out_path, font, with_text):
    """
    DÜŞÜK BELLEK yaklaşımı:
      1. Her fotoğrafı TEK TEK küçük mp4'e encode et (bellekte tek görsel)
      2. concat demuxer (-c copy) ile birleştir (bellek ~0)
      3. Üstüne metin + ses ekle (tek video girişi)
    Bu sayede 9-10 fotoğraf aynı anda belleğe açılmaz → OOM olmaz.
    """
    n = len(photo_paths)
    per = 4.0 if n == 1 else 3.0
    total = max(per * n, 4.0)
    tmp_dir = out_path.parent

    # ── 1. Her slide ayrı encode (düşük bellek) ────────────────────
    slide_files = []
    for i, p in enumerate(photo_paths):
        s = tmp_dir / f"slide_{i}.mp4"
        _run([
            "ffmpeg", "-y",
            "-loop", "1", "-t", str(per), "-i", str(p),
            "-vf", VF_FILL, *X264, "-an",
            str(s)
        ], f"slide-{i}")
        slide_files.append(s)

    # ── 2. concat demuxer ile birleştir (-c copy, bellek ~0) ───────
    if n == 1:
        slides_raw = slide_files[0]
    else:
        list_file = tmp_dir / "slides.txt"
        list_file.write_text("".join(f"file '{s.as_posix()}'\n" for s in slide_files))
        slides_raw = tmp_dir / "slides_raw.mp4"
        _run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c", "copy", str(slides_raw)
        ], "concat-slides")

    # ── 3. Metin + ses ekle (tek video girişi = düşük bellek) ──────
    cmd = ["ffmpeg", "-y", "-i", str(slides_raw)]
    if audio_path and Path(audio_path).exists():
        cmd += ["-i", str(audio_path)]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]

    if with_text and font:
        hook_txt = tmp_dir / "hook.txt"
        hook_txt.write_text(_wrap_hook(hook_text), encoding="utf-8")
        vf = (
            f"drawtext=textfile={hook_txt}:fontfile={font}"
            ":fontsize=52:fontcolor=white"
            ":x=(w-text_w)/2:y=h*0.58:line_spacing=10"
            ":shadowcolor=black@0.8:shadowx=2:shadowy=2"
            ":box=1:boxcolor=black@0.5:boxborderw=20"
        )
        cmd += ["-vf", vf]

    cmd += [
        "-map", "0:v", "-map", "1:a",
        *X264, *AAC,
        "-t", str(total),
        str(out_path)
    ]
    _run(cmd, f"hook-overlay(text={with_text},n={n})")


def assemble_final_video(
    photos_bytes: list,
    audio_bytes: bytes,
    drone_video_url: str,
    hook_text: str
) -> str:
    """
    Final video: slaytshow (foto + hook metni + ses) + drone videosu.
    Her adımda fallback var; en kötü durumda en azından slaytshow döner.
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
            audio_path = None

        # ── fotoğrafları normalize edip diske yaz ──────────────────
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

        # ── Drone videosu indir + segment (başarısız olursa atla) ──
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
                "-vf", VF_FILL, *X264, *AAC,
                "-t", "8", "-shortest",
                str(drone_seg)
            ], "drone-segment")
            drone_ok = True
        except Exception as e:
            logger.warning("Drone segment başarısız (%s), sadece slaytshow", e)

        # ── Birleştir (drone varsa) ────────────────────────────────
        if drone_ok:
            try:
                _run([
                    "ffmpeg", "-y",
                    "-i", str(hook_seg), "-i", str(drone_seg),
                    "-filter_complex",
                    "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
                    "-map", "[v]", "-map", "[a]",
                    *X264, *AAC,
                    str(final_tmp)
                ], "concat-final")
            except Exception as e:
                logger.warning("Filter concat başarısız (%s), demuxer copy deneniyor", e)
                try:
                    flist = tmp / "final.txt"
                    flist.write_text(f"file '{hook_seg.as_posix()}'\nfile '{drone_seg.as_posix()}'\n")
                    _run([
                        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(flist),
                        "-c", "copy", str(final_tmp)
                    ], "concat-final-copy")
                except Exception as e2:
                    logger.warning("Demuxer concat da başarısız (%s), sadece hook segment", e2)
                    shutil.copy(hook_seg, final_tmp)
        else:
            shutil.copy(hook_seg, final_tmp)

        # ── kalıcı konuma taşı ─────────────────────────────────────
        dest = OUTPUT_DIR / f"yacht_{uuid.uuid4().hex[:8]}.mp4"
        shutil.copy(final_tmp, dest)
        logger.info("Final video: %s (%d KB)", dest, dest.stat().st_size // 1024)
        return str(dest)
