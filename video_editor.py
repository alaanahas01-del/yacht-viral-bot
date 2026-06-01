"""
Profesyonel dikey reel montajı (1080x1920).
Akış: (opsiyonel AI drone hero) -> Ken Burns'lü fotoğraf showcase (crossfade)
      -> "Bilgi için DM" end card. Arkada müzik + hook sesi + outro sesi (ducking).

Tüm adımlar düşük bellekli (her an tek/iki video), -threads 1, Railway 512MB uyumlu.
"""
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

W, H, FPS = 1080, 1920, 30

FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
FONT_REG_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
]

# Düşük bellek + tutarlı encode ayarları
X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-threads", "1"]
AAC = ["-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2"]

XFADE = 0.5          # crossfade süresi (sn)
PHOTO_DUR = 3.0      # her fotoğraf ekran süresi (xfade dahil)
ENDCARD_DUR = 3.5    # end card süresi
MAX_PHOTOS = 6       # tempo için kullanılacak max fotoğraf


def _font(cands):
    for f in cands:
        if Path(f).exists():
            return f
    return ""


def _prep_fonts(tmp: Path):
    """Fontları tmp'ye kopyala, bare (relative) isim döndür. Böylece filtergraph'ta
    Windows 'C:' kolonu sorunu olmaz; cwd=tmp ile ffmpeg relative çözer."""
    bold = _font(FONT_BOLD_CANDIDATES)
    reg = _font(FONT_REG_CANDIDATES)
    bold_rel = reg_rel = ""
    if bold:
        shutil.copy(bold, tmp / "font_bold.ttf"); bold_rel = "font_bold.ttf"
    if reg:
        shutil.copy(reg, tmp / "font_reg.ttf"); reg_rel = "font_reg.ttf"
    return bold_rel, reg_rel


def _run(cmd: list, label: str, cwd=None):
    logger.info("FFmpeg [%s] başlıyor", label)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if r.returncode != 0:
        err = (r.stderr or "")[-3500:]
        logger.error("FFmpeg [%s] rc=%d:\n%s", label, r.returncode, err)
        hint = " (OOM/bellek)" if r.returncode == -9 else ""
        raise RuntimeError(f"FFmpeg [{label}] rc={r.returncode}{hint}: ...{(r.stderr or '')[-400:]}")
    logger.info("FFmpeg [%s] tamam", label)


# ── 1. Fotoğraf hazırlığı: EXIF düzelt + sinematik grade ────────────

def _prep_photo(photo_bytes: bytes, out_path: Path):
    """EXIF döndürmesini düzelt, hafif premium renk gradesi uygula, KB için büyük canvas üret."""
    from PIL import Image, ImageOps, ImageEnhance
    img = Image.open(io.BytesIO(photo_bytes))
    img = ImageOps.exif_transpose(img)        # ← yan dönme bug'ını çözer
    img = img.convert("RGB")

    # Premium grade (hafif): renk + kontrast + parlaklık
    img = ImageEnhance.Color(img).enhance(1.14)
    img = ImageEnhance.Contrast(img).enhance(1.07)
    img = ImageEnhance.Brightness(img).enhance(1.03)
    img = ImageEnhance.Sharpness(img).enhance(1.15)

    # Ken Burns için 1.5x canvas'a cover et (zoom payı)
    cw, ch = int(W * 1.5), int(H * 1.5)
    img = ImageOps.fit(img, (cw, ch), method=Image.LANCZOS)
    img.save(out_path, format="JPEG", quality=90)


# ── 2. Ken Burns klip (tek fotoğraf -> hareketli video) ─────────────

def _kb_clip(photo_path: Path, out_path: Path, dur: float, zoom_in: bool):
    """Merkezli yavaş zoom (jitter'sız). zoom_in=True içeri, False dışarı."""
    frames = int(dur * FPS)
    if zoom_in:
        z = f"1.0+{0.12/frames:.6f}*on"
    else:
        z = f"1.12-{0.12/frames:.6f}*on"
    vf = (
        f"scale={int(W*1.5)}:{int(H*1.5)}:force_original_aspect_ratio=increase,"
        f"crop={int(W*1.5)}:{int(H*1.5)},"
        f"zoompan=z='{z}':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={W}x{H}:fps={FPS},"
        f"vignette=PI/5,"
        f"setsar=1,format=yuv420p"
    )
    _run([
        "ffmpeg", "-y", "-loop", "1", "-t", f"{dur}", "-i", str(photo_path),
        "-vf", vf, *X264, "-an", "-r", str(FPS), str(out_path)
    ], f"kb-{out_path.stem}")


# ── 3. Drone klibini reel formatına getir ───────────────────────────

def _conform_clip(in_path: Path, out_path: Path, dur: float):
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"vignette=PI/5,setsar=1,fps={FPS},format=yuv420p")
    _run([
        "ffmpeg", "-y", "-i", str(in_path), "-vf", vf,
        *X264, "-an", "-t", f"{dur}", "-r", str(FPS), str(out_path)
    ], f"conform-{out_path.stem}")


# ── 4. End card: "Bilgi için DM" ────────────────────────────────────

def _endcard(hero_path: Path, out_path: Path, contact: str, dur: float,
             font_bold: str, font_reg: str, cwd: Path):
    big = "BILGI ICIN DM"
    drawbig = ""
    if font_bold:
        drawbig = (
            f",drawtext=text='{big}':fontfile={font_bold}:fontsize=92:fontcolor=white"
            f":x=(w-text_w)/2:y=h*0.42"
            f":shadowcolor=black@0.6:shadowx=3:shadowy=3"
        )
    drawcontact = ""
    if font_reg and contact:
        safe_contact = contact.replace(":", "").replace("'", "")
        drawcontact = (
            f",drawtext=text='{safe_contact}':fontfile={font_reg}:fontsize=52:fontcolor=white"
            f":x=(w-text_w)/2:y=h*0.52:shadowcolor=black@0.6:shadowx=2:shadowy=2"
        )
    # koyu + bulanık hero arka plan + metin
    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
        f"boxblur=24:2,eq=brightness=-0.32:saturation=0.9,setsar=1,fps={FPS},format=yuv420p"
        f"{drawbig}{drawcontact}"
    )
    _run([
        "ffmpeg", "-y", "-loop", "1", "-t", f"{dur}", "-i", str(hero_path),
        "-vf", vf, *X264, "-an", "-r", str(FPS), str(out_path)
    ], "endcard", cwd=str(cwd))


# ── 5. Klipleri crossfade ile zincirle (pairwise, düşük bellek) ─────

def _xfade_chain(clips: list, durs: list, out_path: Path, tmp: Path):
    """clips[i] süresi durs[i]. Pairwise xfade -> düşük bellek (2 decoder)."""
    if len(clips) == 1:
        shutil.copy(clips[0], out_path)
        return durs[0]

    acc = clips[0]
    acc_dur = durs[0]
    for i in range(1, len(clips)):
        nxt = clips[i]
        offset = max(0.0, acc_dur - XFADE)
        step_out = out_path if i == len(clips) - 1 else (tmp / f"acc_{i}.mp4")
        _run([
            "ffmpeg", "-y", "-i", str(acc), "-i", str(nxt),
            "-filter_complex",
            f"[0:v][1:v]xfade=transition=fade:duration={XFADE}:offset={offset:.3f},"
            f"format=yuv420p[v]",
            "-map", "[v]", *X264, "-an", str(step_out)
        ], f"xfade-{i}")
        acc = step_out
        acc_dur = acc_dur + durs[i] - XFADE
    return acc_dur


# ── 6. Final ses miksi + animasyonlu hook metni ─────────────────────

def _wrap(text: str, width: int = 17) -> str:
    clean = " ".join(text.split())[:90]
    return "\\n".join(textwrap.wrap(clean, width=width)[:4])


def _final_mix(video_path: Path, total_dur: float, hook_text: str,
               music_path, hook_voice_path, outro_voice_path,
               outro_start: float, out_path: Path, tmp: Path, font_bold: str):
    # Hook metni: ilk 3 sn fade in/out
    hook_vf = ""
    if font_bold and hook_text:
        (tmp / "hook.txt").write_text(_wrap(hook_text).replace("\\n", "\n"), encoding="utf-8")
        a = ("if(lt(t,0.3),0,if(lt(t,0.8),(t-0.3)/0.5,"
             "if(lt(t,2.6),1,if(lt(t,3.1),(3.1-t)/0.5,0))))")
        hook_vf = (
            f"drawtext=textfile=hook.txt:fontfile={font_bold}"
            f":fontsize=64:fontcolor=white:line_spacing=12"
            f":x=(w-text_w)/2:y=h*0.70:box=1:boxcolor=black@0.35:boxborderw=26"
            f":shadowcolor=black@0.8:shadowx=2:shadowy=2"
            f":alpha='{a}':enable='between(t,0.3,3.1)'"
        )

    # Ses kaynaklarını topla
    inputs = ["-i", str(video_path)]
    amix_parts = []
    idx = 1
    if music_path and Path(music_path).exists():
        inputs += ["-i", str(music_path)]
        amix_parts.append(f"[{idx}:a]volume=0.28,afade=t=out:st={total_dur-1.5:.2f}:d=1.5[m]")
        music_lbl = "[m]"
        idx += 1
    else:
        music_lbl = None
    if hook_voice_path and Path(hook_voice_path).exists():
        inputs += ["-i", str(hook_voice_path)]
        amix_parts.append(f"[{idx}:a]adelay=200|200,volume=1.25[hv]")
        hv_lbl = "[hv]"
        idx += 1
    else:
        hv_lbl = None
    if outro_voice_path and Path(outro_voice_path).exists():
        inputs += ["-i", str(outro_voice_path)]
        d = int(max(0, outro_start) * 1000)
        amix_parts.append(f"[{idx}:a]adelay={d}|{d},volume=1.3[ov]")
        ov_lbl = "[ov]"
        idx += 1
    else:
        ov_lbl = None

    labels = [l for l in (music_lbl, hv_lbl, ov_lbl) if l]

    filt = ""
    if hook_vf:
        filt += f"[0:v]{hook_vf}[v];"
    else:
        filt += "[0:v]null[v];"

    maps = ["-map", "[v]"]
    if labels:
        filt += ";".join(amix_parts) + ";"
        filt += "".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=first[a]"
        maps += ["-map", "[a]"]
        acodec = AAC
    else:
        acodec = []

    _run([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filt.rstrip(";"),
        *maps, *X264, *acodec,
        "-t", f"{total_dur:.2f}", "-movflags", "+faststart",
        str(out_path)
    ], "final-mix", cwd=str(tmp))


# ── ANA FONKSİYON ───────────────────────────────────────────────────

def assemble_final_video(
    photos_bytes: list,
    hook_text: str,
    music_bytes: bytes = None,
    hook_voice_bytes: bytes = None,
    outro_voice_bytes: bytes = None,
    drone_video_url: str = "",
    contact: str = "",
) -> str:
    """
    Profesyonel reel üretir. Sıra:
      [drone hero (varsa)] -> KB fotoğraflar (crossfade) -> end card
      + müzik + hook sesi + outro sesi.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        font_bold, font_reg = _prep_fonts(tmp)

        photos = photos_bytes[:MAX_PHOTOS] if photos_bytes else []
        if not photos:
            raise ValueError("Fotoğraf yok")

        # 1. Fotoğrafları hazırla (EXIF + grade)
        prepped = []
        for i, pb in enumerate(photos):
            p = tmp / f"prep_{i}.jpg"
            try:
                _prep_photo(pb, p)
            except Exception as e:
                logger.warning("prep_photo hata (%s), ham yazılıyor", e)
                p.write_bytes(pb)
            prepped.append(p)

        hero = prepped[0]

        clips, durs = [], []

        # 2. Drone hero (varsa) en başa
        if drone_video_url:
            try:
                dp = tmp / "drone_src.mp4"
                r = requests.get(drone_video_url, timeout=180)
                r.raise_for_status()
                dp.write_bytes(r.content)
                dc = tmp / "clip_drone.mp4"
                _conform_clip(dp, dc, dur=5.0)
                clips.append(dc); durs.append(5.0)
            except Exception as e:
                logger.warning("Drone hero eklenemedi (%s)", e)

        # 3. Ken Burns klipler
        for i, p in enumerate(prepped):
            c = tmp / f"clip_{i}.mp4"
            _kb_clip(p, c, PHOTO_DUR, zoom_in=(i % 2 == 0))
            clips.append(c); durs.append(PHOTO_DUR)

        # 4. End card
        ec = tmp / "clip_endcard.mp4"
        _endcard(hero, ec, contact, ENDCARD_DUR, font_bold, font_reg, tmp)
        clips.append(ec); durs.append(ENDCARD_DUR)

        # 5. Crossfade zinciri
        composed = tmp / "composed.mp4"
        total = _xfade_chain(clips, durs, composed, tmp)

        # 6. Müzik / sesleri yaz
        def _w(b, name):
            if not b:
                return None
            f = tmp / name
            f.write_bytes(b)
            return f
        music_p = _w(music_bytes, "music.mp3")
        hookv_p = _w(hook_voice_bytes, "hookv.mp3")
        outrov_p = _w(outro_voice_bytes, "outrov.mp3")
        outro_start = max(0.0, total - ENDCARD_DUR + 0.4)

        # 7. Final miks
        final_tmp = tmp / "final.mp4"
        try:
            _final_mix(composed, total, hook_text, music_p, hookv_p, outrov_p,
                       outro_start, final_tmp, tmp, font_bold)
        except Exception as e:
            logger.warning("Final miks hata (%s), sade kompozisyon kullanılıyor", e)
            shutil.copy(composed, final_tmp)

        dest = OUTPUT_DIR / f"yacht_{uuid.uuid4().hex[:8]}.mp4"
        shutil.copy(final_tmp, dest)
        logger.info("Final video: %s (%.1f sn, %d KB)", dest, total, dest.stat().st_size // 1024)
        return str(dest)
