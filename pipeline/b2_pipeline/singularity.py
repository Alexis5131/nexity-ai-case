"""Indice de singularite intra-programme : ce qui distingue un lot de ses voisins.

Calcul deterministe sur le flux MBI, fait AVANT l'appel au LLM : le modele
redige a partir de differences deja prouvees, il ne les invente pas.
La portee des comparaisons est bornee selon la couverture reelle du stock :
- `programme` si tout le stock disponible est present, sans voisin en quarantaine, et >= 3 lots ;
- `echantillon_verifie` sinon (interdit alors tout superlatif a l'echelle du programme).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Lot, Programme

MIN_LOTS_PORTEE_PROGRAMME = 3


@dataclass(frozen=True)
class Singularite:
    code: str
    libelle: str
    valeur: str
    reference: str | None = None
    champs: list[str] = field(default_factory=list)
    portee: str = "programme"  # "programme" | "echantillon_verifie"


@dataclass(frozen=True)
class PerimetreComparaison:
    typologie: str
    complet: bool
    nb_comparables: int
    nb_disponibles_flux: int
    nb_disponibles_programme: int
    lots_meme_typo_quarantaine: list[str] = field(default_factory=list)

    @property
    def formulation_autorisee(self) -> str:
        if self.complet:
            return f"parmi les {self.nb_comparables} {self.typologie} disponibles du programme"
        return f"parmi les {self.nb_comparables} {self.typologie} disponibles vérifiés"


def evaluer_perimetre(
    typologie: str,
    programme: Programme,
    lots_flux: list[Lot],
    lots_en_quarantaine: set[str],
) -> PerimetreComparaison:
    disponibles = [lot for lot in lots_flux if lot.disponible]
    quarantaine_typo = sorted(
        lot.lot_id
        for lot in disponibles
        if lot.typologie == typologie and lot.lot_id in lots_en_quarantaine
    )
    comparables = [
        lot
        for lot in disponibles
        if lot.typologie == typologie and lot.lot_id not in lots_en_quarantaine
    ]
    complet = (
        len(disponibles) >= programme.nb_lots_disponibles
        and not quarantaine_typo
        and len(comparables) >= MIN_LOTS_PORTEE_PROGRAMME
    )
    return PerimetreComparaison(
        typologie=typologie,
        complet=complet,
        nb_comparables=len(comparables),
        nb_disponibles_flux=len(disponibles),
        nb_disponibles_programme=programme.nb_lots_disponibles,
        lots_meme_typo_quarantaine=quarantaine_typo,
    )


def m2(x: float) -> str:
    return f"{x:.1f}".replace(".", ",") + " m²"


def euros(x: float) -> str:
    return f"{round(x):,}".replace(",", " ") + " €"


def prix_m2(lot: Lot) -> float:
    return lot.prix_ttc / lot.surface_habitable_mbi


def _contient(texte: str, *mots: str) -> bool:
    t = texte.lower()
    return any(m in t for m in mots)


def calculer_singularites(
    lot: Lot,
    voisins: list[Lot],
    *,
    perimetre_complet: bool = True,
) -> list[Singularite]:
    """`voisins` : autres lots disponibles et fiables de meme typologie."""
    pairs = [v for v in voisins if v.lot_id != lot.lot_id and v.typologie == lot.typologie]
    if not pairs:
        return []
    typ = lot.typologie
    portee = "programme" if perimetre_complet else "echantillon_verifie"
    cible = f"{typ} disponibles" if perimetre_complet else f"{typ} disponibles vérifiés"
    res: list[Singularite] = []

    if lot.dernier_etage and not any(p.dernier_etage for p in pairs):
        lib = (
            f"Seul {typ} disponible au dernier étage"
            if perimetre_complet
            else f"Dernier étage parmi les {cible}"
        )
        res.append(
            Singularite(
                "DERNIER_ETAGE",
                lib,
                f"{lot.etage}e et dernier étage",
                None,
                ["etage", "dernier_etage"],
                portee,
            )
        )

    ext_max = max(p.exterieur_m2 for p in pairs)
    if lot.exterieur_m2 > ext_max:
        res.append(
            Singularite(
                "PLUS_GRAND_EXTERIEUR",
                f"Plus grand extérieur des {cible}",
                m2(lot.exterieur_m2),
                f"{m2(ext_max)} pour le suivant",
                ["exterieur_m2", "exterieur_type"],
                portee,
            )
        )

    if _contient(lot.exposition, "traversant") and not any(
        _contient(p.exposition, "traversant") for p in pairs
    ):
        lib = (
            f"Seul {typ} disponible traversant"
            if perimetre_complet
            else f"Traversant parmi les {cible}"
        )
        res.append(Singularite("TRAVERSANT", lib, lot.exposition, None, ["exposition"], portee))

    if _contient(lot.exposition + lot.exterieur_type, "îlot", "ilot", "jardin") and not any(
        _contient(p.exposition + p.exterieur_type, "îlot", "ilot", "jardin") for p in pairs
    ):
        lib = (
            f"Seul {typ} disponible côté cœur d'îlot"
            if perimetre_complet
            else f"Côté cœur d'îlot parmi les {cible}"
        )
        res.append(
            Singularite(
                "COEUR_ILOT",
                lib,
                lot.exterieur_type,
                None,
                ["exposition", "exterieur_type"],
                portee,
            )
        )

    prix_min_autres = min(p.prix_ttc for p in pairs)
    if lot.prix_ttc < prix_min_autres:
        res.append(
            Singularite(
                "PRIX_ACCES",
                f"Prix le plus bas des {cible}",
                euros(lot.prix_ttc),
                f"{euros(prix_min_autres)} pour le suivant",
                ["prix_ttc"],
                portee,
            )
        )

    pm2_min_autres = min(prix_m2(p) for p in pairs)
    if prix_m2(lot) < pm2_min_autres:
        res.append(
            Singularite(
                "PRIX_M2",
                f"Meilleur prix au m² des {cible}",
                euros(prix_m2(lot)) + "/m²",
                euros(pm2_min_autres) + "/m² pour le suivant",
                ["prix_ttc", "surface_habitable_mbi"],
                portee,
            )
        )

    if lot.surface_habitable_mbi > max(p.surface_habitable_mbi for p in pairs):
        res.append(
            Singularite(
                "PLUS_GRANDE_SURFACE",
                f"Plus grande surface des {cible}",
                m2(lot.surface_habitable_mbi),
                None,
                ["surface_habitable_mbi"],
                portee,
            )
        )
    return res
