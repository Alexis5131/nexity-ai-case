"""Schemas des donnees d'entree (flux MBI, notice, mentions) et de la sortie LLM."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Programme(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    nom: str
    ville: str
    zone_fiscale: str
    nb_lots_total: int
    nb_lots_disponibles: int
    livraison_previsionnelle: str
    prix_a_partir_de_affiche_page_programme: int
    lot_portant_prix_appel: str
    garanties: list[str] = Field(default_factory=list)


class Lot(BaseModel):
    """Un lot tel qu'il sort du flux MBI.

    Tout champ additionnel non reconnu du flux regional est ignore (`extra="ignore"`) :
    le pipeline lit toujours la surface architecte directement sur le plan de vente.
    """

    model_config = ConfigDict(extra="ignore")

    lot_id: str
    typologie: str
    batiment: str
    etage: int
    dernier_etage: bool = False
    statut_mbi: str
    prix_ttc: int
    tva_reduite_5_5_eligible: bool | None = None
    ptz_eligible: bool | None = None
    surface_habitable_mbi: float
    exterieur_type: str
    exterieur_m2: float
    exposition: str
    parking_inclus: str | None = None
    description_actuelle_mbi_seloger: str | None = None
    plan_2d_fichier: str | None = None
    date_changement_statut: str | None = None

    @property
    def disponible(self) -> bool:
        return self.statut_mbi.upper() == "DISPONIBLE"


class FluxMBI(BaseModel):
    programme: Programme
    lots: list[Lot] = Field(alias="lots_echantillon_T3")


class ClauseNotice(BaseModel):
    id: str
    page: int
    rubrique: str
    texte_contractuel: str


class Notice(BaseModel):
    document: str
    clauses_extraites: list[ClauseNotice]

    def clause(self, clause_id: str) -> ClauseNotice | None:
        return next((c for c in self.clauses_extraites if c.id == clause_id), None)


class BibliothequeMentions(BaseModel):
    avertissement: str = ""
    mentions: dict[str, str]


# --- Sortie attendue du LLM (schema impose via un outil) -------------------


class PointFort(BaseModel):
    texte: str = Field(min_length=10, max_length=200)
    sources: list[str] = Field(
        min_length=1,
        description="Cles des faits utilises : MBI.<champ>, PLAN.<piece>, SING.<code>",
    )


class Prestation(BaseModel):
    texte: str = Field(min_length=10, max_length=220)
    clause_id: str = Field(description="Identifiant de la clause de notice citee, ex. NOT-P08")


class FicheLot(BaseModel):
    titre: str = Field(min_length=10, max_length=80)
    accroche: str = Field(min_length=20, max_length=240)
    points_forts: list[PointFort] = Field(min_length=3, max_length=3)
    prestations: list[Prestation] = Field(min_length=3, max_length=3)
