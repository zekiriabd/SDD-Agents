# TOOL CONTRACT: 1-invoices-search

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

- **name** : `invoices_search`
- **description** :

```
Factures, une par commande confirmée (une commande `created` dont le paiement a
échoué n'a pas de facture). Utiliser pour : retrouver la facture d'une commande
(filtrer par order_id), donner ses montants HT, TVA et TTC, son statut
(issued = émise non réglée, paid, partially_refunded, refunded, cancelled), sa
date d'échéance due_at pour un virement en attente, et si le PDF est disponible.
Ne pas utiliser pour : le moyen de paiement ni l'état d'un paiement (payments),
les remboursements (refunds). Montants en EUR, chaînes à deux décimales,
tax_rate en fraction (0.20 = 20 %). Dates UTC ISO 8601 ; due_at est une date
seule. Une facture absente pour une commande existante signifie « pas encore
facturée », à dire tel quel.
```

## 2. Schémas

- **Entrée** :
```json
{
  "additionalProperties": false,
  "properties": {
    "customer_id": {
      "description": "Filtre OBLIGATOIRE sur `customer_id` — égalité stricte, jamais une expression.",
      "type": "string"
    },
    "due_at_max": {
      "description": "Borne haute incluse sur `due_at`.",
      "format": "date",
      "type": "string"
    },
    "due_at_min": {
      "description": "Borne basse incluse sur `due_at`.",
      "format": "date",
      "type": "string"
    },
    "issued_at_max": {
      "description": "Borne haute incluse sur `issued_at`.",
      "format": "date-time",
      "type": "string"
    },
    "issued_at_min": {
      "description": "Borne basse incluse sur `issued_at`.",
      "format": "date-time",
      "type": "string"
    },
    "order_id": {
      "description": "Filtre optionnel sur `order_id` — égalité stricte, jamais une expression.",
      "type": "string"
    },
    "status": {
      "description": "Filtre optionnel sur `status` — égalité stricte, jamais une expression. Valeurs admises : issued, paid, partially_refunded, refunded, cancelled.",
      "enum": [
        "issued",
        "paid",
        "partially_refunded",
        "refunded",
        "cancelled"
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
    "records": {
      "description": "Au plus 50 enregistrements, triés de façon déterministe.",
      "items": {
        "properties": {
          "amount_excl_tax": {
            "description": "Montant HT, EUR, chaîne à deux décimales.",
            "type": "string"
          },
          "amount_incl_tax": {
            "description": "Montant TTC, EUR, chaîne à deux décimales — le montant à citer par défaut.",
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
            "description": "Identifiant du client facturé (CUST-NNNN). Filtre d'identité ; non énumérable.",
            "type": "string"
          },
          "due_at": {
            "description": "Date d'échéance de règlement (date seule). Pertinente surtout pour un virement en attente.",
            "format": "date",
            "type": "string"
          },
          "invoice_id": {
            "description": "Numéro de facture (INV-AAAA-NNNN), à citer dans toute réponse de facturation.",
            "type": "string"
          },
          "issued_at": {
            "description": "Date d'émission, UTC ISO 8601.",
            "format": "date-time",
            "type": "string"
          },
          "order_id": {
            "description": "Numéro de la commande facturée.",
            "type": "string"
          },
          "pdf_available": {
            "description": "true si le PDF de la facture est téléchargeable depuis l'espace client.",
            "type": "boolean"
          },
          "status": {
            "description": "issued (émise, non réglée), paid, partially_refunded, refunded, cancelled.",
            "enum": [
              "issued",
              "paid",
              "partially_refunded",
              "refunded",
              "cancelled"
            ],
            "type": "string"
          },
          "tax_amount": {
            "description": "Montant de TVA, EUR, chaîne à deux décimales.",
            "type": "string"
          },
          "tax_rate": {
            "description": "Taux de TVA en fraction (0.20 = 20 %).",
            "enum": [
              "0.20"
            ],
            "type": "string"
          }
        },
        "required": [
          "amount_excl_tax",
          "amount_incl_tax",
          "currency",
          "customer_id",
          "due_at",
          "invoice_id",
          "issued_at",
          "order_id",
          "pdf_available",
          "status",
          "tax_amount",
          "tax_rate"
        ],
        "type": "object"
      },
      "type": "array"
    },
    "stale": {
      "description": "Vrai si l'instantané dépasse max_staleness_hours ; l'agent doit alors le signaler à l'utilisateur.",
      "type": "boolean"
    },
    "truncated": {
      "description": "Vrai si le plafond de 50 a été atteint : le total réel est SUPÉRIEUR, ne jamais conclure sur ce sous-ensemble.",
      "type": "boolean"
    }
  },
  "required": [
    "records",
    "truncated",
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
| `TOO_MANY_RECORDS` | plus d'enregistrements que le plafond | affiner les filtres avant de répondre, ne jamais conclure sur le tronqué |
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
| `max_response_bytes` | 51200 |

## 6. Authentification

Sans objet : le store `support_data` est déclaré `auth: { mode: none }`.

## 7. Exposé à

| Agent | CAP qui l'exige |
|---|---|
| `{n}-<agent>` | `{n}-{m}-<CAP>` |

> À compléter par `architect-tools`. Si aucune CAP ne l'exige, l'outil ne doit
> être câblé à personne (`[TOOL_SCOPE_EXCESS]`).

## 8. Tests de contrat (L2)

Fichier : `workspace/proof/suites/tool-1-invoices-search.yaml`

- [ ] happy path
- [ ] erreur `TOO_MANY_RECORDS`
- [ ] erreur `INVALID_FILTER`
- [ ] erreur `SOURCE_STALE`
- [ ] erreur `SOURCE_UNAVAILABLE`
- [ ] erreur `TIMEOUT`
- [ ] `as_of` présent dans chaque réponse, y compris vide
- [ ] plafond atteint -> `truncated: true` (lecture de maxRows+1)
- [ ] ordre déterministe : deux appels identiques rendent la même liste
