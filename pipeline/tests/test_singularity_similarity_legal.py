from dataclasses import replace

from b2_pipeline.generator import charger_cache
from b2_pipeline.legal import assembler_mentions, verifier_integrite
from b2_pipeline.models import FicheLot
from b2_pipeline.similarity import (
    SEUIL_SIMILARITE,
    bigrammes,
    jaccard,
    similarite,
    similarites_paires,
)
from b2_pipeline.singularity import calculer_singularites, evaluer_perimetre

from tests.conftest import CACHE


def codes(sings):
    return {s.code for s in sings}


def test_singularites_b31_et_b12(lots):
    voisins = [lots["B31"], lots["B12"]]
    assert codes(calculer_singularites(lots["B31"], voisins)) == {
        "DERNIER_ETAGE",
        "PLUS_GRAND_EXTERIEUR",
        "TRAVERSANT",
        "PLUS_GRANDE_SURFACE",
    }
    assert codes(calculer_singularites(lots["B12"], voisins)) == {
        "COEUR_ILOT",
        "PRIX_ACCES",
        "PRIX_M2",
    }


def test_perimetre_borne_sur_echantillon_partiel(donnees, lots):
    perim = evaluer_perimetre("T3", donnees.flux.programme, list(lots.values()), {"B22"})
    assert perim.complet is False
    assert perim.nb_comparables == 2
    assert perim.lots_meme_typo_quarantaine == ["B22"]
    sings = calculer_singularites(lots["B31"], [lots["B31"], lots["B12"]], perimetre_complet=False)
    assert all(s.portee == "echantillon_verifie" and "Seul" not in s.libelle for s in sings)


def test_valeurs_formatees_en_francais(lots):
    sings = {s.code: s for s in calculer_singularites(lots["B31"], [lots["B12"]])}
    assert sings["PLUS_GRAND_EXTERIEUR"].valeur == "14,0 m²"
    assert sings["PLUS_GRAND_EXTERIEUR"].reference == "6,5 m² pour le suivant"


def test_aucune_singularite_sans_voisin(lots):
    assert calculer_singularites(lots["B31"], [lots["B31"]]) == []


def test_jaccard_bornes():
    a = bigrammes("terrasse plein ciel orientée sud ouest")
    assert jaccard(a, a) == 1.0
    assert jaccard(a, bigrammes("balcon couvert jardin intérieur")) == 0.0
    assert jaccard(set(), set()) == 0.0


def test_fiches_du_cache_sous_le_seuil():
    fiches, _ = charger_cache(CACHE)
    paires = similarites_paires(fiches)
    assert paires and all(p["score"] < SEUIL_SIMILARITE and p["statut"] == "OK" for p in paires)


def test_fiche_copiee_declenche_l_alerte():
    fiches, _ = charger_cache(CACHE)
    copie = FicheLot.model_validate(fiches["B31"].model_dump())
    assert similarite(fiches["B31"], copie) == 1.0
    assert similarites_paires({"X": fiches["B31"], "Y": copie})[0]["statut"] == "ALERTE"


def test_mentions_assemblees_mot_pour_mot(donnees, lots):
    mentions = assembler_mentions(lots["B31"], donnees.mentions)
    assert [m.id for m in mentions] == [
        "LEGAL_VEFA_PRIX",
        "LEGAL_PTZ_2026",
        "LEGAL_VISUEL_NON_CONTRACTUEL",
    ]
    assert all(m.texte == donnees.mentions.mentions[m.id] for m in mentions)
    assert verifier_integrite(mentions, donnees.mentions)


def test_mention_ptz_absente_si_lot_non_eligible(donnees, lots):
    lot = lots["B31"].model_copy(update={"ptz_eligible": False})
    assert "LEGAL_PTZ_2026" not in [m.id for m in assembler_mentions(lot, donnees.mentions)]


def test_mention_alteree_detectee(donnees, lots):
    mentions = assembler_mentions(lots["B31"], donnees.mentions)
    alteree = [replace(mentions[0], texte=mentions[0].texte + " Offre exceptionnelle.")] + mentions[
        1:
    ]
    assert not verifier_integrite(alteree, donnees.mentions)
