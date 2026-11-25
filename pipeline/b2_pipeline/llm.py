"""Acces a Claude avec sortie structuree (outil force).

En demo : API Anthropic directe (cle ANTHROPIC_API_KEY).
En production chez Nexity : meme code avec `anthropic.AnthropicBedrock`
(region UE), sans changer le reste du pipeline.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

MODELE_PAR_DEFAUT = os.environ.get("B2_MODELE", "claude-sonnet-5-5")


class ClientOutil(Protocol):
    """Ce dont le pipeline a besoin : un appel qui renvoie le JSON d'un outil force."""

    modele: str

    def appeler_outil(
        self,
        systeme: str,
        contenu: list[dict[str, Any]],
        nom_outil: str,
        schema: dict[str, Any],
        max_tokens: int = 1500,
    ) -> dict[str, Any]: ...


class ClientClaude:
    def __init__(
        self, client: Any | None = None, modele: str = MODELE_PAR_DEFAUT, timeout_s: float = 20.0
    ):
        if client is None:
            import anthropic  # import tardif : le mode cache ne depend pas du SDK

            client = anthropic.Anthropic(timeout=timeout_s, max_retries=1)
        self._client: Any = client
        self.modele = modele

    def appeler_outil(
        self,
        systeme: str,
        contenu: list[dict[str, Any]],
        nom_outil: str,
        schema: dict[str, Any],
        max_tokens: int = 1500,
    ) -> dict[str, Any]:
        reponse = self._client.messages.create(
            model=self.modele,
            max_tokens=max_tokens,
            system=systeme,
            tools=[
                {
                    "name": nom_outil,
                    "description": f"Renvoie le resultat {nom_outil}.",
                    "input_schema": schema,
                }
            ],
            tool_choice={"type": "tool", "name": nom_outil},
            messages=[{"role": "user", "content": contenu}],
        )
        for bloc in reponse.content:
            if getattr(bloc, "type", None) == "tool_use" and bloc.name == nom_outil:
                return dict(bloc.input)
        raise RuntimeError(f"Claude n'a pas renvoye l'outil {nom_outil}")


def cle_api_disponible() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))
