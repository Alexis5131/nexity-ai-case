"""Mentions legales : assemblage deterministe par identifiant, jamais par le LLM."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .models import BibliothequeMentions, Lot


@dataclass(frozen=True)
class MentionAssemblee:
    id: str
    texte: str
    empreinte: str  # sha256 tronque : prouve que le texte est celui de la bibliotheque


def empreinte(texte: str) -> str:
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()[:12]


def ids_mentions(lot: Lot) -> list[str]:
    ids = ["LEGAL_VEFA_PRIX"]
    if lot.ptz_eligible:
        ids.append("LEGAL_PTZ_2026")
    ids.append("LEGAL_VISUEL_NON_CONTRACTUEL")
    return ids


def assembler_mentions(lot: Lot, bibliotheque: BibliothequeMentions) -> list[MentionAssemblee]:
    manquantes = [i for i in ids_mentions(lot) if i not in bibliotheque.mentions]
    if manquantes:
        raise KeyError(f"Mentions absentes de la bibliothèque validée : {manquantes}")
    return [
        MentionAssemblee(i, bibliotheque.mentions[i], empreinte(bibliotheque.mentions[i]))
        for i in ids_mentions(lot)
    ]


def verifier_integrite(
    mentions: list[MentionAssemblee], bibliotheque: BibliothequeMentions
) -> bool:
    return all(
        empreinte(bibliotheque.mentions[m.id]) == m.empreinte == empreinte(m.texte)
        for m in mentions
    )
