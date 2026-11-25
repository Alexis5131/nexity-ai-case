import pytest
from b2_pipeline.generator import (
    charger_cache,
    empreinte_contexte,
    extraire_nombres,
    generer_depuis_cache,
    generer_live,
    valider_fiche,
)
from b2_pipeline.models import FicheLot
from b2_pipeline.plan_reader import lire_plan_svg
from b2_pipeline.singularity import calculer_singularites

from tests.conftest import ASSETS, CACHE, FauxClient


@pytest.fixture
def contexte(lots, donnees):
    voisins = [lots["B31"], lots["B12"]]

    def _ctx(lot_id, *, perimetre_complet=False):
        lot = lots[lot_id]
        plan = lire_plan_svg(ASSETS / lot.plan_2d_fichier)
        sings = calculer_singularites(lot, voisins, perimetre_complet=perimetre_complet)
        return lot, plan, sings, donnees.notice

    return _ctx


@pytest.fixture
def fiches():
    return charger_cache(CACHE)[0]


def test_extraction_des_nombres_francais():
    texte = "séjour de 27,2 m², 6 prises, 279 000 €, lot B31, T3, RE2020, 45x45"
    assert extraire_nombres(texte) == [27.2, 6.0, 279000.0, 45.0]
    assert extraire_nombres("au 4e et 1er étage") == [4.0, 1.0]


@pytest.mark.parametrize("lot_id", ["B31", "B12"])
def test_fiches_du_cache_passent_les_controles_echantillon(contexte, fiches, lot_id):
    assert valider_fiche(fiches[lot_id], *contexte(lot_id, perimetre_complet=False)) == []


def _modifier(fiche: FicheLot, **champs) -> FicheLot:
    return FicheLot.model_validate({**fiche.model_dump(), **champs})


def test_chiffre_invente_refuse(contexte, fiches):
    f = _modifier(
        fiches["B31"],
        accroche="Un séjour de 32 m² ouvert sur une terrasse plein ciel orientée Sud-Ouest.",
    )
    assert any("32" in v for v in valider_fiche(f, *contexte("B31")))


def test_prix_et_mentions_fiscales_refuses(contexte, fiches):
    f = _modifier(fiches["B31"], titre="T3 éligible au PTZ à 312 000 € seulement")
    violations = valider_fiche(f, *contexte("B31"))
    assert any("ptz" in v.lower() for v in violations)
    assert any("€" in v for v in violations)


def test_promesse_non_prouvee_refusee(contexte, fiches):
    f = _modifier(
        fiches["B31"],
        accroche="Une cuisine équipée et une vue imprenable sur la Seine, au dernier étage.",
    )
    violations = valider_fiche(f, *contexte("B31"))
    assert any("cuisine équipée" in v for v in violations)
    assert any("vue imprenable" in v for v in violations)


def test_superlatif_portee_globale_refuse_sur_echantillon_partiel(contexte, fiches):
    f_seul = _modifier(
        fiches["B31"],
        accroche="Le seul T3 disponible au dernier étage du bâtiment B, terrasse Sud-Ouest.",
    )
    f_prog = _modifier(
        fiches["B12"],
        accroche="Exposé Sud-Est, c'est le T3 disponible le plus accessible du programme.",
    )
    v_seul = valider_fiche(f_seul, *contexte("B31", perimetre_complet=False))
    v_prog = valider_fiche(f_prog, *contexte("B12", perimetre_complet=False))
    assert any("le seul" in v for v in v_seul)
    assert any("du programme" in v for v in v_prog)


def test_superlatif_programme_accepte_si_perimetre_complet_et_disponible(contexte, fiches):
    f_ok = _modifier(
        fiches["B12"],
        accroche="Exposé Sud-Est, c'est le T3 disponible le plus accessible du programme.",
    )
    f_sans_dispo = _modifier(
        fiches["B12"],
        accroche="Exposé Sud-Est, c'est le T3 le plus accessible du programme.",
    )
    assert valider_fiche(f_ok, *contexte("B12", perimetre_complet=True)) == []
    violations = valider_fiche(f_sans_dispo, *contexte("B12", perimetre_complet=True))
    assert any("disponibles" in v for v in violations)


def test_invalidation_empreinte_quand_voisin_vendu(lots, donnees, fiches):
    lot_b31 = lots["B31"]
    plan_b31 = lire_plan_svg(ASSETS / lot_b31.plan_2d_fichier)
    sings_avant = calculer_singularites(
        lot_b31, [lots["B31"], lots["B12"]], perimetre_complet=False
    )
    emp_avant = empreinte_contexte(
        lot_b31, plan_b31, sings_avant, donnees.notice, perimetre_complet=False
    )
    # Si B12 est vendu, B31 n'a plus de voisin disponible dans l'extrait
    sings_apres = calculer_singularites(lot_b31, [lots["B31"]], perimetre_complet=False)
    emp_apres = empreinte_contexte(
        lot_b31, plan_b31, sings_apres, donnees.notice, perimetre_complet=False
    )
    assert emp_avant != emp_apres
    r = generer_depuis_cache(
        fiches,
        lot_b31,
        plan_b31,
        sings_apres,
        donnees.notice,
        perimetre_complet=False,
        empreintes_cache={"B31": emp_avant},
    )
    assert r.statut == "REVUE_HUMAINE"
    assert any("stock modifié" in v or "6.5" in v for v in r.violations)


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
    assert any(
        "singularité" in v for v in valider_fiche(FicheLot.model_validate(d), *contexte("B12"))
    )


def test_generation_live_reessaie_apres_refus(contexte, fiches):
    mauvaise = _modifier(
        fiches["B31"], titre="T3 parfait au dernier étage, terrasse de 14 m²"
    ).model_dump()
    client = FauxClient([mauvaise, fiches["B31"].model_dump()])
    r = generer_live(client, *contexte("B31"))
    assert r.statut == "PRETE" and r.tentatives == 2 and r.source == "live"
    assert "refusée par les contrôles" in client.appels[1]["contenu"][0]["text"]


def test_generation_live_echoue_en_revue_humaine(contexte, fiches):
    mauvaise = _modifier(
        fiches["B31"], titre="T3 parfait au dernier étage, terrasse de 14 m²"
    ).model_dump()
    r = generer_live(FauxClient([mauvaise, mauvaise]), *contexte("B31"))
    assert r.statut == "REVUE_HUMAINE" and r.violations


def test_schema_invalide_puis_correct(contexte, fiches):
    r = generer_live(FauxClient([{"titre": "x"}, fiches["B12"].model_dump()]), *contexte("B12"))
    assert r.statut == "PRETE" and r.tentatives == 2


def test_cache_lot_absent(contexte):
    r = generer_depuis_cache({}, *contexte("B31"))
    assert r.statut == "ECHEC" and r.fiche is None
