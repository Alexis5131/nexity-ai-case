"""Chargement des fichiers de demo (dossier assets/)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .models import BibliothequeMentions, FluxMBI, Notice


@dataclass(frozen=True)
class DonneesDemo:
    racine: Path
    flux: FluxMBI
    notice: Notice
    mentions: BibliothequeMentions

    def chemin(self, relatif: str) -> Path:
        return self.racine / relatif


def _lire_json(chemin: Path) -> dict:
    with chemin.open(encoding="utf-8") as f:
        return json.load(f)


def charger_donnees(racine_assets: str | Path) -> DonneesDemo:
    racine = Path(racine_assets)
    dossier = racine / "data-demo"
    return DonneesDemo(
        racine=racine,
        flux=FluxMBI.model_validate(_lire_json(dossier / "mbi_lots.json")),
        notice=Notice.model_validate(_lire_json(dossier / "notice_descriptive_extrait.json")),
        mentions=BibliothequeMentions.model_validate(
            _lire_json(dossier / "bibliotheque_mentions_legales.json")
        ),
    )
