from pathlib import Path

import pytest
from b2_pipeline.loaders import charger_donnees

RACINE = Path(__file__).resolve().parents[2]
ASSETS = RACINE / "assets"
CACHE = RACINE / "pipeline" / "cache" / "generation_cache.json"


@pytest.fixture(scope="session")
def donnees():
    return charger_donnees(ASSETS)


@pytest.fixture(scope="session")
def lots(donnees):
    return {lot.lot_id: lot for lot in donnees.flux.lots}


class FauxClient:
    """Client Claude simule : renvoie des reponses programmees, enregistre les appels."""

    modele = "faux-modele"

    def __init__(self, reponses):
        self.reponses = list(reponses)
        self.appels = []

    def appeler_outil(self, systeme, contenu, nom_outil, schema, max_tokens=1500):
        self.appels.append({"systeme": systeme, "contenu": contenu, "outil": nom_outil})
        reponse = self.reponses.pop(0)
        if isinstance(reponse, Exception):
            raise reponse
        return reponse
