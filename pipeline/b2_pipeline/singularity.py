"""Indice de singularite intra-programme : ce qui distingue un lot de ses voisins.

Calcul deterministe sur le flux MBI, fait AVANT l'appel au LLM : le modele
redige a partir de differences deja prouvees, il ne les invente pas.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Lot


@dataclass(frozen=True)
class Singularite:
    code: str
    libelle: str
    valeur: str
    reference: str | None = None
    champs: list[str] = field(default_factory=list)


def m2(x: float) -> str:
    return f"{x:.1f}".replace(".", ",") + " m²"


def euros(x: float) -> str:
    return f"{round(x):,}".replace(",", " ") + " €"


def prix_m2(lot: Lot) -> float:
    return lot.prix_ttc / lot.surface_habitable_mbi


def _contient(texte: str, *mots: str) -> bool:
    t = texte.lower()
    return any(m in t for m in mots)


def calculer_singularites(lot: Lot, voisins: list[Lot]) -> list[Singularite]:
    """`voisins` : autres lots disponibles et fiables de meme typologie."""
    pairs = [v for v in voisins if v.lot_id != lot.lot_id and v.typologie == lot.typologie]
    if not pairs:
        return []
    typ = lot.typologie
    res: list[Singularite] = []

    if lot.dernier_etage and not any(p.dernier_etage for p in pairs):
        res.append(Singularite("DERNIER_ETAGE", f"Seul {typ} disponible au dernier étage",
                               f"{lot.etage}e et dernier étage", None, ["etage", "dernier_etage"]))

    ext_max = max(p.exterieur_m2 for p in pairs)
    if lot.exterieur_m2 > ext_max:
        res.append(Singularite("PLUS_GRAND_EXTERIEUR", f"Plus grand extérieur des {typ} disponibles",
                               m2(lot.exterieur_m2), f"{m2(ext_max)} pour le suivant",
                               ["exterieur_m2", "exterieur_type"]))

    if _contient(lot.exposition, "traversant") and not any(_contient(p.exposition, "traversant") for p in pairs):
        res.append(Singularite("TRAVERSANT", f"Seul {typ} disponible traversant",
                               lot.exposition, None, ["exposition"]))

    if _contient(lot.exposition + lot.exterieur_type, "îlot", "ilot", "jardin") and not any(
        _contient(p.exposition + p.exterieur_type, "îlot", "ilot", "jardin") for p in pairs
    ):
        res.append(Singularite("COEUR_ILOT", f"Seul {typ} disponible côté cœur d'îlot",
                               lot.exterieur_type, None, ["exposition", "exterieur_type"]))

    prix_min_autres = min(p.prix_ttc for p in pairs)
    if lot.prix_ttc < prix_min_autres:
        res.append(Singularite("PRIX_ACCES", f"Prix le plus bas des {typ} disponibles",
                               euros(lot.prix_ttc), f"{euros(prix_min_autres)} pour le suivant", ["prix_ttc"]))

    pm2_min_autres = min(prix_m2(p) for p in pairs)
    if prix_m2(lot) < pm2_min_autres:
        res.append(Singularite("PRIX_M2", f"Meilleur prix au m² des {typ} disponibles",
                               euros(prix_m2(lot)) + "/m²", euros(pm2_min_autres) + "/m² pour le suivant",
                               ["prix_ttc", "surface_habitable_mbi"]))

    if lot.surface_habitable_mbi > max(p.surface_habitable_mbi for p in pairs):
        res.append(Singularite("PLUS_GRANDE_SURFACE", f"Plus grande surface des {typ} disponibles",
                               m2(lot.surface_habitable_mbi), None, ["surface_habitable_mbi"]))
    return res
