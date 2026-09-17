# -*- coding: utf-8 -*-
import json

from missions_io import (
    enregistrer_preset,
    exporter_missions,
    importer_missions,
    lister_presets,
    mission_vide,
    slug_region,
)


def test_aller_retour_json():
    ods = [mission_vide(i) for i in range(8)]
    ods[0]["depart"] = "A"
    ods[0]["arrivee"] = "B"
    ods[0]["steps"] = ["A", "X", "B"]
    ods[0]["served_stations"] = ["A", "B"]
    ods[0]["freq_tph"] = 2.0
    ods[0]["color"] = "#123456"
    blob = json.dumps(exporter_missions(ods))
    back, warns = importer_missions(blob, gares_connues={"A", "X", "B"})
    assert not warns
    assert back[0]["steps"] == ["A", "X", "B"]
    assert back[0]["freq_tph"] == 2.0
    assert back[0]["color"] == "#123456"
    assert len(back) == 8


def test_gares_hors_perimetre_filtrees():
    payload = {
        "version": 1,
        "missions": [{
            "idx": 0,
            "color": "#000",
            "depart": "A",
            "arrivee": "Z",
            "steps": ["A", "Z"],
            "served_stations": ["A", "Z"],
            "freq_tph": 1,
        }],
    }
    back, warns = importer_missions(payload, gares_connues={"A"})
    assert warns
    assert back[0]["steps"] == ["A"]
    assert "Z" not in back[0]["served_stations"]


def test_slug_region_paca():
    assert slug_region("Provence-Alpes-Côte d'Azur") == "provence-alpes-cote-d-azur"


def test_preset_ecrit_et_liste(tmp_path):
    ods = [mission_vide(i) for i in range(8)]
    ods[0]["depart"] = "A"
    path = enregistrer_preset(ods, "Provence-Alpes-Côte d'Azur", "cote bleue", root=tmp_path)
    assert path.parent.name == "provence-alpes-cote-d-azur"
    found = lister_presets("Provence-Alpes-Côte d'Azur", root=tmp_path)
    assert path in found
