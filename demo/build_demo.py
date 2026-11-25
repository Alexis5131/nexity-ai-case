"""Assemble la demo en un seul fichier HTML hors ligne.

Integre : resultat du pipeline (JSON), visuels, plans, logos, polices et tokens CSS.
Usage :
    uv run python demo/build_demo.py                # lit pipeline/out/demo_result.json
    uv run python demo/build_demo.py --run cache    # relance le pipeline (cache | live | auto)
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

RACINE = Path(__file__).resolve().parents[1]
ASSETS = RACINE / "assets"
DEMO = RACINE / "demo"
RESULTAT = RACINE / "pipeline" / "out" / "demo_result.json"

POLICES = [
    ("Plus Jakarta Sans", 400, "plus-jakarta-sans-latin-400-normal.woff2"),
    ("Plus Jakarta Sans", 600, "plus-jakarta-sans-latin-600-normal.woff2"),
    ("Plus Jakarta Sans", 700, "plus-jakarta-sans-latin-700-normal.woff2"),
    ("Plus Jakarta Sans", 800, "plus-jakarta-sans-latin-800-normal.woff2"),
    ("JetBrains Mono", 400, "jetbrains-mono-latin-400-normal.woff2"),
    ("JetBrains Mono", 600, "jetbrains-mono-latin-600-normal.woff2"),
]


def data_uri(octets: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(octets).decode()}"


def jpeg(chemin: Path, largeur_max: int = 1400, qualite: int = 80) -> str:
    try:
        from PIL import Image
    except ImportError:  # sans Pillow : image d'origine
        return data_uri(chemin.read_bytes(), "image/jpeg")
    im = Image.open(chemin).convert("RGB")
    if im.width > largeur_max:
        hauteur = round(im.height * largeur_max / im.width)
        im = im.resize((largeur_max, hauteur), Image.Resampling.LANCZOS)
    tampon = io.BytesIO()
    im.save(tampon, "JPEG", quality=qualite, optimize=True, progressive=True)
    return data_uri(tampon.getvalue(), "image/jpeg")


def polices_css() -> str:
    regles = []
    for famille, poids, fichier in POLICES:
        uri = data_uri((DEMO / "fonts" / fichier).read_bytes(), "font/woff2")
        regles.append(
            f"@font-face{{font-family:'{famille}';font-weight:{poids};font-style:normal;"
            f"font-display:block;src:url({uri}) format('woff2')}}"
        )
    return "\n".join(regles)


def assets(resultat: dict[str, Any]) -> dict[str, Any]:
    visuels = ASSETS / "batiment-visuels"
    plans = {
        lot["lot_id"]: data_uri((ASSETS / lot["plan_2d_fichier"]).read_bytes(), "image/svg+xml")
        for lot in resultat["lots"]
        if lot.get("plan_2d_fichier")
    }
    return {
        "facade": jpeg(visuels / "01-facade-residence-oree-seine.jpg"),
        "livre": jpeg(visuels / "02-sejour-t3-etat-livre-notice.jpg"),
        "inspiration": jpeg(visuels / "03-sejour-t3-inspiration-laredoute.jpg"),
        "logoW": data_uri(
            (ASSETS / "brand" / "nexity-logo-2025-white-hd.png").read_bytes(), "image/png"
        ),
        "logoR": data_uri((ASSETS / "brand" / "nexity-logo-2025-hd.png").read_bytes(), "image/png"),
        "plans": plans,
    }


def json_script(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def construire(chemin_resultat: Path = RESULTAT, sortie: Path = DEMO / "index.html") -> Path:
    resultat: dict[str, Any] = json.loads(chemin_resultat.read_text(encoding="utf-8"))
    html = (DEMO / "template.html").read_text(encoding="utf-8")
    remplacements = {
        "/*__FONTS__*/": polices_css(),
        "/*__TOKENS__*/": (ASSETS / "design-system" / "nexity-da-tokens.css").read_text(
            encoding="utf-8"
        ),
        "/*__RESULT__*/null": json_script(resultat),
        "/*__ASSETS__*/null": json_script(assets(resultat)),
    }
    for cle, valeur in remplacements.items():
        if cle not in html:
            raise ValueError(f"Marqueur absent du gabarit : {cle}")
        html = html.replace(cle, valeur)
    if not sortie.exists() or sortie.read_text(encoding="utf-8") != html:
        sortie.write_text(html, encoding="utf-8")
    return sortie


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run",
        choices=["cache", "live", "auto"],
        help="relancer le pipeline avant l'assemblage",
    )
    args = parser.parse_args()
    if args.run:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "b2_pipeline.run",
                "--mode",
                args.run,
                "--assets",
                str(ASSETS),
                "--out",
                str(RESULTAT),
            ],
            cwd=RACINE / "pipeline",
            check=True,
        )
    sortie = construire()
    print(f"Démo assemblée : {sortie} ({sortie.stat().st_size / 1e6:.1f} Mo)")


if __name__ == "__main__":
    main()
