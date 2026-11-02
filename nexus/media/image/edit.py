from __future__ import annotations

def edit_info() -> dict:
    try:
        import PIL  # noqa
        return {"pillow": True, "ops": ["resize", "convert"]}
    except Exception:
        return {"pillow": False, "ops": [], "note": "Pillow fehlt — Image-Edit Stubs."}
