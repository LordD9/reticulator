# -*- coding: utf-8 -*-
"""Sauvegarde / import des 8 missions (JSON) et presets par région."""
import json
import re
import unicodedata
from pathlib import Path

SCHEMA_VERSION = 1
N_MISSIONS = 8
_FIELDS = ("idx", "color", "depart", "arrivee", "steps", "served_stations", "freq_tph")
MISSIONS_ROOT = Path(__file__).resolve().parent / "missions"


def mission_vide(idx, color="#4E79A7"):
    return {
        "idx": idx,
        "color": color,
        "depart": None,
        "arrivee": None,
        "steps": [],
        "served_stations": [],
        "freq_tph": 1.0,
    }


def exporter_missions(ods):
    missions = []
    for i in range(N_MISSIONS):
        if i < len(ods):
            od = ods[i]
            missions.append({
                "idx": i,
                "color": od.get("color"),
                "depart": od.get("depart"),
                "arrivee": od.get("arrivee"),
                "steps": list(od.get("steps") or []),
                "served_stations": list(od.get("served_stations") or []),
                "freq_tph": float(od.get("freq_tph") or 1.0),
            })
        else:
            missions.append(mission_vide(i))
    return {"version": SCHEMA_VERSION, "missions": missions}


def importer_missions(payload, gares_connues=None, couleurs_defaut=None):
    """Retourne (ods, avertissements). Gares inconnues retirees du trajet."""
    warns = []
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict) or "missions" not in payload:
        raise ValueError("JSON missions invalide (cle 'missions' manquante).")
    known = set(gares_connues or [])
    colors = list(couleurs_defaut or ["#4E79A7"] * N_MISSIONS)
    ods = [mission_vide(i, colors[i] if i < len(colors) else "#4E79A7") for i in range(N_MISSIONS)]
    for raw in payload.get("missions") or []:
        if not isinstance(raw, dict):
            continue
        try:
            i = int(raw.get("idx", -1))
        except (TypeError, ValueError):
            continue
        if i < 0 or i >= N_MISSIONS:
            continue
        steps = [str(s) for s in (raw.get("steps") or [])]
        served = [str(s) for s in (raw.get("served_stations") or [])]
        if known:
            dropped = [s for s in steps if s not in known]
            if dropped:
                warns.append(
                    f"Mission {i + 1} : {len(dropped)} gare(s) hors perimetre ignoree(s)."
                )
            steps = [s for s in steps if s in known]
            served = [s for s in served if s in steps]
        depart = raw.get("depart")
        arrivee = raw.get("arrivee")
        if known:
            if depart and str(depart) not in known:
                depart = steps[0] if steps else None
            if arrivee and str(arrivee) not in known:
                arrivee = steps[-1] if steps else None
        if steps:
            depart = depart or steps[0]
            arrivee = arrivee or steps[-1]
        try:
            freq = float(raw.get("freq_tph") or 1.0)
        except (TypeError, ValueError):
            freq = 1.0
        ods[i] = {
            "idx": i,
            "color": raw.get("color") or ods[i]["color"],
            "depart": str(depart) if depart else None,
            "arrivee": str(arrivee) if arrivee else None,
            "steps": steps,
            "served_stations": served,
            "freq_tph": max(0.1, freq),
        }
    return ods, warns


def slug_region(nom):
    if not nom:
        return "national"
    raw = unicodedata.normalize("NFKD", str(nom))
    raw = "".join(c for c in raw if not unicodedata.combining(c))
    raw = re.sub(r"[^A-Za-z0-9]+", "-", raw).strip("-").lower()
    return raw or "region"


def dossier_presets(perimetre, root=None):
    root = Path(root) if root is not None else MISSIONS_ROOT
    if not perimetre or perimetre == "national":
        return root / "national"
    return root / slug_region(perimetre)


def lister_presets(perimetre, root=None):
    d = dossier_presets(perimetre, root)
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.json") if p.is_file())


def enregistrer_preset(ods, perimetre, nom, root=None):
    d = dossier_presets(perimetre, root)
    d.mkdir(parents=True, exist_ok=True)
    slug = slug_region(nom) or "missions"
    path = d / f"{slug}.json"
    path.write_text(
        json.dumps(exporter_missions(ods), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
