# Prototype B2 : usine de pages lots différenciées (cas nexity.fr)

Prototype pédagogique : programme, lots, plans et visuels fictifs. Aucun formulaire actif, aucune donnée collectée.

## Lancer la démo (aucune installation)

Ouvrir `demo/index.html` dans Chrome, puis passer en plein écran. Le fichier contient tout : résultat du pipeline, visuels, plans, logos et polices. Il fonctionne sans réseau.

| Touche | Action |
| --- | --- |
| Espace ou flèche droite | Étape suivante (à l'étape 5 : valide le lot-témoin) |
| R | Réinitialiser |
| P | Basculer entre l'annonce portail et la page nexity.fr |
| L | Changer de lot (B31, B12) |

Le bouton « tout dérouler » enchaîne les 5 étapes seul. Le curseur de l'image se manipule à la souris.

## Scénario de 4 minutes

| Minute | Ce que l'on montre | Étape |
| --- | --- | --- |
| 1 | Le programme L'Orée Seine, les 4 lots du flux MBI et leur annonce actuelle d'une ligne. Téléphone en vue « Portail aujourd'hui ». | 0 |
| 2 | Garde-fou 1 : B22 en quarantaine (plan 61,80 m², MBI 65,00 m²). Garde-fou 2 : A04 vendu, prix d'appel 269 000 € remplacé par 279 000 € (B12). | 1 et 2 |
| 3 | Singularités calculées avant le modèle, puis rédaction et contrôles : valeurs chiffrées prouvées, notice citée par page, mentions légales hors IA, similarité B12/B31. | 3 et 4 |
| 4 | Lot-témoin B31 validé. Téléphone : page nexity.fr de B31 (curseur état livré / inspiration), touche L pour B12, touche P pour revenir au portail. | 5 |

## Les trois garde-fous et les risques demandés par le jury

| Risque | Garde-fou dans le prototype | Où le voir |
| --- | --- | --- |
| Erreur de l'IA ou donnée fausse | Le cartouche de chaque plan est comparé au MBI (seuil 0,5 m²) et à la somme des pièces ; un lot en écart est mis en quarantaine, rien n'est généré. Tout chiffre du texte doit exister dans le MBI, le plan ou la notice, sinon la fiche est refusée. | Étapes 1 et 4 |
| Promesse que Nexity ne peut pas tenir | Chaque prestation cite sa clause de notice (page) ; termes interdits (cuisine équipée, vue imprenable, superlatifs) ; le visuel d'ambiance n'est affiché que si le lot a le même type d'extérieur (B12 montre son plan, pas la terrasse). | Étape 4 et téléphone |
| Risque juridique | Aucun prix ni mention fiscale dans le texte du modèle ; le prix (avec astérisque) est injecté par le code ; les mentions légales sont assemblées par identifiant depuis la bibliothèque validée, avec empreinte vérifiée ; un prix d'appel caduc déclenche une alerte. | Étapes 2 et 4, pied de la page mobile |

## Mode live avec Claude

Par défaut, la démo utilise le **cache de secours** : des fiches pré-générées par Claude pendant la préparation, repassées dans exactement les mêmes contrôles que le mode live. C'est le mode conseillé en salle.

Pour relancer en direct (lecture des plans PNG par Claude Vision et rédaction) :

```bash
uv sync
export ANTHROPIC_API_KEY=sk-ant-...
export B2_MODELE=claude-sonnet-5-5        # optionnel
uv run python demo/build_demo.py --run live
```

- Sans clé, le mode live bascule seul sur le cache. Pour lancer le pipeline sans reconstruire la démo : `cd pipeline && uv run python -m b2_pipeline.run --mode live`.
- En cas d'erreur réseau ou de réponse refusée deux fois, le pipeline se replie sur la lecture SVG et sur le cache : la démo ne casse jamais.
- Un passage live réussi met à jour `pipeline/cache/generation_cache.json`, qui devient le nouveau cache de secours.
- Le mode live n'a pas pu être testé contre l'API réelle pendant la préparation (pas de clé) : faites un passage avant 20h et relisez le résultat.
- En production chez Nexity, le même code tourne avec `anthropic.AnthropicBedrock` (région UE), sans autre changement.

## Tests

```bash
uv run pytest
```

39 tests : lecture des 3 plans, quarantaine B22, recalcul du prix d'appel, singularités, similarité, intégrité des mentions, refus des chiffres inventés, des prix, des termes interdits et des clauses inconnues, nouvel essai après refus, repli sur le cache, mise à jour du cache après un passage live réussi.

## Architecture

```text
pipeline/b2_pipeline/
  models.py        schémas des entrées (MBI, notice, mentions) et de la sortie du modèle
  plan_reader.py   lecture du cartouche : texte SVG (déterministe) ou Claude Vision (PNG)
  guardrails.py    quarantaine surface, prix d'appel, lots générables
  singularity.py   indice de singularité intra-programme
  generator.py     consigne, appel à Claude (outil forcé), contrôles, cache
  similarity.py    Jaccard sur bigrammes, seuil 30 %
  legal.py         mentions assemblées par identifiant, empreintes
  run.py           orchestration et écriture de pipeline/out/demo_result.json
demo/
  template.html    interface (3 colonnes et téléphone)
  build_demo.py    assemble demo/index.html en un seul fichier hors ligne
```

## Écarts volontaires avec le plan de build

- **Similarité B31 / B12 : 5 %**, valeur calculée sur les textes réels (le plan annonçait 18 %). À reporter dans les slides.
- **Avantage de la terrasse de B31** : affiché comme « 14,0 m² contre 6,5 m² pour le suivant » (calculé), au lieu du « +65 % » du plan, qui ne correspond à aucun calcul sur les données.
- **Badge « PTZ possible* »** au lieu de « Éligible PTZ » : l'éligibilité dépend des revenus de l'acheteur, pas du lot.
- **Plan B22** : dans la copie du projet, le bandeau « CAS TEST DÉMO », le sous-titre « Lot avec écart flux MBI » et le cadre rouge ont été retirés (ils donnaient la réponse). Le PNG a été régénéré à partir du SVG nettoyé.
- **Le champ `surface_cartouche_plan_2d` du JSON MBI est ignoré** : dans la réalité, le MBI ne connaît pas la surface du plan ; le pipeline la lit sur le plan.
- **Image de gauche du curseur** étiquetée « État livré (illustration) » : c'est une image générée, pas une photo du logement.

## Limites à assumer en questions

- Les singularités sont calculées sur les T3 disponibles et fiables du flux reçu (2 lots ici) ; en production, sur tous les lots du programme.
- Le prix d'appel recalculé ne porte que sur les lots du flux de démo.
- La similarité lexicale ne mesure pas la qualité ; elle détecte les textes copiés d'un lot à l'autre.
