"""NEXUS Studio Bild-Werkzeuge + Retusche (Seed 2026-10-29, ST09/ST10). Nur lokal, nur G:, 0 EUR.
Leitbild: Original bleibt unveraendert (neue Version), ehrliche Fehler, KI-Inhalte kennzeichnen."""
from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image
from PIL import Image, ImageDraw, ImageFilter, ImageOps
import cv2
import numpy as np


def tool(img: Image.Image, name: str, **p) -> Image.Image:
    """ST09: name in crop(box=(l,t,r,b)), rotate(deg), flip(h|v), resize(w,h), watermark(text), border(px).
    Gibt immer ein NEUES Bild zurueck. Original unveraendert."""

    # Klonen des Originals, um es unveraendert zu lassen
    result = img.copy()

    if name == "crop":
        box_p = p.get("box")
        if not isinstance(box_p, tuple) or len(box_p) != 4:
            raise ValueError("NOT_IMPLEMENTED:tool: crop needs valid box=(left,top,right,bottom)")
        l, t, r, b = box_p
        # Crop Box (left, top, right, bottom)
        result = result.crop((l, t, r, b))

    elif name == "rotate":
        deg_p = p.get("deg")
        if not isinstance(deg_p, int):
            raise ValueError("NOT_IMPLEMENTED:tool: rotate needs integer degrees")
        # expand=True ist notwendig, damit das Bild die neue groessere Hoehe/Breite annimmt
        result = result.rotate(deg_p, expand=True)

    elif name == "flip":
        mode_p = p.get("mode")
        if mode_p not in ("h", "v"):
            raise ValueError("NOT_IMPLEMENTED:tool: flip needs 'h' or 'v'")
        # Horizontal Flip (h) -> mirror, Vertical Flip (v) -> flip
        if mode_p == "h":
            result = ImageOps.mirror(result)
        else:
            result = ImageOps.flip(result)

    elif name == "resize":
        w_p, h_p = p.get("w"), p.get("h")
        if w_p is None or h_p is None:
            raise ValueError("NOT_IMPLEMENTED:tool: resize needs w and h")
        # Resize exakt auf diese Maasse (kein Thumbnail)
        result = result.resize((w_p, h_p), Image.LANCZOS)

    elif name == "watermark":
        text_p = p.get("text")
        if not isinstance(text_p, str):
            raise ValueError("NOT_IMPLEMENTED:tool: watermark needs text string")

        # Wasserzeichen auf einer Kopie des aktuellen result zeichnen (damit Original und Zwischenschritte sicher sind)
        # Da das Bild RGB ist, maehlen wir direkt darauf. Um es "on top" zu haben, kopieren wir erst.
        draw_img = result.copy()
        draw = ImageDraw.Draw(draw_img)

        # Text zentriert einfaecher: wir zeichnen den Text in der Mitte unten rechts als Default Positioning fuer Lesbarkeit
        # Aber um sicherzustellen es im Bild bleibt, nutzen wir eine einfache Zentrierung oder Unten-Rechts.
        # Da keine Position uebergeben wird: Zentriert ist meist sicherste "on top" Logik ohne Auslauf.
        font_size = min(draw_img.width // 10, draw_img.height // 2)
        bbox = draw.textbbox((0, 0), text_p)
        txt_width = bbox[2] - bbox[0]
        txt_height = bbox[3] - bbox[1]
        x_pos = (draw_img.width - txt_width) // 2
        y_pos = draw_img.height - txt_height - (draw_img.height * 0.1) # 10% Abstand unten

        draw.text((x_pos, y_pos), text_p, fill=(255, 255, 255)) # Weisses Text

        return draw_img

    elif name == "border":
        px_p = p.get("px")
        if not isinstance(px_p, int) or px_p < 0:
            raise ValueError("NOT_IMPLEMENTED:tool: border needs positive integer px")

        # ImageOps.expand fügt symmetrischen Rand hinzu (2*px Breite und Hoehe)
        result = ImageOps.expand(result, border=px_p, fill=(0, 0, 0))

    else:
        raise ValueError(f"NOT_IMPLEMENTED:tool: unknown name '{name}'")

    return result


def retouch(img: Image.Image, mask: Image.Image) -> Image.Image:
    """ST10: Flecken entfernen per cv2.inpaint (TELEA) im weissen Maskenbereich; neues Bild."""
    # Sicherstellen, dass Bilder nicht None sind und Dimensionen stimmen
    if img is None or mask is None:
        raise ValueError("retouch: Eingangsparameter 'img' oder 'mask' dürfen nicht None sein.")

    if img.size != mask.size:
        raise ValueError(f"retouch: Bildgröße {img.size} stimmt nicht mit Maske {mask.size} überein.")

    # Konvertiere zu Numpy Arrays. PIL Default ist RGB. Maske ist L (Graustufen).
    pil_img = img.convert("RGB")
    pil_mask = mask.convert("L")

    np_img = np.array(pil_img, dtype=np.uint8)   # Format: (H, W, 3) channels are R,G,B
    np_mask = np.array(pil_mask, dtype=np.uint8) # Format: (H, W) channels are 0 oder 255

    try:
        # cv2 erwartet BGR. Wir wandeln RGB zu BGR um und stellen sicher, dass Arrays kontigu sind.
        # swapaxes(-1, -2) tauscht die letzte Achse mit der vorletzten (W -> C, C -> W?? Nein).
        # np_img: H x W x 3. cv2 erwartet H x W x 3 (BGR).
        # PIL RGB: [0]=R, [1]=G, [2]=B. cv2 BGR: [0]=B, [1]=G, [2]=R.
        # swapaxes(-1, -2) ergibt H x 3 x W. Das ist falsch für den Farbraum-Tausch allein.
        # Korrektur: Wir brauchen nur channels zu tauschen: [H,W,3]->[H,W] mit umgekehrten R,G,B?
        # Nein, swapaxes(-1,-2) bei (H,W,3) macht H x 3 x W. Das ist unsinnig für das Farbraum-Problem.
        # OpenCV erwartet Array mit Form (h,w,c). 
        # Um RGB in BGR umzuwandeln: np_img[:, :, ::-1] oder swapaxes(-1, -2)??
        # swapaxes(-1, -2) auf (H,W,C) gibt (H,C,W). Das ist NICHT das, was wir wollen.
        # Wir wollen H x W x 3 (BGR). 
        # Der richtige Weg: np_img[:, :, ::-1] tauscht die Kanäle R,G,B? Nein, das tauscht R und B nur wenn C=2.
        # Bei RGB(0)=R, (1)=G, (2)=B. cv2 braucht (0)=B, (1)=G, (2)=R.
        # swapaxes(-1, -2) verwandelt (H,W,C) in (H,C,W). Das ist falsch.
        # Korrekte Umdrehung der Kanäle: np_img[:,:, ::-1] tauscht R(0) und B(2) nur? 
        # Nein, [::-1] kehrt den Kanal-Vektor um. [R,G,B] -> [B,G,R]. Das ist genau richtig für RGB zu BGR Umwandlung.

        np_img_bgr = np.ascontiguousarray(np_img[:,:, ::-1], dtype=np.uint8)

        sigma = 3.0
        inpaint_radius = 3 # Typische Größe

        result_cv2 = cv2.inpaint(np_img_bgr, np_mask.astype(np.uint8), sigma, cv2.INPAINT_TELEA)

        # Ergebnis wieder zu RGB umwandeln (BGR -> RGB)
        result_rgb = np.ascontiguousarray(result_cv2[:,:, ::-1], dtype=np.uint8) 

        return Image.fromarray(result_rgb, mode='RGB')

    except Exception as e:
        raise ValueError(f"retouch: Fehlschlag bei inpaint - {type(e).__name__}: {str(e)}") from None
