import pytest

from b2_pipeline.generator import (
    extraire_nombres, generer_depuis_cache, generer_live, charger_cache, valider_fiche,
)
from b2_pipeline.models import FicheLot
from b2_pipeline.plan_reader import lire_plan_svg
from b2_pipeline.singularity import calculer_singularites
from tests.conftest import ASSETS, CACHE, FauxClient


@pytest.fixture
def contexte(lots, donnees):
    voisins = [lots["B31"], lots["B12"]]

    def _ctx(lot_id):
        lot = lots[lot_id]
        return lot, lire_plan_svg(ASSETS / lot.plan_2d_fichier), calculer_singularites(lot, voisins), donnees.notice
    return _ctx


@pytest.fixture
def fiches():
    return charger_cache(CACHE)[0]


def test_extraction_des_nombres_francais():
    assert extraire_nombres("séjour de 27,2 m², 6 prises, 279 000 €, lot B31, T3, RE2020, 45x45") == [27.2, 6.0, 279000.0, 45.0]
    assert extraire_nombres("au 4e et 1er étage") == [4.0, 1.0]


@pytest.mark.parametrize("lot_id", ["B31", "B12"])
def test_fiches_du_cache_passent_les_controles(contexte, fiches, lot_id):
    assert valider_fiche(fiches[lot_id], *contexte(lot_id)) == []


def _modifier(fiche: FicheLot, **champs) -> FicheLot:
    return FicheLot.model_validate({**fiche.model_dump(), **champs})


def test_chiffre_invente_refuse(contexte, fiches):
    f = _modifier(fiches["B31"], accroche="Un séjour de 32 m² ouvert sur une terrasse plein ciel orientée Sud-Ouest.")
    assert any("32" in v for v in valider_fiche(f, *contexte("B31")))


def test_prix_et_mentions_fiscales_refuses(contexte, fiches):
    f = _modifier(fiches["B31"], titre="T3 éligible au PTZ à 312 000 € seulement")
    violations = valider_fiche(f, *contexte("B31"))
    assert any("ptz" in v.lower() for v in violations)
    assert any("€" in v for v in violations)


def test_promesse_non_prouvee_refusee(contexte, fiches):
    f = _modifier(fiches["B31"], accroche="Une cuisine équipée et une vue imprenable sur la Seine, au dernier étage.")
    violations = valider_fiche(f, *contexte("B31"))
    assert any("cuisine équipée" in v for v in violations) and any("vue imprenable" in v for v in violations)


def test_clause_inconnue_et_source_inconnue_refusees(contexte, fiches):
    d = fiches["B31"].model_dump()
    d["prestations"][0]["clause_id"] = "NOT-P99"
    d["points_forts"][0]["sources"] = ["MBI.prix_ttc"]
    violations = valider_fiche(FicheLot.model_validate(d), *contexte("B31"))
    assert any("NOT-P99" in v for v in violations) and any("MBI.prix_ttc" in v for v in violations)


def test_fiche_sans_singularite_refusee(contexte, fiches):
    d = fiches["B12"].model_dump()
    for p in d["points_forts"]:
        p["sources"] = [s for s in p["sources"] if not s.startswith("SING.")] or ["MBI.etage"]
    assert any("singularité" in v for v in valider_fiche(FicheLot.model_validate(d), *contexte("B12")))


def test_generation_live_reessaie_apres_refus(contexte, fiches):
    mauvaise = _modifier(fiches["B31"], titre="T3 parfait au dernier étage, terrasse de 14 m²").model_dump()
    client = FauxClient([mauvaise, fiches["B31"].model_dump()])
    r = generer_live(client, *contexte("B31"))
    assert r.statut == "PRETE" and r.tentatives == 2 and r.source == "live"
    assert "refusée par les contrôles" in client.appels[1]["contenu"][0]["text"]


def test_generation_live_echoue_en_revue_humaine(contexte, fiches):
    mauvaise = _modifier(fiches["B31"], titre="T3 parfait au dernier étage, terrasse de 14 m²").model_dump()
    r = generer_live(FauxClient([mauvaise, mauvaise]), *contexte("B31"))
    assert r.statut == "REVUE_HUMAINE" and r.violations


def test_schema_invalide_puis_correct(contexte, fiches):
    r = generer_live(FauxClient([{"titre": "x"}, fiches["B12"].model_dump()]), *contexte("B12"))
    assert r.statut == "PRETE" and r.tentatives == 2


def test_cache_lot_absent(contexte):
    r = generer_depuis_cache({}, *contexte("B31"))
    assert r.statut == "ECHEC" and r.fiche is None
