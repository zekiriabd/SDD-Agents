# TOOL CONTRACT: 1-refunds-count

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

- **name** : `refunds_count`
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
    "claim_id": {
      "description": "Filtre optionnel sur `claim_id` — égalité stricte, jamais une expression.",
      "type": "string"
    },
    "customer_id": {
      "description": "Filtre OBLIGATOIRE sur `customer_id` — égalité stricte, jamais une expression.",
      "type": "string"
    },
    "method": {
      "description": "Filtre optionnel sur `method` — égalité stricte, jamais une expression. Valeurs admises : card, paypal, bank_transfer, gift_card.",
      "enum": [
        "card",
        "paypal",
        "bank_transfer",
        "gift_card"
      ],
      "type": "string"
    },
    "order_id": {
      "description": "Filtre optionnel sur `order_id` — égalité stricte, jamais une expression.",
      "type": "string"
    },
    "processed_at_max": {
      "description": "Borne haute incluse sur `processed_at`.",
      "format": "date-time",
      "type": [
        "string",
        "null"
      ]
    },
    "processed_at_min": {
      "description": "Borne basse incluse sur `processed_at`.",
      "format": "date-time",
      "type": [
        "string",
        "null"
      ]
    },
    "requested_at_max": {
      "description": "Borne haute incluse sur `requested_at`.",
      "format": "date-time",
      "type": "string"
    },
    "requested_at_min": {
      "description": "Borne basse incluse sur `requested_at`.",
      "format": "date-time",
      "type": "string"
    },
    "status": {
      "description": "Filtre optionnel sur `status` — égalité stricte, jamais une expression. Valeurs admises : requested, approved, processed, rejected.",
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
    "customer_id"
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
    "count": {
      "description": "Nombre d'enregistrements correspondant aux filtres, compté côté code. Utiliser CET outil pour toute question « combien ».",
      "type": "integer"
    },
    "stale": {
      "description": "Vrai si l'instantané dépasse max_staleness_hours ; l'agent doit alors le signaler à l'utilisateur.",
      "type": "boolean"
    }
  },
  "required": [
    "count",
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
| `INVALID_FILTER` | filtre sur un champ non exposé, ou valeur hors domaine | corriger le filtre, ne pas contourner par un autre outil |
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

Fichier : `workspace/proof/suites/tool-1-refunds-count.yaml`

- [ ] happy path
- [ ] erreur `INVALID_FILTER`
- [ ] erreur `SOURCE_STALE`
- [ ] erreur `SOURCE_UNAVAILABLE`
- [ ] erreur `TIMEOUT`
- [ ] `as_of` présent dans chaque réponse, y compris vide
- [ ] le compte porte sur le TOTAL, pas sur la page tronquée
