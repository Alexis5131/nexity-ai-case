"""Score de similarite lexicale entre fiches d'un meme programme (alerte au-dela de 30 %).

Mesure : indice de Jaccard sur les paires de mots consecutifs (bigrammes),
apres retrait des mots vides. Calcule sur le texte differenciant (titre,
accroche, points forts) ; les prestations viennent de la meme notice pour
tous les lots et ne sont donc pas comparees.
"""

from __future__ import annotations

import re
import unicodedata
from itertools import combinations, pairwise
from typing import Any

from .models import FicheLot

SEUIL_SIMILARITE = 0.30

MOTS_VIDES = {
    "le",
    "la",
    "les",
    "un",
    "une",
    "des",
    "de",
    "du",
    "d",
    "l",
    "et",
    "a",
    "au",
    "aux",
    "en",
    "sur",
    "pour",
    "par",
    "avec",
    "dans",
    "ce",
    "cet",
    "cette",
    "son",
    "sa",
    "ses",
    "est",
    "qui",
    "que",
    "ou",
    "se",
    "s",
    "m",
    "m2",
}


def mots(texte: str) -> list[str]:
    t = unicodedata.normalize("NFKD", texte.lower()).encode("ascii", "ignore").decode()
    return [w for w in re.findall(r"[a-z0-9]+", t) if w not in MOTS_VIDES]


def bigrammes(texte: str) -> set[tuple[str, str]]:
    return set(pairwise(mots(texte)))


def jaccard(a: set[tuple[str, str]], b: set[tuple[str, str]]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def texte_differenciant(fiche: FicheLot) -> str:
    return " ".join([fiche.titre, fiche.accroche, *(p.texte for p in fiche.points_forts)])


def similarite(f1: FicheLot, f2: FicheLot) -> float:
    return round(jaccard(bigrammes(texte_differenciant(f1)), bigrammes(texte_differenciant(f2))), 3)


def similarites_paires(fiches: dict[str, FicheLot]) -> list[dict[str, Any]]:
    resultats: list[dict[str, Any]] = []
    for a, b in combinations(sorted(fiches), 2):
        score = similarite(fiches[a], fiches[b])
        resultats.append(
            {
                "paire": f"{a}-{b}",
                "score": score,
                "seuil": SEUIL_SIMILARITE,
                "statut": "OK" if score < SEUIL_SIMILARITE else "ALERTE",
            }
        )
    return resultats
