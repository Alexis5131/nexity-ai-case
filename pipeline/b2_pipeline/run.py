"""Execution complete du pipeline B2 et ecriture du resultat lu par la demo.

Usage :
    uv run --directory pipeline python -m b2_pipeline.run \\
        --assets ../assets --out out/demo_result.json --mode auto
Modes :
    cache : textes pre-generes (aucun reseau)            -> demo sans risque et deterministe
    live  : lecture des plans et redaction par Claude    -> necessite ANTHROPIC_API_KEY
    auto  : live si une cle est presente, sinon cache ; repli sur le cache en cas d'erreur
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .generator import (
    ResultatGeneration,
    charger_cache,
    ecrire_cache,
    empreinte_contexte,
    extraire_nombres,
    generer_depuis_cache,
    generer_live,
    textes_fiche,
)
from .guardrails import (
    ControleSurface,
    controler_prix_appel,
    controler_surface,
    lots_generables,
)
from .legal import assembler_mentions, verifier_integrite
from .llm import ClientClaude, ClientOutil, cle_api_disponible
from .loaders import DonneesDemo, charger_donnees
from .models import FicheLot, Lot, Notice, Programme
from .plan_reader import LecturePlan, lire_plan_svg, lire_plan_vision
from .similarity import similarites_paires
from .singularity import (
    PerimetreComparaison,
    Singularite,
    calculer_singularites,
    evaluer_perimetre,
    prix_m2,
)

logger = logging.getLogger(__name__)

RACINE = Path(__file__).resolve().parents[2]
CACHE_PAR_DEFAUT = RACINE / "pipeline" / "cache" / "generation_cache.json"
MINUTES_MANUELLES_PAR_LOT = 36  # test de l'equipe contenu : 3 jours pour 40 lots (interview 4)
MINUTES_RELECTURE_LOT_TEMOIN = 4
HORODATAGE_CACHE_DEFAUT = "2026-11-25T12:00:00+01:00"


def _chrono() -> float:
    return time.perf_counter()


def lire_plans(
    donnees: DonneesDemo,
    lots: list[Lot],
    client: ClientOutil | None,
    live: bool,
) -> tuple[dict[str, LecturePlan], dict[str, str]]:
    lectures: dict[str, LecturePlan] = {}
    sources: dict[str, str] = {}
    for lot in lots:
        if not lot.plan_2d_fichier:
            continue
        svg = donnees.chemin(lot.plan_2d_fichier)
        if live and client is not None:
            try:
                lectures[lot.lot_id] = lire_plan_vision(svg.with_suffix(".png"), client)
                sources[lot.lot_id] = "vision Claude (PNG)"
                continue
            except Exception as e:
                logger.warning("Repli SVG sur le plan %s apres erreur Vision : %s", lot.lot_id, e)
                sources[lot.lot_id] = f"texte SVG (repli : {type(e).__name__})"
        else:
            sources[lot.lot_id] = "texte SVG"
        lectures[lot.lot_id] = lire_plan_svg(svg)
    return lectures, sources


def _resume_controles(r: ResultatGeneration, notice: Notice) -> dict[str, Any] | None:
    if r.fiche is None:
        return None
    return {
        "chiffres_verifies": extraire_nombres(" ".join(textes_fiche(r.fiche))),
        "clauses_citees": [
            {"id": p.clause_id, "page": c.page if (c := notice.clause(p.clause_id)) else None}
            for p in r.fiche.prestations
        ],
        "sources_citees": sorted({s for p in r.fiche.points_forts for s in p.sources}),
        "empreinte_contexte": r.empreinte_contexte,
        "violations": len(r.violations),
    }


def choisir_lots_temoins(
    generes: dict[str, ResultatGeneration],
    lots: list[Lot],
    singularites: dict[str, list[Singularite]],
) -> list[dict[str, Any]]:
    par_typo: dict[str, list[Lot]] = {}
    for lot in lots:
        if lot.lot_id in generes and generes[lot.lot_id].fiche is not None:
            par_typo.setdefault(lot.typologie, []).append(lot)
    temoins: list[dict[str, Any]] = []
    for typo, groupe in par_typo.items():
        temoin = max(groupe, key=lambda item: (len(singularites[item.lot_id]), item.prix_ttc))
        temoins.append(
            {
                "typologie": typo,
                "lot_id": temoin.lot_id,
                "lots_couverts": [item.lot_id for item in groupe],
                "raison": (
                    "Lot qui porte le plus de singularités : "
                    "c'est là que le risque de surpromesse est le plus fort."
                ),
            }
        )
    return temoins


def _empreintes_stock(
    lots: list[Lot],
    lectures: dict[str, LecturePlan],
    notice: Notice,
    programme: Programme,
) -> dict[str, str]:
    disponibles = [lot for lot in lots if lot.disponible]
    controles = {
        lot.lot_id: controler_surface(lot, lectures.get(lot.lot_id)) for lot in disponibles
    }
    quarantaine = {k for k, c in controles.items() if c.statut == "QUARANTAINE"}
    generables = lots_generables(lots, controles)
    perimetres = {
        typo: evaluer_perimetre(typo, programme, lots, quarantaine)
        for typo in {lot.typologie for lot in generables}
    }
    res: dict[str, str] = {}
    for lot in generables:
        complet = perimetres[lot.typologie].complet
        sings = calculer_singularites(lot, generables, perimetre_complet=complet)
        res[lot.lot_id] = empreinte_contexte(
            lot, lectures[lot.lot_id], sings, notice, perimetre_complet=complet
        )
    return res


def lots_impactes_par_mouvement_stock(
    lots_avant: list[Lot],
    lots_apres: list[Lot],
    lectures: dict[str, LecturePlan],
    notice: Notice,
    programme: Programme,
) -> list[str]:
    """Identifie les lots disponibles dont la fiche doit etre regeneree apres mouvement du stock."""
    avant = _empreintes_stock(lots_avant, lectures, notice, programme)
    apres = _empreintes_stock(lots_apres, lectures, notice, programme)
    return sorted(lot_id for lot_id, emp in apres.items() if avant.get(lot_id) != emp)


def executer(
    racine_assets: str | Path,
    mode: str = "auto",
    chemin_cache: str | Path = CACHE_PAR_DEFAUT,
    client: ClientOutil | None = None,
) -> dict[str, Any]:
    debut = _chrono()
    donnees = charger_donnees(racine_assets)
    programme, lots, notice = donnees.flux.programme, donnees.flux.lots, donnees.notice
    cache, meta_cache = charger_cache(chemin_cache)
    empreintes_cache: dict[str, str] = dict(meta_cache.get("empreintes", {}))

    live = mode == "live" or (mode == "auto" and cle_api_disponible())
    erreur_init: str | None = None
    if live and client is None and not cle_api_disponible():
        live, erreur_init = False, "ANTHROPIC_API_KEY absente : mode cache"
    if live and client is None:
        try:
            client = ClientClaude()
        except Exception as e:
            logger.warning("Impossible d'initialiser ClientClaude, repli sur le cache : %s", e)
            live, erreur_init = False, f"{type(e).__name__}: {e}"

    # Etape 1 : lecture des plans et controle des surfaces (lots disponibles uniquement)
    t = _chrono()
    disponibles = [lot for lot in lots if lot.disponible]
    lectures, sources_lecture = lire_plans(donnees, disponibles, client, live)
    controles: dict[str, ControleSurface] = {
        lot.lot_id: controler_surface(lot, lectures.get(lot.lot_id)) for lot in disponibles
    }
    quarantaine = {k for k, c in controles.items() if c.statut == "QUARANTAINE"}
    duree_1 = _chrono() - t

    # Etape 2 : statuts et prix d'appel
    t = _chrono()
    alerte_prix = controler_prix_appel(programme, lots, quarantaine)
    exclus = [
        {"lot_id": lot.lot_id, "statut": lot.statut_mbi, "depuis": lot.date_changement_statut}
        for lot in lots
        if not lot.disponible
    ]
    duree_2 = _chrono() - t

    # Etape 3 : perimetre de comparaison et singularite intra-programme
    t = _chrono()
    generables = lots_generables(lots, controles)
    perimetres: dict[str, PerimetreComparaison] = {
        typo: evaluer_perimetre(typo, programme, lots, quarantaine)
        for typo in sorted({lot.typologie for lot in generables})
    }
    singularites: dict[str, list[Singularite]] = {
        lot.lot_id: calculer_singularites(
            lot, generables, perimetre_complet=perimetres[lot.typologie].complet
        )
        for lot in generables
    }
    duree_3 = _chrono() - t

    # Etape 4 : redaction bornee + controles
    t = _chrono()
    generes: dict[str, ResultatGeneration] = {}
    for lot in generables:
        lecture, sings = lectures[lot.lot_id], singularites[lot.lot_id]
        complet = perimetres[lot.typologie].complet
        if live and client is not None:
            try:
                generes[lot.lot_id] = generer_live(
                    client, lot, lecture, sings, notice, perimetre_complet=complet
                )
                continue
            except Exception as e:
                logger.warning("Repli cache pour la fiche %s apres erreur live : %s", lot.lot_id, e)
                generes[lot.lot_id] = generer_depuis_cache(
                    cache,
                    lot,
                    lecture,
                    sings,
                    notice,
                    "cache (repli)",
                    f"{type(e).__name__}: {e}",
                    perimetre_complet=complet,
                    empreintes_cache=empreintes_cache,
                )
                continue
        generes[lot.lot_id] = generer_depuis_cache(
            cache,
            lot,
            lecture,
            sings,
            notice,
            perimetre_complet=complet,
            empreintes_cache=empreintes_cache,
        )
    fiches: dict[str, FicheLot] = {k: r.fiche for k, r in generes.items() if r.fiche is not None}
    similarite = similarites_paires(fiches)
    mentions = {lot.lot_id: assembler_mentions(lot, donnees.mentions) for lot in generables}
    duree_4 = _chrono() - t

    # Etape 5 : lot-temoin par typologie
    temoins = choisir_lots_temoins(generes, generables, singularites)

    nouvelles: dict[str, FicheLot] = {
        k: r.fiche
        for k, r in generes.items()
        if r.source == "live" and r.statut == "PRETE" and r.fiche is not None
    }
    nouvelles_emp = {
        k: r.empreinte_contexte
        for k, r in generes.items()
        if k in nouvelles and r.empreinte_contexte is not None
    }
    if nouvelles and client is not None:
        ecrire_cache(
            chemin_cache,
            {**cache, **nouvelles},
            {
                "source": f"live {client.modele}",
                "genere_le": datetime.now(UTC).isoformat(),
                "empreintes": {**empreintes_cache, **nouvelles_emp},
            },
        )

    sources_gen = {r.source for r in generes.values()}
    mode_effectif = "live" if "live" in sources_gen else "cache"
    rejeu_cache_pur = mode_effectif == "cache" and erreur_init is None and sources_gen <= {"cache"}
    execute_le = (
        str(meta_cache.get("genere_le") or HORODATAGE_CACHE_DEFAUT)
        if rejeu_cache_pur
        else datetime.now(UTC).isoformat()
    )
    duree_totale = 0.0 if rejeu_cache_pur else round(_chrono() - debut, 2)
    durees_etapes = (
        [0.0, 0.0, 0.0, 0.0]
        if rejeu_cache_pur
        else [round(d, 3) for d in (duree_1, duree_2, duree_3, duree_4)]
    )

    return {
        "meta": {
            "mode_demande": mode,
            "mode_effectif": mode_effectif,
            "modele": getattr(client, "modele", None),
            "cache": meta_cache,
            "erreur_initialisation": erreur_init,
            "execute_le": execute_le,
            "duree_totale_s": duree_totale,
            "durees_etapes_s": durees_etapes,
        },
        "programme": programme.model_dump(),
        "lots": [
            {
                **lot.model_dump(),
                "prix_m2": round(prix_m2(lot)),
                "lecture_plan": (asdict(lectures[lot.lot_id]) if lot.lot_id in lectures else None),
                "source_lecture": sources_lecture.get(lot.lot_id),
            }
            for lot in lots
        ],
        "notice": notice.model_dump(),
        "etape1_surfaces": [asdict(c) for c in controles.values()],
        "etape2_prix_appel": asdict(alerte_prix) if alerte_prix else None,
        "etape2_exclus": exclus,
        "etape3_perimetres": {
            k: {**asdict(p), "formulation_autorisee": p.formulation_autorisee}
            for k, p in perimetres.items()
        },
        "etape3_singularites": {k: [asdict(s) for s in v] for k, v in singularites.items()},
        "etape4_generation": {
            k: {
                "statut": r.statut,
                "source": r.source,
                "tentatives": r.tentatives,
                "violations": r.violations,
                "erreur": r.erreur,
                "empreinte_contexte": r.empreinte_contexte,
                "fiche": r.fiche.model_dump() if r.fiche else None,
                "mentions": [asdict(m) for m in mentions[k]],
                "mentions_intactes": verifier_integrite(mentions[k], donnees.mentions),
                "controles": _resume_controles(r, notice),
            }
            for k, r in generes.items()
        },
        "similarite": similarite,
        "lots_temoins": temoins,
        "productivite": {
            "minutes_manuelles_par_lot": MINUTES_MANUELLES_PAR_LOT,
            "minutes_relecture_temoin": MINUTES_RELECTURE_LOT_TEMOIN,
            "lots_generes": len(fiches),
            "lots_a_relire": len(temoins),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline B2 : pages lots différenciées")
    parser.add_argument("--assets", default=str(RACINE / "assets"))
    parser.add_argument("--out", default=str(RACINE / "pipeline" / "out" / "demo_result.json"))
    parser.add_argument("--mode", choices=["auto", "cache", "live"], default="auto")
    parser.add_argument("--cache", default=str(CACHE_PAR_DEFAUT))
    args = parser.parse_args()

    resultat = executer(args.assets, args.mode, args.cache)
    sortie = Path(args.out)
    sortie.parent.mkdir(parents=True, exist_ok=True)
    texte = json.dumps(resultat, ensure_ascii=False, indent=2) + "\n"
    if not sortie.exists() or sortie.read_text(encoding="utf-8") != texte:
        sortie.write_text(texte, encoding="utf-8")

    m = resultat["meta"]
    print(f"Mode : {m['mode_effectif']} ({m['duree_totale_s']} s)")
    for c in resultat["etape1_surfaces"]:
        print(f"  {c['lot_id']} : {c['statut']} {'; '.join(c['motifs'])}")
    if resultat["etape2_prix_appel"]:
        a = resultat["etape2_prix_appel"]
        print(
            f"  Prix d'appel : {a['ancien_prix']} ({a['ancien_lot']}) "
            f"-> {a['nouveau_prix']} ({a['nouveau_lot']})"
        )
    for k, g in resultat["etape4_generation"].items():
        print(f"  {k} : {g['statut']} via {g['source']} {g['violations'] or ''}")
    for s in resultat["similarite"]:
        print(f"  Similarité {s['paire']} : {s['score']:.0%} ({s['statut']})")
    print(f"Résultat écrit dans {sortie}")


if __name__ == "__main__":
    main()
