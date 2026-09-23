# `workspace/data/` — la base de test du support client

Sept fichiers JSON, **synthétiques**, générés par `_generate.py`. Ils jouent le
rôle d'un système d'information de e-commerce pour la MISSION `1-SupportDesk`.
C'est la racine du store `support_data` déclaré inline dans
[`../stack/STACK.md`](../stack/STACK.md) (`## Active Data Sources`).

```bash
python workspace/data/_generate.py     # régénère les 7 JSON + les scénarios annotés
```

Ne pas éditer les JSON à la main : modifier `_generate.py` et relancer. La
cohérence entre fichiers (un remboursement cite un paiement qui existe, une
facture reprend le total de sa commande) tient au script, pas à l'attention.

## Les fichiers

| Fichier | Clé | Lignes | Ce qu'il répond |
|---|---|---:|---|
| `customers.json` | `customer_id` | 6 | qui parle, son segment |
| `orders.json` | `order_id` (300..319) | 20 | statut, date promise, contenu, montant |
| `shipments.json` | `order_id` | 15 | où est le colis, transporteur, raison du retard |
| `invoices.json` | `invoice_id` | 19 | montants HT/TVA/TTC, statut, échéance |
| `payments.json` | `payment_id` | 20 | moyen de paiement, état, motif d'échec |
| `refunds.json` | `refund_id` | 4 | remboursements demandés ou versés |
| `claims.json` | `claim_id` | 6 | réclamations ouvertes, résolues, rejetées |

Pourquoi ces sept et pas d'autres : chaque question du périmètre se répond par
**une** source (parfois deux lectures par clé), jamais par un croisement que le
modèle recollerait lui-même. Un « catalogue produits » ou un « stock » n'est pas
nécessaire : aucune question du support après-vente ne les interroge.

## Ce que le jeu couvre volontairement

- **La commande 300** des exemples : expédiée, en retard de 4 jours
  (promise le 19/09, référence 23/09), raison `carrier_capacity`.
- Un retard **non** avéré (316 : prévue le 25/09) pour piéger « en retard = expédiée ».
- Un colis **perdu** (312) avec réclamation résolue et remboursement approuvé non versé.
- Un remboursement **éligible** (306 : 9 jours de retard) et deux **non éligibles**
  (310 : hors fenêtre de 14 jours ; 300 : retard ≤ 7 jours).
- Une réclamation **déjà ouverte** (303) et une **rejetée** (319) : pas de doublon.
- Un virement **en attente** (308), un paiement **échoué** sans facture (314),
  quatre moyens de paiement différents.
- Deux **injections indirectes** dans des champs libres (`shipments[304].carrier_message`,
  `claims[CLM-0005].description`) : le jeu exerce la posture face au texte non maîtrisé.

Date de référence du jeu : **2026-09-23**. Les calculs « en retard » et « sous
14 jours » se font par rapport au paramètre `--as-of` de l'application, pas à
l'horloge, pour que les évaluations se rejouent à l'identique.

## Ce qui n'est pas ici

Les **scénarios annotés** (question, client, intention attendue, faits à citer)
vivent dans `workspace/proof/seed/1-SupportDesk.scenarios.jsonl` : c'est la
vérité terrain de la MISSION, fournie par l'humain, matière première de
`qa-evals`, qui la découpe en golden / holdout sous `workspace/proof/datasets/`.
Ce répertoire-ci ne contient que les données que l'application lit. Les schémas
figés de ces sept fichiers, eux, partent avec le code :
`workspace/src/SupportDesk/src/SupportDesk/data/schemas/`.
