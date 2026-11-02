"""NEXUS Studio FX (2026-10-28, ST02-ST04): Filter-Galerie, manuelle Farbanpassung + Histogramm, Social-/Zitat-Generator.
Lokal (Pillow/OpenCV/numpy). Originale bleiben unveraendert; Speichern legt immer eine neue Version an."""
from __future__ import annotations

import base64
import io
import textwrap
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

FILTERS = ("bw", "vintage", "warm", "cool", "contrast", "hdr", "sharpen", "soften", "vignette")
FILTER_LABELS = {"bw": "S/W", "vintage": "Vintage", "warm": "Warm", "cool": "Kalt", "contrast": "Kontrast",
                 "hdr": "HDR-Look", "sharpen": "Schaerfen", "soften": "Weichzeichnen", "vignette": "Vignette"}
ADJUST_KEYS = ("exposure", "contrast", "saturation", "temperature", "tint", "highlights", "shadows")  # je -100..100
FORMATS = {"insta_post": (1080, 1080), "insta_story": (1080, 1920), "yt_thumb": (1280, 720), "fb_post": (1200, 630)}
TEMPLATES = {"clean": {"fg": "#ffffff", "shade": 0.45, "grad": ("#1b2a4a", "#5b3a8c")},
             "warm": {"fg": "#fff6e8", "shade": 0.35, "grad": ("#7a2e1d", "#e0913f")},
             "mint": {"fg": "#0f2a26", "shade": 0.0, "grad": ("#b8f2e6", "#5ec2b7")},
             "night": {"fg": "#e8f0ff", "shade": 0.55, "grad": ("#05070d", "#1d3557")}}
FONT_DIRS = (Path(r"C:\Windows\Fonts"),)  # nur lesen
FONTS = {"sans": ("segoeuib.ttf", "arialbd.ttf"), "serif": ("georgiab.ttf", "timesbd.ttf"), "mono": ("consolab.ttf", "courbd.ttf")}


def _bgr(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2BGR)


def _pil(a: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(np.clip(a, 0, 255).astype(np.uint8), cv2.COLOR_BGR2RGB))


def _vignette(a, strength=0.55):
    h, w = a.shape[:2]
    k = cv2.getGaussianKernel(w, w * 0.55) @ cv2.getGaussianKernel(h, h * 0.55).T
    m = (k.T / k.max())
    m = 1 - strength + strength * m
    return a.astype(np.float32) * m[..., None]


def apply_filter(img: Image.Image, name: str) -> Image.Image:
    if name not in FILTERS:
        raise ValueError("UNKNOWN_FILTER")
    a = _bgr(img).astype(np.float32)
    if name == "bw":
        g = cv2.cvtColor(a.astype(np.uint8), cv2.COLOR_BGR2GRAY)
        out = cv2.cvtColor(cv2.equalizeHist(g), cv2.COLOR_GRAY2BGR) * 0.35 + cv2.cvtColor(g, cv2.COLOR_GRAY2BGR) * 0.65
    elif name == "vintage":
        sep = np.array([[0.131, 0.534, 0.272], [0.168, 0.686, 0.349], [0.189, 0.769, 0.393]], np.float32)  # BGR
        out = a @ sep.T * 0.85 + a * 0.15
        out = _vignette(out * 0.92 + 18, 0.4)
    elif name == "warm":
        out = a + np.array([-14, 2, 18], np.float32)
    elif name == "cool":
        out = a + np.array([18, 2, -12], np.float32)
    elif name == "contrast":
        out = (a - 128) * 1.3 + 128
    elif name == "hdr":
        lab = cv2.cvtColor(a.astype(np.uint8), cv2.COLOR_BGR2LAB)
        l, x, y = cv2.split(lab)
        l = cv2.createCLAHE(3.0, (8, 8)).apply(l)
        o = cv2.cvtColor(cv2.merge((l, x, y)), cv2.COLOR_LAB2BGR).astype(np.float32)
        out = cv2.addWeighted(o, 1.5, cv2.GaussianBlur(o, (0, 0), 3), -0.5, 0)
    elif name == "sharpen":
        out = cv2.addWeighted(a, 1.7, cv2.GaussianBlur(a, (0, 0), 1.6), -0.7, 0)
    elif name == "soften":
        out = cv2.GaussianBlur(a, (0, 0), 2.2) * 0.7 + a * 0.3
    else:
        out = _vignette(a, 0.6)
    return _pil(out)


def adjust(img: Image.Image, p: dict | None) -> Image.Image:
    """Werte -100..100; alle 0 -> Bild unveraendert."""
    p = {k: max(-100.0, min(100.0, float((p or {}).get(k) or 0))) for k in ADJUST_KEYS}
    if not any(p.values()):
        return img.convert("RGB").copy()
    a = _bgr(img).astype(np.float32) / 255.0
    if p["exposure"]:
        a = a * (2 ** (p["exposure"] / 50.0))
    if p["contrast"]:
        a = (a - 0.5) * (1 + p["contrast"] / 100.0) + 0.5
    lum = (a @ np.array([0.114, 0.587, 0.299], np.float32))[..., None]
    if p["highlights"]:
        a = a + (p["highlights"] / 250.0) * np.clip((lum - 0.5) * 2, 0, 1)
    if p["shadows"]:
        a = a + (p["shadows"] / 250.0) * np.clip((0.5 - lum) * 2, 0, 1)
    if p["temperature"]:
        t = p["temperature"] / 400.0
        a = a + np.array([-t, 0, t], np.float32)
    if p["tint"]:
        t = p["tint"] / 400.0
        a = a + np.array([t / 2, -t, t / 2], np.float32)
    if p["saturation"]:
        g = (a @ np.array([0.114, 0.587, 0.299], np.float32))[..., None]
        a = g + (a - g) * (1 + p["saturation"] / 100.0)
    return _pil(a * 255.0)


def histogram(img: Image.Image) -> dict:
    a = np.asarray(img.convert("RGB"))
    return {c: np.bincount(a[..., i].ravel(), minlength=256).tolist() for i, c in enumerate("rgb")}


def preview_b64(img: Image.Image, max_side: int = 900) -> str:
    im = img.copy(); im.thumbnail((max_side, max_side))
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _font(family: str, size: int):
    for fn in FONTS.get(family, FONTS["sans"]):
        for d in FONT_DIRS:
            if (d / fn).exists():
                return ImageFont.truetype(str(d / fn), size)
    return ImageFont.load_default(size)


def _hex(c: str):
    c = c.lstrip("#"); return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def gradient(size, c1, c2) -> Image.Image:
    w, h = size
    t = np.linspace(0, 1, h)[:, None, None] * 0.7 + np.linspace(0, 1, w)[None, :, None] * 0.3
    a = np.array(_hex(c1), np.float32) * (1 - t) + np.array(_hex(c2), np.float32) * t
    return Image.fromarray(a.astype(np.uint8), "RGB")


def social(text: str, fmt: str = "insta_post", template: str = "clean", font: str = "serif", bg_image: Image.Image | None = None,
           position: str = "center", author: str = "", watermark: str = "", logo: Image.Image | None = None) -> Image.Image:
    if fmt not in FORMATS:
        raise ValueError("UNKNOWN_FORMAT")
    W, H = FORMATS[fmt]
    tp = TEMPLATES.get(template, TEMPLATES["clean"])
    if bg_image is not None:
        canvas = ImageOps.fit(bg_image.convert("RGB"), (W, H), Image.LANCZOS)
        if tp["shade"]:
            canvas = Image.blend(canvas, Image.new("RGB", (W, H), (0, 0, 0)), tp["shade"])
    else:
        canvas = gradient((W, H), *tp["grad"])
    d = ImageDraw.Draw(canvas)
    size = int(min(W, H) * (0.085 if len(text) < 60 else 0.062 if len(text) < 140 else 0.048))
    f = _font(font, size)
    wrap = max(10, int(W * 0.8 / (size * 0.52)))
    lines = textwrap.wrap(str(text).strip() or " ", wrap)
    lh = int(size * 1.25)
    block = lh * len(lines)
    y0 = {"top": int(H * 0.12), "bottom": int(H * 0.88) - block}.get(position, (H - block) // 2)
    fg = _hex(tp["fg"])
    for i, ln in enumerate(lines):
        tw = d.textlength(ln, font=f)
        x, y = (W - tw) / 2, y0 + i * lh
        d.text((x + 2, y + 3), ln, font=f, fill=(0, 0, 0) if sum(fg) > 380 else (255, 255, 255))
        d.text((x, y), ln, font=f, fill=fg)
    if author:
        fa = _font("sans", int(size * 0.5)); s = "- " + author
        d.text(((W - d.textlength(s, font=fa)) / 2, y0 + block + int(size * 0.4)), s, font=fa, fill=fg)
    if watermark:
        fw = _font("sans", max(16, int(min(W, H) * 0.025)))
        d.text((W - d.textlength(watermark, font=fw) - 24, H - int(min(W, H) * 0.025) - 28), watermark, font=fw, fill=fg + (160,) if False else fg)
    if logo is not None:
        lg = logo.convert("RGBA"); lg.thumbnail((int(W * 0.14), int(W * 0.14)))
        canvas.paste(lg, (24, H - lg.height - 24), lg)
    return canvas


# ---------------------------------------------------------------- Studio-Anbindung (neue Version, Original unveraendert)
def _load(studio, iid: str) -> Image.Image:
    it = studio.get(iid)
    with Image.open(studio.base / "originals" / it["copy"]) as im:
        img = ImageOps.exif_transpose(im).convert("RGB")
    if max(img.size) > 3000:
        img.thumbnail((3000, 3000))
    return img


def save_version(studio, iid: str, img: Image.Image, op: str, params: dict | None = None, by: str = "NEXUS") -> dict:
    from nexus.studio.core import sha256_file, _live
    it = studio.get(iid)
    with studio._lock:
        n = len(it["versions"]) + 1
        name = f"{iid}_v{n}_{op}.jpg"
        fp = studio.base / "versions" / name
        img.convert("RGB").save(fp, "JPEG", quality=92)
        ver = {"n": n, "op": op, "file": name, "by": by, "ts": datetime.now().isoformat(timespec="seconds"),
               "sha256": sha256_file(fp), **({"params": params} if params else {})}
        it["versions"].append(ver)
        studio._save()
    studio.audit("edit", {"id": iid, **ver})
    _live("NEXUS", "studio", f"Neue Version {name} ({op}) - Original unveraendert", ok=True)
    return ver


def studio_filter(studio, iid: str, name: str, preview: bool = False) -> dict:
    img = _load(studio, iid)
    if preview:
        img.thumbnail((900, 900))
        return {"preview": preview_b64(apply_filter(img, name))}
    return {"version": save_version(studio, iid, apply_filter(img, name), "filter_" + name)}


def studio_adjust(studio, iid: str, params: dict, preview: bool = False) -> dict:
    img = _load(studio, iid)
    if preview:
        img.thumbnail((900, 900))
        out = adjust(img, params)
        return {"preview": preview_b64(out), "histogram": histogram(out)}
    clean = {k: float(params.get(k) or 0) for k in ADJUST_KEYS if params.get(k)}
    return {"version": save_version(studio, iid, adjust(img, params), "adjust", clean)}


def studio_social(studio, b: dict) -> dict:
    from nexus.studio.core import _live
    bg = _load(studio, str(b["bg_id"])) if b.get("bg_id") else None
    img = social(str(b.get("text") or ""), str(b.get("format") or "insta_post"), str(b.get("template") or "clean"),
                 str(b.get("font") or "serif"), bg, str(b.get("position") or "center"), str(b.get("author") or ""),
                 str(b.get("watermark") or ""))
    if b.get("preview"):
        return {"preview": preview_b64(img, 700), "size": list(img.size)}
    out_dir = studio.base / "social"; out_dir.mkdir(parents=True, exist_ok=True)
    name = f"social_{b.get('format') or 'insta_post'}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
    img.save(out_dir / name, "PNG")
    studio.audit("social", {"file": name, "format": b.get("format"), "bg_id": b.get("bg_id")})
    _live("NEXUS", "studio", f"Social-Grafik {name} erstellt", ok=True)
    return {"file": name, "size": list(img.size)}
