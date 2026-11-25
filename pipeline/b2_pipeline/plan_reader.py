"""Lecture du cartouche des plans de vente 2D.

Deux lectures possibles :
- `lire_plan_svg` : deterministe, sur le texte du SVG (aucun appel reseau) ;
- `lire_plan_vision` : Claude lit le PNG du plan (demo live).
Dans les deux cas, on controle aussi que la somme des pieces egale le cartouche.
"""

from __future__ import annotations

import base64
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from defusedxml import ElementTree as ET

from .llm import ClientOutil

_NOMBRE = r"(\d+(?:[.,]\d+)?)"
_RE_SURFACE_SEULE = re.compile(rf"^{_NOMBRE}\s*m²$")
_RE_DETAIL = re.compile(rf"^(.+?)\s*\.{{2,}}\s*{_NOMBRE}\s*m²$")
_RE_EXTERIEUR = re.compile(rf"^\+\s*(.+?)\s+{_NOMBRE}\s*m²$")
_RE_LOT = re.compile(r"LOT\s+([A-Z]\d+)")
_RE_EXPO = re.compile(r"^Expo\s*:\s*(.+)$")
_TITRE_CARTOUCHE = "SURFACE HABITABLE CARTOUCHE ARCHITECTE"


@dataclass(frozen=True)
class LecturePlan:
    lot_id: str | None
    surface_cartouche: float
    pieces: dict[str, float] = field(default_factory=dict)
    exterieur_libelle: str | None = None
    exterieur_m2: float | None = None
    exposition: str | None = None
    source: str = "svg"

    @property
    def somme_pieces(self) -> float:
        return round(sum(self.pieces.values()), 2)


def nombre_fr(texte: str) -> float:
    return float(texte.replace(",", "."))


def slug(texte: str) -> str:
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", sans_accents.lower()).strip("_")


def textes_svg(chemin: str | Path) -> list[str]:
    arbre = ET.parse(str(chemin))
    textes: list[str] = []
    for element in arbre.iter():
        if element.tag.endswith("text"):
            contenu = "".join(element.itertext()).strip()
            if contenu:
                textes.append(contenu)
    return textes


def analyser_textes_plan(textes: list[str], source: str = "svg") -> LecturePlan:
    lot_id: str | None = None
    exposition: str | None = None
    exterieur_libelle: str | None = None
    exterieur_m2: float | None = None
    surface: float | None = None
    pieces: dict[str, float] = {}

    for i, t in enumerate(textes):
        if lot_id is None and (m := _RE_LOT.search(t)):
            lot_id = m.group(1)
        if exposition is None and (m := _RE_EXPO.match(t)):
            exposition = m.group(1).strip()
        if t == _TITRE_CARTOUCHE:
            for candidat in textes[i + 1 :]:
                if m_surf := _RE_SURFACE_SEULE.match(candidat):
                    surface = nombre_fr(m_surf.group(1))
                    break
        if m := _RE_DETAIL.match(t):
            pieces[slug(m.group(1))] = nombre_fr(m.group(2))
        if exterieur_m2 is None and (m := _RE_EXTERIEUR.match(t)):
            exterieur_libelle, exterieur_m2 = m.group(1).strip(), nombre_fr(m.group(2))

    if surface is None:
        raise ValueError("Cartouche introuvable : surface habitable non lue sur le plan")
    return LecturePlan(lot_id, surface, pieces, exterieur_libelle, exterieur_m2, exposition, source)


def lire_plan_svg(chemin: str | Path) -> LecturePlan:
    return analyser_textes_plan(textes_svg(chemin), source="svg")


SCHEMA_VISION: dict[str, Any] = {
    "type": "object",
    "properties": {
        "lot_id": {"type": "string"},
        "surface_cartouche_m2": {"type": "number"},
        "pieces": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"nom": {"type": "string"}, "m2": {"type": "number"}},
                "required": ["nom", "m2"],
            },
        },
        "exterieur_libelle": {"type": "string"},
        "exterieur_m2": {"type": "number"},
        "exposition": {"type": "string"},
    },
    "required": ["lot_id", "surface_cartouche_m2", "pieces"],
}

SYSTEME_VISION = (
    "Tu lis des plans de vente d'appartements neufs. Recopie uniquement ce qui est ecrit "
    "dans le cartouche de l'architecte (bloc de droite) : reference du lot, surface habitable, "
    "detail des surfaces privatives, exterieur et exposition. N'estime rien, ne calcule rien."
)


def lire_plan_vision(chemin_png: str | Path, client: ClientOutil) -> LecturePlan:
    image = base64.standard_b64encode(Path(chemin_png).read_bytes()).decode()
    contenu: list[dict[str, Any]] = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image}},
        {"type": "text", "text": "Lis le cartouche de ce plan et renvoie les valeurs exactes."},
    ]
    brut = client.appeler_outil(
        SYSTEME_VISION, contenu, "lecture_cartouche", SCHEMA_VISION, max_tokens=800
    )
    return LecturePlan(
        lot_id=brut.get("lot_id"),
        surface_cartouche=float(brut["surface_cartouche_m2"]),
        pieces={slug(p["nom"]): float(p["m2"]) for p in brut.get("pieces", [])},
        exterieur_libelle=brut.get("exterieur_libelle"),
        exterieur_m2=brut.get("exterieur_m2"),
        exposition=brut.get("exposition"),
        source="vision",
    )
