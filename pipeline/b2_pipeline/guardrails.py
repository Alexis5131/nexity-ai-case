"""Garde-fous amont (brique B3) : rien n'est genere sur une donnee douteuse."""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Lot, Programme
from .plan_reader import LecturePlan

TOLERANCE_SURFACE_M2 = 0.5
TOLERANCE_CONTRACTUELLE_VEFA = 0.05  # +/- 5 % (mention du plan de vente)


def fr(x: float, decimales: int = 2, signe: bool = False) -> str:
    texte = f"{x:+.{decimales}f}" if signe else f"{x:.{decimales}f}"
    return texte.replace(".", ",")


@dataclass
class ControleSurface:
    lot_id: str
    surface_mbi: float
    surface_plan: float | None
    somme_pieces: float | None
    ecart_m2: float | None
    ecart_pct: float | None
    statut: str  # "OK" | "QUARANTAINE"
    motifs: list[str] = field(default_factory=list)
    dans_tolerance_contractuelle: bool | None = None


@dataclass
class AlertePrixAppel:
    ancien_lot: str
    ancien_prix: int
    motif: str
    nouveau_lot: str | None
    nouveau_prix: int | None
    perimetre: str
    flux_complet: bool = True  # False : le nouveau prix n'est qu'un candidat a confirmer


def controler_surface(
    lot: Lot, lecture: LecturePlan | None, tolerance_m2: float = TOLERANCE_SURFACE_M2
) -> ControleSurface:
    if lecture is None:
        return ControleSurface(
            lot.lot_id,
            lot.surface_habitable_mbi,
            None,
            None,
            None,
            None,
            "QUARANTAINE",
            ["Plan de vente absent : surface non verifiable"],
        )

    ecart = round(lecture.surface_cartouche - lot.surface_habitable_mbi, 2)
    ecart_pct = round(ecart / lot.surface_habitable_mbi * 100, 1)
    motifs: list[str] = []
    if lecture.lot_id and lecture.lot_id != lot.lot_id:
        motifs.append(f"Le plan porte la reference {lecture.lot_id}, pas {lot.lot_id}")
    if abs(ecart) > tolerance_m2:
        motifs.append(
            f"Surface MBI {fr(lot.surface_habitable_mbi)} m² contre "
            f"{fr(lecture.surface_cartouche)} m² au cartouche (écart {fr(ecart, signe=True)} m²)"
        )
    if lecture.pieces and abs(lecture.somme_pieces - lecture.surface_cartouche) > tolerance_m2:
        motifs.append(
            f"Somme des pièces {fr(lecture.somme_pieces)} m² incohérente avec le cartouche"
        )
    return ControleSurface(
        lot_id=lot.lot_id,
        surface_mbi=lot.surface_habitable_mbi,
        surface_plan=lecture.surface_cartouche,
        somme_pieces=lecture.somme_pieces if lecture.pieces else None,
        ecart_m2=ecart,
        ecart_pct=ecart_pct,
        statut="QUARANTAINE" if motifs else "OK",
        motifs=motifs,
        dans_tolerance_contractuelle=abs(ecart_pct) <= TOLERANCE_CONTRACTUELLE_VEFA * 100,
    )


def controler_prix_appel(
    programme: Programme, lots: list[Lot], lots_en_quarantaine: set[str]
) -> AlertePrixAppel | None:
    """Alerte si le prix « à partir de » affiché ne correspond plus à un lot vendable."""
    candidats = [lot for lot in lots if lot.disponible and lot.lot_id not in lots_en_quarantaine]
    meilleur = min(candidats, key=lambda lot: lot.prix_ttc) if candidats else None
    porteur = next((lot for lot in lots if lot.lot_id == programme.lot_portant_prix_appel), None)
    affiche = programme.prix_a_partir_de_affiche_page_programme

    if porteur is not None and not porteur.disponible:
        motif = (
            f"Le lot {porteur.lot_id} qui porte le prix d'appel "
            f"est passé au statut {porteur.statut_mbi}"
        )
    elif meilleur is not None and meilleur.prix_ttc != affiche:
        motif = "Le prix affiché ne correspond plus au lot disponible le moins cher"
    else:
        return None

    nb_dispo_flux = sum(1 for lot in lots if lot.disponible)
    flux_complet = nb_dispo_flux >= programme.nb_lots_disponibles
    return AlertePrixAppel(
        ancien_lot=programme.lot_portant_prix_appel,
        ancien_prix=affiche,
        motif=motif,
        nouveau_lot=meilleur.lot_id if meilleur else None,
        nouveau_prix=meilleur.prix_ttc if meilleur else None,
        perimetre=(
            f"{len(candidats)} lot(s) disponible(s) et fiable(s) dans le flux reçu "
            f"({nb_dispo_flux} disponible(s) reçus sur {programme.nb_lots_disponibles} "
            "au programme)"
        ),
        flux_complet=flux_complet,
    )


def lots_generables(lots: list[Lot], controles: dict[str, ControleSurface]) -> list[Lot]:
    return [
        lot
        for lot in lots
        if lot.disponible
        and controles.get(lot.lot_id) is not None
        and controles[lot.lot_id].statut == "OK"
    ]
