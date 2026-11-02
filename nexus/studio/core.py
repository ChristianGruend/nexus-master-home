"""NEXUS Studio Kern: Import (Kopie+Hash), Kategorisierung (lokal llava), Qualitaet, dHash-Dubletten,
Suche, klassische Bearbeitung (OpenCV/PIL) als neue Versionen, Ordner-Vorschlag mit Freigabe.

Originale werden nie veraendert: Quelle wird nur gelesen, Bibliothekskopie ist schreibgeschuetzt,
sha256 von Quelle und Kopie wird gespeichert und ist jederzeit pruefbar (verify).
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import stat
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from nexus.common.paths import ROOT

STUDIO_DIR = ROOT / "workspace" / "studio"
TAXONOMY_FILE = ROOT / "workspace" / "projects" / "hermes" / "studio_categories.json"  # von Hermes erstellt
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
ALLOWED_SOURCE_ROOTS = ("G:\\",)  # Import nur von G:
FALLBACK_CATS = ["Tierfotografie", "Kinder & Familie", "Industrie", "Natur & Landschaft",
                 "Stadt & Architektur", "Makro & Details", "Screenshots & Dokumente", "Sonstiges"]
SENSITIVE = re.compile(r"(?i)\b(name|ethni|religi|krank|behinder|nackt|nude|race|rasse|sexual|erotik|erotisch|penis|tattoo|t[aä]towier|gesund|alter|herkunft)\w*")


def _live(agent, kind, text, **kw):
    try:
        from nexus.common.live_events import emit
        emit(agent, kind, text, **kw)
    except Exception:
        pass


class StudioError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code, self.status = code, status


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dhash(img, size: int = 8) -> str:
    g = img.convert("L").resize((size + 1, size), 1)
    px = list(g.tobytes())
    bits = 0
    for r in range(size):
        for c in range(size):
            bits = (bits << 1) | (px[r * (size + 1) + c] > px[r * (size + 1) + c + 1])
    return f"{bits:016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def taxonomy() -> dict:
    try:
        t = json.loads(TAXONOMY_FILE.read_text(encoding="utf-8"))
        if isinstance(t.get("categories"), dict) and t["categories"]:
            return {"source": "hermes", **t}
    except Exception:
        pass
    return {"source": "fallback", "categories": {c: {"sub": [], "folder": c} for c in FALLBACK_CATS}}


class Studio:
    def __init__(self, base: Path | None = None):
        self.base = Path(base or STUDIO_DIR)
        for d in ("originals", "versions", "thumbs", "sortiert"):
            (self.base / d).mkdir(parents=True, exist_ok=True)
        self.index_file = self.base / "index.json"
        self.audit_file = self.base / "audit.jsonl"
        self._lock = threading.RLock()
        self._pending: dict[str, dict] = {}
        self.items: dict[str, dict] = self._load()

    # ------------------------------------------------------------ Persistenz
    def _load(self) -> dict:
        try:
            data = json.loads(self.index_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except ValueError:
            bad = self.index_file.with_suffix(f".corrupt_{int(time.time())}")
            os.replace(self.index_file, bad)
            self.audit("index_corrupt", {"moved_to": str(bad)})
            return {}

    def _save(self) -> None:
        tmp = self.index_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.index_file)

    def audit(self, action: str, data: dict) -> None:
        with open(self.audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "action": action, **data},
                               ensure_ascii=False) + "\n")

    def get(self, iid: str) -> dict:
        it = self.items.get(str(iid))
        if not it:
            raise StudioError("NOT_FOUND", 404)
        return it

    # ------------------------------------------------------------ Import
    def import_files(self, paths: list[str], limit: int = 200) -> dict:
        from PIL import Image, ImageOps
        done, skipped, errors = [], [], []
        for raw in list(paths)[:limit]:
            try:
                src = Path(str(raw)).resolve()
                if not str(src).upper().startswith(ALLOWED_SOURCE_ROOTS):
                    raise StudioError("SOURCE_NOT_ALLOWED", 403)
                if src.name.startswith(".trashed"):
                    raise StudioError("TRASHED_FILE_SKIPPED", 403)
                if src.suffix.lower() not in IMG_EXT or not src.is_file():
                    raise StudioError("NOT_AN_IMAGE")
                if str(src).lower().startswith(str(self.base).lower()):
                    raise StudioError("SOURCE_INSIDE_LIBRARY", 403)
                h = sha256_file(src)
                iid = h[:16]
                if iid in self.items:
                    skipped.append({"path": str(src), "id": iid, "reason": "exakte_dublette_bereits_importiert"})
                    continue
                dst = self.base / "originals" / f"{iid}{src.suffix.lower()}"
                shutil.copy2(src, dst)
                if sha256_file(dst) != h:
                    dst.unlink(missing_ok=True)
                    raise StudioError("COPY_HASH_MISMATCH", 500)
                os.chmod(dst, stat.S_IREAD)
                with Image.open(dst) as im:
                    exif = im.getexif()
                    taken = exif.get(36867) or (exif.get_ifd(0x8769) or {}).get(36867) or exif.get(306)
                    im2 = ImageOps.exif_transpose(im).convert("RGB")
                    w, hgt = im2.size
                    th = im2.copy()
                    th.thumbnail((420, 420))
                    th.save(self.base / "thumbs" / f"{iid}.jpg", quality=82)
                    dh = dhash(im2)
                    q = self._quality(im2)
                try:
                    date = datetime.strptime(str(taken)[:19], "%Y:%m:%d %H:%M:%S").date().isoformat() if taken else None
                except ValueError:
                    date = None
                date = date or datetime.fromtimestamp(src.stat().st_mtime).date().isoformat()
                it = {"id": iid, "name": src.name, "source": str(src), "source_sha256": h, "copy": dst.name,
                      "copy_sha256": h, "width": w, "height": hgt, "date": date, "dhash": dh, "quality": q,
                      "category": None, "subcategory": None, "keywords": [], "description": "",
                      "imported": datetime.now().isoformat(timespec="seconds"), "versions": []}
                with self._lock:
                    self.items[iid] = it
                    self._save()
                self.audit("import", {"id": iid, "source": str(src), "sha256": h})
                done.append(iid)
            except StudioError as e:
                errors.append({"path": str(raw), "error": e.code})
            except Exception as e:
                errors.append({"path": str(raw), "error": type(e).__name__})
        _live("NEXUS", "studio", f"Import: {len(done)} neu, {len(skipped)} uebersprungen, {len(errors)} Fehler",
              ok=not errors)
        return {"success": True, "imported": done, "skipped": skipped, "errors": errors}

    @staticmethod
    def _quality(img) -> dict:
        import cv2
        import numpy as np
        a = np.asarray(img.convert("L").resize((min(1024, img.width), int(img.height * min(1024, img.width) / img.width))))
        sharp = float(cv2.Laplacian(a, cv2.CV_64F).var())
        bright = float(a.mean())
        contrast = float(a.std())
        mp = img.width * img.height / 1e6
        score = min(40, sharp / 10) + (30 - min(30, abs(bright - 120) / 3)) + min(15, contrast / 4) + min(15, mp * 1.5)
        return {"score": round(score), "sharpness": round(sharp, 1), "brightness": round(bright, 1),
                "contrast": round(contrast, 1), "megapixel": round(mp, 1)}

    # ------------------------------------------------------------ Kategorisierung (lokal)
    def categorize(self, iid: str, model: str = "llava:latest") -> dict:
        it = self.get(iid)
        tax = taxonomy()
        cats = list(tax["categories"])
        subs = {c: v.get("sub", []) for c, v in tax["categories"].items()}
        thumb = (self.base / "thumbs" / f"{iid}.jpg").read_bytes()
        prompt = ("Look at this photo. What is the MAIN subject? Choose exactly one main value: "
                  "animal, children_family, industry, nature_landscape, city_architecture, macro_closeup, "
                  "screenshot_document, other. If an animal (dog, cat, horse, bird...) is clearly visible, choose animal. "
                  "Machines, factories, technical equipment, vehicles, construction, computers -> industry. "
                  "Answer ONLY JSON: {\"main\": \"...\", \"subject\": \"one or two English words, e.g. dog\", "
                  "\"keywords\": [\"5 to 8 short German keywords\"], \"description\": \"one short German sentence, max 15 words\"}. "
                  "Describe only image content. Never identify people, never mention names, age, origin, body features, "
                  "tattoos or other sensitive attributes.")
        MAIN = {"animal": "Tierfotografie", "children_family": "Kinder & Familie", "industry": "Industrie",
                "nature_landscape": "Natur & Landschaft", "city_architecture": "Stadt & Architektur",
                "macro_closeup": "Makro & Details", "screenshot_document": "Screenshots & Dokumente", "other": "Sonstiges"}
        SUBMAP = {"dog": "Hunde", "puppy": "Hunde", "cat": "Katzen", "horse": "Pferde", "bird": "Voegel",
                  "insect": "Insekten", "machine": "Maschinen", "computer": "Maschinen", "vehicle": "Fahrzeuge",
                  "car": "Fahrzeuge", "truck": "Fahrzeuge", "factory": "Anlagen", "sky": "Himmel", "sunset": "Himmel",
                  "forest": "Waelder", "tree": "Waelder", "flower": "Blumen", "plant": "Pflanzen", "lake": "Seen & Berge",
                  "mountain": "Seen & Berge", "building": "Gebaeude", "street": "Strassen", "child": "Kinder",
                  "family": "Familie", "screenshot": "Screenshots", "document": "Dokumente", "food": "Essen"}
        body = {"model": model, "prompt": prompt, "images": [base64.b64encode(thumb).decode()],
                "format": "json", "stream": False, "keep_alive": "2m", "options": {"temperature": 0, "num_ctx": 2048}}
        t0 = time.time()
        req = urllib.request.Request("http://127.0.0.1:11434/api/generate", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        res = None
        try:
            for attempt in range(2):  # llava liefert gelegentlich abgeschnittenes JSON -> 1 Wiederholung
                with urllib.request.urlopen(req, timeout=240) as r:
                    raw = json.loads(r.read().decode("utf-8")).get("response", "")
                m = re.search(r"\{.*\}", raw, re.S)
                try:
                    res = json.loads(m.group(0) if m else raw)
                    break
                except ValueError:
                    if attempt:
                        raise
        except Exception as e:
            _live("NEXUS", "studio", f"Kategorisierung {iid} fehlgeschlagen: {type(e).__name__}", ok=False)
            raise StudioError("VISION_FAILED:" + type(e).__name__, 503)
        main = str(res.get("main") or "").strip().lower()
        match = MAIN.get(main) or next((v for k, v in MAIN.items() if main and (k in main or main in k)), None) or "Sonstiges"
        if match not in cats:
            match = "Sonstiges" if "Sonstiges" in cats else cats[-1]
        subject_raw = str(res.get("subject") or "").strip()
        bad_phrases = ["one or two", "5 to 8", "short german keywords", "english words"]
        if any(ph in subject_raw for ph in bad_phrases):
            subject = ""
        elif subject_raw:
            subject = subject_raw.lower()
        sub = next((v for k, v in SUBMAP.items() if k in subject and v in subs.get(match, [])), None) or \
            next((v for k, v in SUBMAP.items() if k in subject), None) or (subs.get(match) or [None])[0]
        # Regel-Nachkorrektur (llava 7B waehlt bei Tier im Gruenen oft "nature")
        ANIMAL = ("dog", "puppy", "cat", "kitten", "horse", "bird", "cow", "sheep", "insect", "animal", "deer", "fox")
        INDUSTRY = ("construction", "excavat", "machine", "factory", "crane", "truck", "industr", "engine", "tractor", "pipe")
        if any(a in subject for a in ANIMAL) and match not in ("Screenshots & Dokumente",):
            match = "Tierfotografie"
        elif any(a in subject for a in INDUSTRY):
            match = "Industrie"
        sub = next((v for k, v in SUBMAP.items() if k in subject and v in subs.get(match, [])), None) or \
            (subs.get(match) or [None])[0]
        bad = ("keyword", "german", "english", "one or two", "nature_landscape", "short")
        res["keywords"] = [k for k in (res.get("keywords") or []) if not any(b in str(k).lower() for b in bad)]
        if any(b in str(res.get("description") or "").lower() for b in bad):
            res["description"] = ""
        if SENSITIVE.search(subject + " " + str(res.get("description") or "")) and re.search(
                r"(?i)penis|nude|naked|nackt|underwear|erot|sexual|genital|breast", subject + " " + str(res.get("description") or "")):
            match, sub, res["keywords"], res["description"] = "Privat (gesperrt)", None, [], ""
            subject = "privat"
            self._blur_thumb(iid)
        kw = [str(k)[:30] for k in (res.get("keywords") or []) if isinstance(k, (str, int)) and not SENSITIVE.search(str(k))][:10]
        desc = SENSITIVE.sub("…", str(res.get("description") or ""))[:160]
        with self._lock:
            it.update(category=match, subcategory=sub, subject=subject[:40], keywords=kw, description=desc, vision_model=model)
            self._save()
        self.audit("categorize", {"id": iid, "category": match, "subcategory": sub, "model": model,
                                  "seconds": round(time.time() - t0, 1)})
        _live("NEXUS", "studio", f"{it['name']} -> {match}/{sub} ({', '.join(kw[:4])})", ok=True,
              data={"model": model, "sek": round(time.time() - t0, 1)})
        return it

    def _blur_thumb(self, iid: str) -> None:
        from PIL import Image, ImageFilter
        tp = self.base / "thumbs" / f"{iid}.jpg"
        with Image.open(tp) as im:
            im.convert("RGB").filter(ImageFilter.GaussianBlur(40)).save(tp, quality=60)
        self.audit("private_locked", {"id": iid})

    # ------------------------------------------------------------ Statistik / Suche / Dubletten
    def stats(self) -> dict:
        tree: dict = {}
        for it in self.items.values():
            c = it.get("category") or "Unkategorisiert"
            node = tree.setdefault(c, {"count": 0, "sub": {}})
            node["count"] += 1
            if it.get("subcategory"):
                node["sub"][it["subcategory"]] = node["sub"].get(it["subcategory"], 0) + 1
        return {"total": len(self.items), "categorized": sum(1 for i in self.items.values() if i.get("category")),
                "versions": sum(len(i.get("versions", [])) for i in self.items.values()), "tree": tree,
                "taxonomy_source": taxonomy()["source"]}

    def search(self, q: str = "", category: str = "", date_from: str = "", date_to: str = "",
               min_quality: int = 0) -> list[dict]:
        terms = [t for t in re.split(r"\s+", q.lower().strip()) if t]
        out = []
        for it in self.items.values():
            hay = " ".join([it.get("name", ""), it.get("subject") or "", it.get("category") or "", it.get("subcategory") or "",
                            it.get("description", ""), " ".join(it.get("keywords", []))]).lower()
            if terms and not all(t in hay for t in terms):
                continue
            if category and (it.get("category") or "") != category:
                continue
            if date_from and (it.get("date") or "") < date_from:
                continue
            if date_to and (it.get("date") or "") > date_to:
                continue
            if (it.get("quality") or {}).get("score", 0) < int(min_quality or 0):
                continue
            out.append(it)
        return sorted(out, key=lambda i: i.get("date") or "", reverse=True)

    def duplicates(self, dup_max: int = 4, sim_max: int = 12) -> dict:
        ids = list(self.items)
        pairs = []
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                d = hamming(self.items[a]["dhash"], self.items[b]["dhash"])
                if d <= sim_max:
                    pairs.append({"a": a, "b": b, "distance": d, "type": "dublette" if d <= dup_max else "aehnlich"})
        return {"pairs": sorted(pairs, key=lambda p: p["distance"]), "method": f"dHash 64 bit, Hamming <= {dup_max} Dublette, <= {sim_max} aehnlich"}

    def similar(self, iid: str, n: int = 8) -> list[dict]:
        base = self.get(iid)["dhash"]
        ranked = sorted(((hamming(base, it["dhash"]), k) for k, it in self.items.items() if k != iid))
        return [{"id": k, "distance": d} for d, k in ranked[:n]]

    def verify(self) -> dict:
        res = []
        for it in self.items.values():
            src = Path(it["source"])
            cp = self.base / "originals" / it["copy"]
            res.append({"id": it["id"], "source_unchanged": src.is_file() and sha256_file(src) == it["source_sha256"],
                        "copy_unchanged": cp.is_file() and sha256_file(cp) == it["copy_sha256"]})
        ok = all(r["source_unchanged"] and r["copy_unchanged"] for r in res)
        self.audit("verify", {"count": len(res), "all_ok": ok})
        return {"all_ok": ok, "checked": len(res), "items": res}

    # ------------------------------------------------------------ Bearbeitung (klassisch, neue Version)
    OPS = ("auto", "natural", "warm", "cool", "artistic", "sharpen", "restore", "brighten", "darken",
           "bg_blur", "bg_replace", "remove_object", "social_square", "social_portrait", "social_story", "pro_industrial")
    BLOCKED_OPS = {"outpaint": "Generatives Outpainting braucht ein Diffusionsmodell (ComfyUI/SD) - auf diesem PC nicht lauffaehig installiert.",
                   "style_transfer": "Neuronaler Stiltransfer braucht ein Diffusions-/Stilmodell - nicht installiert. Ersatz: 'artistic' (OpenCV-Stylization).",
                   "generative_fill": "Generatives Inpainting nicht verfuegbar; 'remove_object' nutzt klassisches OpenCV-Inpainting mit Rechteck."}

    def edit(self, iid: str, op: str, rect: list | None = None, color: str | None = None, by: str = "NEXUS") -> dict:
        import cv2
        import numpy as np
        from PIL import Image, ImageOps
        it = self.get(iid)
        if op in self.BLOCKED_OPS:
            raise StudioError("BLOCKED:" + op, 501)
        if op not in self.OPS:
            raise StudioError("UNKNOWN_OP", 400)
        src = self.base / "originals" / it["copy"]
        with Image.open(src) as im:
            img = ImageOps.exif_transpose(im).convert("RGB")
        if max(img.size) > 3000:
            img.thumbnail((3000, 3000))
        a = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)

        def clahe(x, clip=2.0):
            lab = cv2.cvtColor(x, cv2.COLOR_BGR2LAB)
            l, aa, bb = cv2.split(lab)
            l = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8)).apply(l)
            return cv2.cvtColor(cv2.merge((l, aa, bb)), cv2.COLOR_LAB2BGR)

        def wb(x):
            f = x.astype(np.float32)
            m = f.reshape(-1, 3).mean(0)
            return np.clip(f * (m.mean() / np.maximum(m, 1)), 0, 255).astype(np.uint8)

        def tint(x, b, r):
            f = x.astype(np.int16)
            f[..., 0] += b
            f[..., 2] += r
            return np.clip(f, 0, 255).astype(np.uint8)

        def fg_mask(x, r):
            h, w = x.shape[:2]
            r = r or [int(w * .12), int(h * .08), int(w * .76), int(h * .86)]
            scale = 800 / max(h, w) if max(h, w) > 800 else 1
            small = cv2.resize(x, (int(w * scale), int(h * scale)))
            m = np.zeros(small.shape[:2], np.uint8)
            rr = tuple(max(1, int(v * scale)) for v in r)
            cv2.grabCut(small, m, rr, np.zeros((1, 65)), np.zeros((1, 65)), 4, cv2.GC_INIT_WITH_RECT)
            m2 = np.where((m == 1) | (m == 3), 255, 0).astype(np.uint8)
            m2 = cv2.GaussianBlur(cv2.resize(m2, (w, h)), (21, 21), 0)
            return (m2.astype(np.float32) / 255)[..., None]

        def crop(x, ratio):
            h, w = x.shape[:2]
            if w / h > ratio:
                nw = int(h * ratio); o = (w - nw) // 2
                return x[:, o:o + nw]
            nh = int(w / ratio); o = (h - nh) // 2
            return x[o:o + nh]

        if op == "auto":
            out = clahe(wb(a))
        elif op == "natural":
            out = clahe(wb(a), 1.4)
        elif op == "warm":
            out = tint(clahe(a, 1.5), -12, 14)
        elif op == "cool":
            out = tint(clahe(a, 1.5), 14, -10)
        elif op == "artistic":
            out = cv2.stylization(a, sigma_s=60, sigma_r=0.45)
        elif op == "sharpen":
            out = cv2.addWeighted(a, 1.6, cv2.GaussianBlur(a, (0, 0), 2), -0.6, 0)
        elif op == "restore":
            d = cv2.fastNlMeansDenoisingColored(a, None, 6, 6, 7, 21)
            out = cv2.addWeighted(clahe(d, 1.6), 1.4, cv2.GaussianBlur(d, (0, 0), 2), -0.4, 0)
        elif op in ("brighten", "darken"):
            out = cv2.convertScaleAbs(a, alpha=1.0, beta=28 if op == "brighten" else -28)
        elif op == "bg_blur":
            m = fg_mask(a, rect)
            out = (a * m + cv2.GaussianBlur(a, (0, 0), 12) * (1 - m)).astype(np.uint8)
        elif op == "bg_replace":
            m = fg_mask(a, rect)
            c = (color or "#202a3a").lstrip("#")
            bg = np.zeros_like(a); bg[:] = (int(c[4:6], 16), int(c[2:4], 16), int(c[0:2], 16))
            out = (a * m + bg * (1 - m)).astype(np.uint8)
        elif op == "remove_object":
            if not rect or len(rect) != 4:
                raise StudioError("RECT_REQUIRED: Bereich [x,y,b,h] des Objekts angeben", 400)
            x, y, w, h = (int(v) for v in rect)
            m = np.zeros(a.shape[:2], np.uint8)
            m[max(0, y):y + h, max(0, x):x + w] = 255
            out = cv2.inpaint(a, m, 7, cv2.INPAINT_TELEA)
        elif op == "pro_industrial":
            o = clahe(wb(a), 2.2)
            hsv = cv2.cvtColor(o, cv2.COLOR_BGR2HSV).astype(np.float32); hsv[..., 1] *= 0.8
            o = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
            out = tint(cv2.addWeighted(o, 1.5, cv2.GaussianBlur(o, (0, 0), 2), -0.5, 0), 8, -4)
        else:
            out = crop(clahe(wb(a), 1.6), {"social_square": 1.0, "social_portrait": 0.8, "social_story": 9 / 16}[op])
        n = len(it["versions"]) + 1
        name = f"{iid}_v{n}_{op}.jpg"
        cv2.imwrite(str(self.base / "versions" / name), out, [cv2.IMWRITE_JPEG_QUALITY, 92])
        ver = {"n": n, "op": op, "file": name, "by": by, "ts": datetime.now().isoformat(timespec="seconds"),
               "sha256": sha256_file(self.base / "versions" / name)}
        with self._lock:
            it["versions"].append(ver)
            self._save()
        self.audit("edit", {"id": iid, **ver})
        _live("NEXUS", "studio", f"Neue Version {name} ({op}) - Original unveraendert", ok=True)
        return ver

    # ------------------------------------------------------------ Ordnerschema: Vorschlag + Freigabe
    def propose_folders(self) -> dict:
        tax = taxonomy()["categories"]
        plan = []
        for it in self.items.values():
            if not it.get("category"):
                continue
            folder = (tax.get(it["category"], {}) or {}).get("folder") or re.sub(r"\W+", "_", it["category"])
            sub = re.sub(r"[^\w\- ]+", "", it.get("subcategory") or "Allgemein").strip() or "Allgemein"
            plan.append({"id": it["id"], "from": it["copy"], "to": f"{folder}/{sub}/{it['name']}"})
        nonce = secrets.token_hex(8)
        self._pending = {nonce: {"plan": plan, "ts": time.time()}}
        self.audit("propose_folders", {"count": len(plan), "nonce": nonce})
        return {"plan": plan, "nonce": nonce, "hinweis": "Nur Vorschlag. Ausfuehren erst mit Freigabe (nonce). "
                "Es werden KOPIEN nach workspace/studio/sortiert angelegt; Quellen werden nicht verschoben."}

    def apply_folders(self, nonce: str, confirm: bool) -> dict:
        p = self._pending.get(str(nonce))
        if not confirm or not p:
            raise StudioError("APPROVAL_REQUIRED", 403)
        if time.time() - p["ts"] > 900:
            self._pending.pop(nonce, None)
            raise StudioError("APPROVAL_EXPIRED", 403)
        self._pending.pop(nonce, None)
        done = 0
        root = (self.base / "sortiert").resolve()
        for step in p["plan"]:
            dst = (root / step["to"]).resolve()
            if not str(dst).startswith(str(root)):
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copy2(self.base / "originals" / step["from"], dst)
                os.chmod(dst, stat.S_IREAD | stat.S_IWRITE)
                done += 1
        self.audit("apply_folders", {"nonce": nonce, "copied": done})
        _live("NEXUS", "studio", f"Ordnerschema nach Freigabe angewendet: {done} Kopien", ok=True)
        return {"copied": done, "root": str(root)}


_STUDIO: Studio | None = None


def get_studio() -> Studio:
    global _STUDIO
    if _STUDIO is None:
        _STUDIO = Studio()
    return _STUDIO