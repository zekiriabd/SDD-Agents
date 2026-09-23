# TOOL CONTRACT: 1-refunds-lookup

MISSION: 1-SupportDesk
Status: Architected
Side Effect Class: read-only
Trust: trusted

> SQUELETTE généré par `gen_source_tools.py` : §1, §2, §4, §5 et §6 viennent de la
> déclaration de la source. Ce fichier, lui, SE COMPLÈTE — `architect-tools` remplit
> §7, `dev-prompt` revoit la description de §1, `qa-tests` écrit la suite
> de §8. La régénération ne l'écrase jamais. En revanche, la description de §1 doit
> rester celle de la source : un seul artefact porte l'intention métier, et
> `--check` signale la divergence.

---

## 1. Nom et description

- **name** : `refunds_lookup`
- **description** :

```
Remboursements déjà DEMANDÉS ou ÉMIS, un par remboursement. Utiliser pour :
dire si un remboursement existe pour une commande ou une réclamation, son
montant, son état (requested, approved = validé non versé, processed = versé,
rejected), et sur quel moyen il est ou sera versé (toujours celui du paiement
d'origine). Ne pas utiliser pour : décider de l'éligibilité d'un NOUVEAU
remboursement — cela relève des règles métier de la MISSION, pas d'une donnée.
`approved` sans processed_at : ne jamais promettre une date de versement.
Montants en EUR, chaînes à deux décimales. Dates UTC ISO 8601.
```

## 2. Schémas

- **Entrée** :
```json
{
  "additionalProperties": false,
  "properties": {
    "refund_id": {
      "description": "Clé d'identification de l'enregistrement recherché (`refund_id`).",
      "type": "string"
    }
  },
  "required": [
    "refund_id"
  ],
  "type": "object"
}
```
- **Sortie** :
```json
{
  "properties": {
    "as_of": {
      "description": "Instantané de la source en ISO 8601 UTC. La donnée a pu changer depuis : toute réponse temporelle doit en tenir compte.",
      "format": "date-time",
      "type": "string"
    },
    "record": {
      "description": "L'enregistrement trouvé, ou null si la clé est inconnue.",
      "properties": {
        "amount": {
          "description": "Montant remboursé, EUR, chaîne à deux décimales (partiel possible).",
          "type": "string"
        },
        "claim_id": {
          "description": "Réclamation à l'origine du remboursement (CLM-NNNN).",
          "type": "string"
        },
        "currency": {
          "description": "Devise — toujours EUR.",
          "enum": [
            "EUR"
          ],
          "type": "string"
        },
        "customer_id": {
          "description": "Client remboursé (CUST-NNNN). Filtre d'identité ; non énumérable.",
          "type": "string"
        },
        "method": {
          "description": "Moyen de versement — toujours celui du paiement d'origine : card, paypal, bank_transfer, gift_card.",
          "enum": [
            "card",
            "paypal",
            "bank_transfer",
            "gift_card"
          ],
          "type": "string"
        },
        "order_id": {
          "description": "Commande concernée.",
          "type": "string"
        },
        "payment_id": {
          "description": "Paiement d'origine sur lequel le remboursement est ou sera versé.",
          "type": "string"
        },
        "processed_at": {
          "description": "Date du versement, UTC ISO 8601. null tant que non versé — ne jamais promettre de date.",
          "format": "date-time",
          "type": [
            "string",
            "null"
          ]
        },
        "reason": {
          "description": "Motif : cancelled_before_shipping, lost_in_transit, missing_item, returned_to_sender, late_delivery, damaged…",
          "type": "string"
        },
        "refund_id": {
          "description": "Identifiant du remboursement (RFD-NNNN).",
          "type": "string"
        },
        "requested_at": {
          "description": "Date de la demande, UTC ISO 8601.",
          "format": "date-time",
          "type": "string"
        },
        "status": {
          "description": "requested, approved (validé, non versé), processed (versé), rejected.",
          "enum": [
            "requested",
            "approved",
            "processed",
            "rejected"
          ],
          "type": "string"
        }
      },
      "required": [
        "amount",
        "claim_id",
        "currency",
        "customer_id",
        "method",
        "order_id",
        "payment_id",
        "processed_at",
        "reason",
        "refund_id",
        "requested_at",
        "status"
      ],
      "type": "object"
    },
    "stale": {
      "description": "Vrai si l'instantané dépasse max_staleness_hours ; l'agent doit alors le signaler à l'utilisateur.",
      "type": "boolean"
    }
  },
  "required": [
    "as_of",
    "stale"
  ],
  "type": "object"
}
```

## 3. Stratégie de sûreté

Sans objet : `read-only`. La stack `declared-sources` est en lecture seule par
construction — un besoin d'écriture est `dataaccess/repository-tools.md`.

## 4. Erreurs déclarées

| Code | Signification | Comportement attendu de l'agent |
|---|---|---|
| `NOT_FOUND` | aucun enregistrement pour cette clé | le dire à l'utilisateur, ne pas réessayer |
| `SOURCE_STALE` | l'instantané dépasse max_staleness_hours | répondre en signalant explicitement la date de la donnée |
| `SOURCE_UNAVAILABLE` | source illisible (fichier réécrit, hôte injoignable) | un seul retry, puis échec explicite |
| `TIMEOUT` | budget de lecture dépassé | échec explicite, jamais un résultat partiel muet |

## 5. Bornes techniques

| | |
|---|---|
| `timeout_s` | 3 |
| `rate_limit_rpm` | 600 |
| `retry_policy` | exponential:2 |
| `max_response_bytes` | 65536 |

## 6. Authentification

Sans objet : le store `support_data` est déclaré `auth: { mode: none }`.

## 7. Exposé à

| Agent | CAP qui l'exige |
|---|---|
| `{n}-<agent>` | `{n}-{m}-<CAP>` |

> À compléter par `architect-tools`. Si aucune CAP ne l'exige, l'outil ne doit
> être câblé à personne (`[TOOL_SCOPE_EXCESS]`).

## 8. Tests de contrat (L2)

Fichier : `workspace/proof/suites/tool-1-refunds-lookup.yaml`

- [ ] happy path
- [ ] erreur `NOT_FOUND`
- [ ] erreur `SOURCE_STALE`
- [ ] erreur `SOURCE_UNAVAILABLE`
- [ ] erreur `TIMEOUT`
- [ ] `as_of` présent dans chaque réponse, y compris vide
