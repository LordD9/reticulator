# -*- coding: utf-8 -*-
"""Logo Cerema : bandeau PNG sans recouvrir la carte."""
import io
from pathlib import Path

LOGO_PATH = Path(__file__).resolve().parent / "logo.png"


def composer_png_avec_logo(
    png_bytes,
    logo_path=LOGO_PATH,
    largeur_frac=0.32,
    pad_frac=0.012,
):
    """Ajoute le logo dans une bande blanche *au-dessus* du PNG (carte intacte).

    Si le logo est absent, renvoie les octets d'origine.
    """
    if not png_bytes:
        return png_bytes
    path = Path(logo_path)
    if not path.is_file():
        return png_bytes
    from PIL import Image

    carte = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    logo = Image.open(path).convert("RGBA")
    target_w = max(1, int(carte.width * largeur_frac))
    ratio = target_w / float(logo.width)
    target_h = max(1, int(logo.height * ratio))
    logo_r = logo.resize((target_w, target_h), Image.Resampling.LANCZOS)
    pad = max(4, int(carte.width * pad_frac))
    banner_h = logo_r.height + 2 * pad
    out = Image.new("RGBA", (carte.width, carte.height + banner_h), (255, 255, 255, 255))
    out.paste(logo_r, (pad, pad), logo_r)
    out.paste(carte, (0, banner_h), carte)
    buf = io.BytesIO()
    out.convert("RGB").save(buf, format="PNG", dpi=(300, 300))
    return buf.getvalue()


def composer_pdf_depuis_png(png_bytes):
    """PDF une page, logo déjà dans le PNG (300 dpi)."""
    if not png_bytes:
        return png_bytes
    from PIL import Image

    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="PDF", resolution=300.0)
    return buf.getvalue()
