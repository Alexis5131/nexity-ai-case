from b2_pipeline.guardrails import controler_prix_appel, controler_surface, lots_generables
from b2_pipeline.plan_reader import LecturePlan, lire_plan_svg

from tests.conftest import ASSETS


def lecture(lot):
    return lire_plan_svg(ASSETS / lot.plan_2d_fichier)


def test_b22_mis_en_quarantaine_sur_ecart_de_surface(lots):
    c = controler_surface(lots["B22"], lecture(lots["B22"]))
    assert c.statut == "QUARANTAINE"
    assert c.ecart_m2 == -3.2
    assert c.ecart_pct == -4.9
    assert c.dans_tolerance_contractuelle  # sous les 5 % du contrat, mais l'annonce serait fausse
    assert "65,00" in c.motifs[0] and "61,80" in c.motifs[0]


def test_lots_conformes_passent(lots):
    for lot_id in ("B31", "B12"):
        assert controler_surface(lots[lot_id], lecture(lots[lot_id])).statut == "OK"


def test_plan_absent_bloque(lots):
    c = controler_surface(lots["B31"], None)
    assert c.statut == "QUARANTAINE" and "absent" in c.motifs[0]


def test_reference_de_plan_differente_bloque(lots):
    faux = LecturePlan("B99", 63.4, {"sejour": 63.4})
    c = controler_surface(lots["B31"], faux)
    assert c.statut == "QUARANTAINE" and "B99" in c.motifs[0]


def test_somme_des_pieces_incoherente_bloque(lots):
    faux = LecturePlan("B31", 63.4, {"sejour": 30.0, "chambre": 20.0})
    assert controler_surface(lots["B31"], faux).statut == "QUARANTAINE"


def test_prix_appel_recalcule_apres_vente_de_a04(donnees, lots):
    alerte = controler_prix_appel(donnees.flux.programme, list(lots.values()), {"B22"})
    assert alerte is not None
    assert alerte.ancien_lot == "A04" and alerte.ancien_prix == 269000
    assert alerte.nouveau_lot == "B12" and alerte.nouveau_prix == 279000
    assert "VENDU" in alerte.motif


def test_lot_en_quarantaine_exclu_du_prix_appel(donnees, lots):
    lots_mod = [
        lot.model_copy(update={"prix_ttc": 250000}) if lot.lot_id == "B22" else lot
        for lot in lots.values()
    ]
    alerte = controler_prix_appel(donnees.flux.programme, lots_mod, {"B22"})
    assert alerte is not None
    assert alerte.nouveau_lot == "B12"


def test_pas_d_alerte_si_prix_a_jour(donnees, lots):
    prog = donnees.flux.programme.model_copy(
        update={
            "prix_a_partir_de_affiche_page_programme": 279000,
            "lot_portant_prix_appel": "B12",
        }
    )
    assert controler_prix_appel(prog, list(lots.values()), {"B22"}) is None


def test_lots_generables_excluent_vendus_et_quarantaine(lots):
    controles = {k: controler_surface(lots[k], lecture(lots[k])) for k in ("B31", "B12", "B22")}
    assert [lot.lot_id for lot in lots_generables(list(lots.values()), controles)] == ["B31", "B12"]
