# -*- coding: utf-8 -*-
import io

from PIL import Image

from branding import composer_png_avec_logo, LOGO_PATH


def _png(w, h, color):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def test_sans_logo_renvoie_l_original(tmp_path):
    src = _png(40, 20, (10, 20, 30))
    assert composer_png_avec_logo(src, logo_path=tmp_path / "absent.png") == src


def test_bandeau_agrandit_la_hauteur_sans_toucher_la_largeur(tmp_path):
    src = _png(200, 80, (200, 200, 200))
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (100, 40), (255, 0, 0, 255)).save(logo)
    out = composer_png_avec_logo(src, logo_path=logo, largeur_frac=0.5, pad_frac=0.02)
    im = Image.open(io.BytesIO(out))
    assert im.size[0] == 200
    assert im.size[1] > 80


def test_logo_repo_existe_et_se_compose():
    assert LOGO_PATH.is_file()
    src = _png(400, 200, (240, 240, 240))
    out = composer_png_avec_logo(src)
    im = Image.open(io.BytesIO(out))
    assert im.size[0] == 400
    assert im.size[1] > 200
