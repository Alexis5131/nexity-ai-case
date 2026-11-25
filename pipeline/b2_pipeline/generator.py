"""Redaction bornee des fiches lots par Claude, avec controles et cache de secours."""

from __future__ import annotations

import hashlib
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
    "typologie",
    "batiment",
    "etage",
    "dernier_etage",
    "surface_habitable_mbi",
    "exterieur_type",
    "exterieur_m2",
    "exposition",
    "parking_inclus",
]

SYSTEME_REDACTION = (
    "Tu rédiges la fiche d'un appartement neuf vendu sur plan (VEFA) pour nexity.fr.\n"
    "Règles impératives :\n"
    "1. N'utilise que les faits fournis (MBI, plan, singularités, notice). Zéro chiffre inventé.\n"
    "2. Ouvre sur ce qui distingue ce lot : titre ou accroche appuyé sur une singularité fournie.\n"
    "3. Respecte strictement la portée des comparaisons (`portee_comparaison`) :\n"
    "   - si `echantillon_verifie`, n'emploie jamais « le seul », « la seule », « l'unique »,\n"
    "     « du programme » ou « de la résidence » ; écris uniquement « des T3 disponibles\n"
    "     vérifiés » ou « parmi les T3 disponibles vérifiés ».\n"
    "   - si `programme_complet`, toute comparaison doit préciser « disponibles ».\n"
    "4. Zéro prix, montant ou mention fiscale/juridique (PTZ, TVA, prêt, garantie, rendement).\n"
    "5. Décris l'état de livraison prévu par la notice sans l'embellir (coin cuisine nu).\n"
    "6. Aucun adjectif invérifiable (exceptionnel, idéal, parfait, luxueux, vue imprenable).\n"
    "7. Ton : partenaire factuel qui apporte des preuves. Phrases courtes, pas de tiret long.\n"
    "8. Chaque point fort cite ses sources ; chaque prestation cite l'id de sa clause de notice.\n"
    "9. Varie la structure d'un lot à l'autre pour éviter toute ressemblance."
)

MOTS_INTERDITS = [
    "ptz",
    "tva",
    "prêt",
    "taux",
    "défiscal",
    "pinel",
    "lmnp",
    "rendement",
    "garanti",
    "exceptionnel",
    "idéal",
    "parfait",
    "luxueux",
    "prestigieux",
    "magnifique",
    "sublime",
    "incroyable",
    "sans vis-à-vis",
    "vue imprenable",
    "calme absolu",
    "cuisine équipée",
    "parquet massif",
    "bois massif",
]
SYMBOLES_INTERDITS = ["€", "—", "–"]

_MOTIFS_PORTEE_ECHANTILLON: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bdu programme\b"), "du programme"),
    (re.compile(r"\bde la résidence\b"), "de la résidence"),
    (re.compile(r"\bde l['’]immeuble\b"), "de l'immeuble"),
    (re.compile(r"\ble seul\b"), "le seul"),
    (re.compile(r"\bla seule\b"), "la seule"),
    (re.compile(r"\bl['’]unique\b"), "l'unique"),
    (re.compile(r"\baucun autre\b"), "aucun autre"),
]
_RE_PORTEE_PROGRAMME_SANS_DISPO = re.compile(
    r"\b(?:le seul|la seule|le plus|la plus|les plus|meilleur|meilleure)\b"
    r"[^.!?]{0,80}\b(?:du programme|de la résidence)\b"
)

_RE_NOMBRE = re.compile(r"(?<![\w,.])(\d{1,3}(?:[   ]\d{3})+|\d+)(?:,(\d+))?")


@dataclass
class ResultatGeneration:
    lot_id: str
    fiche: FicheLot | None
    source: str  # "live" | "cache" | "cache (repli)"
    tentatives: int = 0
    violations: list[str] = field(default_factory=list)
    erreur: str | None = None
    empreinte_contexte: str | None = None

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

    resolu: dict[str, Any] = resoudre(schema)
    return resolu


def extraire_nombres(texte: str) -> list[float]:
    res = []
    for entier, decimales in _RE_NOMBRE.findall(texte):
        entier = re.sub(r"[   ]", "", entier)
        res.append(float(f"{entier}.{decimales}" if decimales else entier))
    return res


def sources_autorisees(lecture: LecturePlan, singularites: list[Singularite]) -> set[str]:
    return (
        {f"MBI.{c}" for c in CHAMPS_MBI_AUTORISES}
        | {f"PLAN.{p}" for p in lecture.pieces}
        | {f"SING.{s.code}" for s in singularites}
    )


def nombres_autorises(
    lot: Lot, lecture: LecturePlan, singularites: list[Singularite], notice: Notice
) -> set[float]:
    vals = {
        float(lot.etage),
        lot.surface_habitable_mbi,
        lot.exterieur_m2,
        lecture.surface_cartouche,
    }
    vals |= set(lecture.pieces.values())
    vals |= set(extraire_nombres(lot.parking_inclus or ""))
    for s in singularites:
        if not s.code.startswith("PRIX"):
            vals |= set(extraire_nombres(f"{s.valeur} {s.reference or ''}"))
    for c in notice.clauses_extraites:
        vals |= set(extraire_nombres(c.texte_contractuel))
    return {round(v, 2) for v in vals}


def textes_fiche(fiche: FicheLot) -> list[str]:
    return [
        fiche.titre,
        fiche.accroche,
        *(p.texte for p in fiche.points_forts),
        *(p.texte for p in fiche.prestations),
    ]


def _est_perimetre_complet(singularites: list[Singularite], perimetre_complet: bool | None) -> bool:
    if perimetre_complet is not None:
        return perimetre_complet
    return all(s.portee != "echantillon_verifie" for s in singularites)


def valider_fiche(
    fiche: FicheLot,
    lot: Lot,
    lecture: LecturePlan,
    singularites: list[Singularite],
    notice: Notice,
    *,
    perimetre_complet: bool | None = None,
) -> list[str]:
    violations: list[str] = []
    tout = " ".join(textes_fiche(fiche))
    bas = tout.lower()
    complet = _est_perimetre_complet(singularites, perimetre_complet)

    for mot in MOTS_INTERDITS:
        if re.search(rf"(?<!\w){re.escape(mot)}", bas):
            violations.append(f"Terme interdit : « {mot} »")
    for sym in SYMBOLES_INTERDITS:
        if sym in tout:
            violations.append(f"Symbole interdit : « {sym} »")

    if not complet:
        for motif, libelle in _MOTIFS_PORTEE_ECHANTILLON:
            if motif.search(bas):
                violations.append(f"Portée non prouvée sur échantillon partiel : « {libelle} »")
    else:
        for segment in re.split(r"[.!?]+", bas):
            if _RE_PORTEE_PROGRAMME_SANS_DISPO.search(segment) and "disponible" not in segment:
                violations.append("Portée comparative non bornée aux lots disponibles")
                break

    permis = nombres_autorises(lot, lecture, singularites, notice)
    for n in extraire_nombres(tout):
        if round(n, 2) not in permis:
            violations.append(f"Chiffre non prouvé par les données : {n:g}")

    ids_notice = {c.id for c in notice.clauses_extraites}
    for prest in fiche.prestations:
        if prest.clause_id not in ids_notice:
            violations.append(f"Clause de notice inconnue : {prest.clause_id}")
    if len({prest.clause_id for prest in fiche.prestations}) < len(fiche.prestations):
        violations.append("Deux prestations citent la même clause")

    permises = sources_autorisees(lecture, singularites)
    for pf in fiche.points_forts:
        for s in pf.sources:
            if s not in permises:
                violations.append(f"Source inconnue : {s}")
    if singularites and not any(
        s.startswith("SING.") for pf in fiche.points_forts for s in pf.sources
    ):
        violations.append("Aucun point fort ne s'appuie sur une singularité du lot")
    return violations


def construire_faits(
    lot: Lot,
    lecture: LecturePlan,
    singularites: list[Singularite],
    notice: Notice,
    *,
    perimetre_complet: bool | None = None,
) -> dict[str, Any]:
    complet = _est_perimetre_complet(singularites, perimetre_complet)
    return {
        "portee_comparaison": "programme_complet" if complet else "echantillon_verifie",
        "lot": {c: getattr(lot, c) for c in CHAMPS_MBI_AUTORISES},
        "plan_de_vente": {
            "surface_cartouche_m2": lecture.surface_cartouche,
            "pieces_m2": lecture.pieces,
        },
        "singularites": [
            {
                "cle": f"SING.{s.code}",
                "libelle": s.libelle,
                "valeur": s.valeur,
                "reference": s.reference,
                "portee": s.portee,
            }
            for s in singularites
            if not s.code.startswith("PRIX")
        ]
        + [
            {
                "cle": f"SING.{s.code}",
                "libelle": s.libelle,
                "portee": s.portee,
                "note": "ne pas citer de montant, le site l'affiche",
            }
            for s in singularites
            if s.code.startswith("PRIX")
        ],
        "clauses_notice": [
            {"id": c.id, "page": c.page, "rubrique": c.rubrique, "texte": c.texte_contractuel}
            for c in notice.clauses_extraites
        ],
        "sources_autorisees": sorted(sources_autorisees(lecture, singularites)),
    }


def empreinte_contexte(
    lot: Lot,
    lecture: LecturePlan,
    singularites: list[Singularite],
    notice: Notice,
    *,
    perimetre_complet: bool | None = None,
) -> str:
    """Empreinte deterministe des faits du lot ET des singularites liees aux voisins disponibles.

    Si un lot voisin est vendu, corrige ou modifie dans le flux suivant, ses singularites
    voisines changent -> l'empreinte change et declenche la regeneration de la fiche.
    """
    faits = construire_faits(
        lot, lecture, singularites, notice, perimetre_complet=perimetre_complet
    )
    canonique = json.dumps(faits, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonique.encode("utf-8")).hexdigest()[:12]


def generer_live(
    client: ClientOutil,
    lot: Lot,
    lecture: LecturePlan,
    singularites: list[Singularite],
    notice: Notice,
    max_tentatives: int = 2,
    *,
    perimetre_complet: bool | None = None,
) -> ResultatGeneration:
    faits = construire_faits(
        lot, lecture, singularites, notice, perimetre_complet=perimetre_complet
    )
    emp = empreinte_contexte(
        lot, lecture, singularites, notice, perimetre_complet=perimetre_complet
    )
    consigne = "Rédige la fiche de ce lot à partir de ces faits :\n" + json.dumps(
        faits, ensure_ascii=False, indent=1
    )
    violations: list[str] = []
    fiche: FicheLot | None = None
    for tentative in range(1, max_tentatives + 1):
        texte = consigne
        if violations:
            texte += (
                "\n\nTa version précédente a été refusée par les contrôles :\n- "
                + "\n- ".join(violations)
            )
        brut = client.appeler_outil(
            SYSTEME_REDACTION,
            [{"type": "text", "text": texte}],
            "rediger_fiche_lot",
            schema_sans_refs(FicheLot),
        )
        try:
            fiche = FicheLot.model_validate(brut)
        except ValidationError as e:
            violations = [f"Schéma non respecté : {err['loc']} {err['msg']}" for err in e.errors()]
            fiche = None
            continue
        violations = valider_fiche(
            fiche, lot, lecture, singularites, notice, perimetre_complet=perimetre_complet
        )
        if not violations:
            return ResultatGeneration(lot.lot_id, fiche, "live", tentative, [], None, emp)
    return ResultatGeneration(lot.lot_id, fiche, "live", max_tentatives, violations, None, emp)


def charger_cache(chemin: str | Path) -> tuple[dict[str, FicheLot], dict[str, Any]]:
    p = Path(chemin)
    if not p.exists():
        return {}, {}
    brut = json.loads(p.read_text(encoding="utf-8"))
    return (
        {k: FicheLot.model_validate(v) for k, v in brut.get("lots", {}).items()},
        brut.get("meta", {}),
    )


def ecrire_cache(chemin: str | Path, fiches: dict[str, FicheLot], meta: dict[str, Any]) -> None:
    contenu = {"meta": meta, "lots": {k: v.model_dump() for k, v in fiches.items()}}
    Path(chemin).write_text(
        json.dumps(contenu, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def generer_depuis_cache(
    cache: dict[str, FicheLot],
    lot: Lot,
    lecture: LecturePlan,
    singularites: list[Singularite],
    notice: Notice,
    source: str = "cache",
    erreur: str | None = None,
    *,
    perimetre_complet: bool | None = None,
    empreintes_cache: dict[str, str] | None = None,
) -> ResultatGeneration:
    emp = empreinte_contexte(
        lot, lecture, singularites, notice, perimetre_complet=perimetre_complet
    )
    fiche = cache.get(lot.lot_id)
    if fiche is None:
        return ResultatGeneration(
            lot.lot_id, None, source, 0, [], erreur or "Lot absent du cache", emp
        )
    violations = valider_fiche(
        fiche, lot, lecture, singularites, notice, perimetre_complet=perimetre_complet
    )
    if empreintes_cache and lot.lot_id in empreintes_cache and empreintes_cache[lot.lot_id] != emp:
        violations.append(
            "Contexte stock modifié (lot voisin vendu ou mis à jour) : régénération requise"
        )
    return ResultatGeneration(lot.lot_id, fiche, source, 0, violations, erreur, emp)
