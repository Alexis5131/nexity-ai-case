import pytest
from b2_pipeline.plan_reader import analyser_textes_plan, lire_plan_svg, lire_plan_vision, slug

from tests.conftest import ASSETS, FauxClient

PLANS = {
    "B31": ("plans-2d/lot-B31-T3-dernier-etage-63.4m2.svg", 63.4, 14.0, "Sud-Ouest"),
    "B12": ("plans-2d/lot-B12-T3-coeur-ilot-61.2m2.svg", 61.2, 6.5, "Sud-Est"),
    "B22": ("plans-2d/lot-B22-T3-2e-etage-61.8m2.svg", 61.8, 5.8, "Ouest"),
}


@pytest.mark.parametrize("lot_id", PLANS)
def test_lecture_svg_des_trois_plans(lot_id):
    fichier, surface, exterieur, expo = PLANS[lot_id]
    lecture = lire_plan_svg(ASSETS / fichier)
    assert lecture.lot_id == lot_id
    assert lecture.surface_cartouche == surface
    assert lecture.somme_pieces == pytest.approx(surface)
    assert lecture.exterieur_m2 == exterieur
    assert lecture.exposition == expo
    assert set(lecture.pieces) == {
        "sejour_cuisine",
        "chambre_1",
        "chambre_2",
        "salle_de_bain_wc",
        "entree_degagement",
    }


def test_cartouche_absent_leve_une_erreur():
    with pytest.raises(ValueError):
        analyser_textes_plan(["LOT X1 — T2", "12,00 m²"])


def test_slug_retire_accents_et_ponctuation():
    assert slug("Entrée / Dégagement") == "entree_degagement"
    assert slug("Salle de bain + WC") == "salle_de_bain_wc"


def test_lecture_vision_convertit_la_reponse_de_claude(tmp_path):
    png = tmp_path / "plan.png"
    png.write_bytes(b"\x89PNG fake")
    client = FauxClient(
        [
            {
                "lot_id": "B22",
                "surface_cartouche_m2": 61.8,
                "pieces": [
                    {"nom": "Séjour / Cuisine", "m2": 26.1},
                    {"nom": "Chambre 1", "m2": 12.0},
                ],
                "exterieur_m2": 5.8,
                "exposition": "Ouest",
            }
        ]
    )
    lecture = lire_plan_vision(png, client)
    assert lecture.source == "vision"
    assert lecture.surface_cartouche == 61.8
    assert lecture.pieces == {"sejour_cuisine": 26.1, "chambre_1": 12.0}
    image = client.appels[0]["contenu"][0]
    assert image["type"] == "image" and image["source"]["media_type"] == "image/png"
