import json
import shutil

from b2_pipeline.plan_reader import lire_plan_svg
from b2_pipeline.run import executer, lots_impactes_par_mouvement_stock

from tests.conftest import ASSETS, CACHE, FauxClient


def test_bout_en_bout_mode_cache():
    r = executer(ASSETS, mode="cache", chemin_cache=CACHE)
    assert r["meta"]["mode_effectif"] == "cache"
    statuts = {c["lot_id"]: c["statut"] for c in r["etape1_surfaces"]}
    assert statuts == {"B31": "OK", "B12": "OK", "B22": "QUARANTAINE"}
    assert r["etape2_prix_appel"]["nouveau_prix"] == 279000
    assert r["etape2_exclus"] == [
        {"lot_id": "A04", "statut": "VENDU", "depuis": "2026-11-25T08:00:00Z"}
    ]
    assert r["etape3_perimetres"]["T3"]["complet"] is False
    assert set(r["etape4_generation"]) == {"B31", "B12"}
    assert all(
        g["statut"] == "PRETE" and g["mentions_intactes"] and g["empreinte_contexte"]
        for g in r["etape4_generation"].values()
    )
    assert r["lots_temoins"][0]["lot_id"] == "B31"
    json.dumps(r, ensure_ascii=False)


def test_mode_cache_est_deterministe():
    r1 = executer(ASSETS, mode="cache", chemin_cache=CACHE)
    r2 = executer(ASSETS, mode="cache", chemin_cache=CACHE)
    assert r1 == r2


def test_mouvement_stock_identifie_les_lots_a_regenerer(donnees):
    lots_avant = donnees.flux.lots
    lectures = {
        lot.lot_id: lire_plan_svg(ASSETS / lot.plan_2d_fichier)
        for lot in lots_avant
        if lot.plan_2d_fichier
    }
    lots_apres = [
        lot.model_copy(update={"statut_mbi": "VENDU"}) if lot.lot_id == "B12" else lot
        for lot in lots_avant
    ]
    impactes = lots_impactes_par_mouvement_stock(
        lots_avant, lots_apres, lectures, donnees.notice, donnees.flux.programme
    )
    assert impactes == ["B31"]


def test_mode_live_avec_repli_sur_cache_si_erreur(tmp_path):
    cache = tmp_path / "cache.json"
    shutil.copy(CACHE, cache)
    client = FauxClient([RuntimeError("réseau coupé")] * 10)
    r = executer(ASSETS, mode="live", chemin_cache=cache, client=client)
    assert {c["lot_id"]: c["statut"] for c in r["etape1_surfaces"]}["B22"] == "QUARANTAINE"
    assert all(g["source"] == "cache (repli)" for g in r["etape4_generation"].values())
    assert all(
        lot["source_lecture"].startswith("texte SVG (repli")
        for lot in r["lots"]
        if lot["source_lecture"]
    )


def test_mode_live_reussi_met_a_jour_le_cache(tmp_path):
    cache = tmp_path / "cache.json"
    shutil.copy(CACHE, cache)
    fiches = json.loads(CACHE.read_text(encoding="utf-8"))["lots"]
    vision = {
        "B31": {
            "lot_id": "B31",
            "surface_cartouche_m2": 63.4,
            "pieces": [
                {"nom": "Séjour / Cuisine", "m2": 27.2},
                {"nom": "Chambre 1", "m2": 12.4},
                {"nom": "Chambre 2", "m2": 10.3},
                {"nom": "Salle de bain + WC", "m2": 7.7},
                {"nom": "Entrée / Dégagement", "m2": 5.8},
            ],
        },
        "B12": {
            "lot_id": "B12",
            "surface_cartouche_m2": 61.2,
            "pieces": [
                {"nom": "Séjour / Cuisine", "m2": 25.8},
                {"nom": "Chambre 1", "m2": 11.8},
                {"nom": "Chambre 2", "m2": 10.1},
                {"nom": "Salle de bain + WC", "m2": 7.4},
                {"nom": "Entrée / Dégagement", "m2": 6.1},
            ],
        },
        "B22": {
            "lot_id": "B22",
            "surface_cartouche_m2": 61.8,
            "pieces": [
                {"nom": "Séjour / Cuisine", "m2": 26.1},
                {"nom": "Chambre 1", "m2": 12.0},
                {"nom": "Chambre 2", "m2": 10.2},
                {"nom": "Salle de bain + WC", "m2": 7.6},
                {"nom": "Entrée / Dégagement", "m2": 5.9},
            ],
        },
    }
    client = FauxClient([vision["B31"], vision["B12"], vision["B22"], fiches["B31"], fiches["B12"]])
    r = executer(ASSETS, mode="live", chemin_cache=cache, client=client)
    assert r["meta"]["mode_effectif"] == "live"
    assert all(
        g["statut"] == "PRETE" and g["source"] == "live" for g in r["etape4_generation"].values()
    )
    meta_maj = json.loads(cache.read_text(encoding="utf-8"))["meta"]
    assert meta_maj["source"] == "live faux-modele"
    assert set(meta_maj["empreintes"]) == {"B31", "B12"}
