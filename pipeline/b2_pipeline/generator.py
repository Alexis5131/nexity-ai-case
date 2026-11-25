"""Redaction bornee des fiches lots par Claude, avec controles et cache de secours."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .llm import ClientOutil
from .models import FicheLot, Lot, Notice
from .plan_reader import LecturePlan
from .singularity import Singularite

CHAMPS_MBI_AUTORISES = [
    "typologie", "batiment", "etage", "dernier_etage", "surface_habitable_mbi",
    "exterieur_type", "exterieur_m2", "exposition", "parking_inclus",
]

SYSTEME_REDACTION = """Tu rédiges la fiche d'un appartement neuf vendu sur plan (VEFA) pour le site nexity.fr.
Règles impératives :
1. N'utilise que les faits fournis (flux MBI, plan de vente, singularités, clauses de notice). Aucun chiffre qui n'y figure pas.
2. Ouvre sur ce qui distingue ce lot de ses voisins : le titre ou l'accroche reprend au moins une singularité fournie.
3. N'écris aucun prix ni montant, aucune mention fiscale ou juridique (PTZ, TVA, prêt, garantie, rendement) : le site les ajoute lui-même depuis une bibliothèque validée.
4. Décris l'état de livraison prévu par la notice, sans l'embellir (par exemple, un coin cuisine livré sans meubles ni électroménager reste décrit ainsi).
5. Aucun adjectif invérifiable (exceptionnel, idéal, parfait, luxueux, sublime, sans vis-à-vis, vue imprenable).
6. Ton : un partenaire factuel qui apporte des preuves. Phrases courtes, français courant, pas de tiret long.
7. Chaque point fort cite ses sources avec les clés fournies ; chaque prestation cite l'identifiant de sa clause de notice.
8. Varie la structure d'un lot à l'autre : deux fiches d'un même programme ne doivent pas se ressembler."""

MOTS_INTERDITS = [
    "ptz", "tva", "prêt", "taux", "défiscal", "pinel", "lmnp", "rendement", "garanti",
    "exceptionnel", "idéal", "parfait", "luxueux", "prestigieux", "magnifique", "sublime",
    "incroyable", "sans vis-à-vis", "vue imprenable", "calme absolu", "cuisine équipée",
    "parquet massif", "bois massif",
]
SYMBOLES_INTERDITS = ["€", "—", "–"]

_RE_NOMBRE = re.compile(r"(?<![\w,.])(\d{1,3}(?:[   ]\d{3})+|\d+)(?:,(\d+))?")


@dataclass
class ResultatGeneration:
    lot_id: str
    fiche: FicheLot | None
    source: str  # "live" | "cache" | "cache (repli)"
    tentatives: int = 0
    violations: list[str] = field(default_factory=list)
    erreur: str | None = None

    @property
    def statut(self) -> str:
        if self.fiche is None:
            return "ECHEC"
        return "PRETE" if not self.violations else "REVUE_HUMAINE"


def schema_sans_refs(modele: type[FicheLot]) -> dict[str, Any]:
    """Schema JSON de l'outil avec les $ref remplaces par leur definition (plus robuste)."""
    schema = modele.model_json_schema()
    defs = schema.pop("$defs", {})

    def resoudre(noeud: Any) -> Any:
        if isinstance(noeud, dict):
            if "$ref" in noeud:
                return resoudre(defs[noeud["$ref"].split("/")[-1]])
            return {k: resoudre(v) for k, v in noeud.items()}
        if isinstance(noeud, list):
            return [resoudre(v) for v in noeud]
        return noeud

    return resoudre(schema)


def extraire_nombres(texte: str) -> list[float]:
    res = []
    for entier, decimales in _RE_NOMBRE.findall(texte):
        entier = re.sub(r"[   ]", "", entier)
        res.append(float(f"{entier}.{decimales}" if decimales else entier))
    return res


def sources_autorisees(lecture: LecturePlan, singularites: list[Singularite]) -> set[str]:
    return (
        {f"MBI.{c}" for c in CHAMPS_MBI_AUTORISES}
        | {f"PLAN.{p}" for p in lecture.pieces}
        | {f"SING.{s.code}" for s in singularites}
    )


def nombres_autorises(lot: Lot, lecture: LecturePlan, singularites: list[Singularite], notice: Notice) -> set[float]:
    vals = {float(lot.etage), lot.surface_habitable_mbi, lot.exterieur_m2, lecture.surface_cartouche}
    vals |= set(lecture.pieces.values())
    vals |= set(extraire_nombres(lot.parking_inclus or ""))
    for s in singularites:
        if not s.code.startswith("PRIX"):
            vals |= set(extraire_nombres(f"{s.valeur} {s.reference or ''}"))
    for c in notice.clauses_extraites:
        vals |= set(extraire_nombres(c.texte_contractuel))
    return {round(v, 2) for v in vals}


def textes_fiche(fiche: FicheLot) -> list[str]:
    return [fiche.titre, fiche.accroche, *(p.texte for p in fiche.points_forts), *(p.texte for p in fiche.prestations)]


def valider_fiche(
    fiche: FicheLot, lot: Lot, lecture: LecturePlan, singularites: list[Singularite], notice: Notice
) -> list[str]:
    violations: list[str] = []
    tout = " ".join(textes_fiche(fiche))
    bas = tout.lower()

    for mot in MOTS_INTERDITS:
        if re.search(rf"(?<!\w){re.escape(mot)}", bas):
            violations.append(f"Terme interdit : « {mot} »")
    for sym in SYMBOLES_INTERDITS:
        if sym in tout:
            violations.append(f"Symbole interdit : « {sym} »")

    permis = nombres_autorises(lot, lecture, singularites, notice)
    for n in extraire_nombres(tout):
        if round(n, 2) not in permis:
            violations.append(f"Chiffre non prouvé par les données : {n:g}")

    ids_notice = {c.id for c in notice.clauses_extraites}
    for p in fiche.prestations:
        if p.clause_id not in ids_notice:
            violations.append(f"Clause de notice inconnue : {p.clause_id}")
    if len({p.clause_id for p in fiche.prestations}) < len(fiche.prestations):
        violations.append("Deux prestations citent la même clause")

    permises = sources_autorisees(lecture, singularites)
    for p in fiche.points_forts:
        for s in p.sources:
            if s not in permises:
                violations.append(f"Source inconnue : {s}")
    if singularites and not any(s.startswith("SING.") for p in fiche.points_forts for s in p.sources):
        violations.append("Aucun point fort ne s'appuie sur une singularité du lot")
    return violations


def construire_faits(lot: Lot, lecture: LecturePlan, singularites: list[Singularite], notice: Notice) -> dict[str, Any]:
    return {
        "lot": {c: getattr(lot, c) for c in CHAMPS_MBI_AUTORISES},
        "plan_de_vente": {"surface_cartouche_m2": lecture.surface_cartouche, "pieces_m2": lecture.pieces},
        "singularites": [
            {"cle": f"SING.{s.code}", "libelle": s.libelle, "valeur": s.valeur, "reference": s.reference}
            for s in singularites if not s.code.startswith("PRIX")
        ] + [
            {"cle": f"SING.{s.code}", "libelle": s.libelle, "note": "ne pas citer de montant, le site l'affiche"}
            for s in singularites if s.code.startswith("PRIX")
        ],
        "clauses_notice": [
            {"id": c.id, "page": c.page, "rubrique": c.rubrique, "texte": c.texte_contractuel}
            for c in notice.clauses_extraites
        ],
        "sources_autorisees": sorted(sources_autorisees(lecture, singularites)),
    }


def generer_live(
    client: ClientOutil, lot: Lot, lecture: LecturePlan, singularites: list[Singularite],
    notice: Notice, max_tentatives: int = 2,
) -> ResultatGeneration:
    faits = construire_faits(lot, lecture, singularites, notice)
    consigne = "Rédige la fiche de ce lot à partir de ces faits :\n" + json.dumps(faits, ensure_ascii=False, indent=1)
    violations: list[str] = []
    fiche: FicheLot | None = None
    for tentative in range(1, max_tentatives + 1):
        texte = consigne
        if violations:
            texte += "\n\nTa version précédente a été refusée par les contrôles :\n- " + "\n- ".join(violations)
        brut = client.appeler_outil(
            SYSTEME_REDACTION, [{"type": "text", "text": texte}], "rediger_fiche_lot", schema_sans_refs(FicheLot)
        )
        try:
            fiche = FicheLot.model_validate(brut)
        except ValidationError as e:
            violations = [f"Schéma non respecté : {err['loc']} {err['msg']}" for err in e.errors()]
            fiche = None
            continue
        violations = valider_fiche(fiche, lot, lecture, singularites, notice)
        if not violations:
            return ResultatGeneration(lot.lot_id, fiche, "live", tentative)
    return ResultatGeneration(lot.lot_id, fiche, "live", max_tentatives, violations)


def charger_cache(chemin: str | Path) -> tuple[dict[str, FicheLot], dict[str, Any]]:
    p = Path(chemin)
    if not p.exists():
        return {}, {}
    brut = json.loads(p.read_text(encoding="utf-8"))
    return {k: FicheLot.model_validate(v) for k, v in brut.get("lots", {}).items()}, brut.get("meta", {})


def ecrire_cache(chemin: str | Path, fiches: dict[str, FicheLot], meta: dict[str, Any]) -> None:
    contenu = {"meta": meta, "lots": {k: v.model_dump() for k, v in fiches.items()}}
    Path(chemin).write_text(json.dumps(contenu, ensure_ascii=False, indent=2), encoding="utf-8")


def generer_depuis_cache(
    cache: dict[str, FicheLot], lot: Lot, lecture: LecturePlan, singularites: list[Singularite],
    notice: Notice, source: str = "cache", erreur: str | None = None,
) -> ResultatGeneration:
    fiche = cache.get(lot.lot_id)
    if fiche is None:
        return ResultatGeneration(lot.lot_id, None, source, 0, [], erreur or "Lot absent du cache")
    return ResultatGeneration(
        lot.lot_id, fiche, source, 0, valider_fiche(fiche, lot, lecture, singularites, notice), erreur
    )
