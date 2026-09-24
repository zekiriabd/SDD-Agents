# TOOL CONTRACT: 1-invoice-lookup

MISSION: 1-SupportAssistant
Status: Draft
Side Effect Class: read-only
Trust: trusted

---

## 1. Nom et description

- **name** : `invoice_lookup`
- **description** :

```
Retourne les lignes d'une facture identifiée par son numéro, pour le client courant uniquement.
Utiliser quand la question cite un numéro de facture. Ne pas utiliser pour les contrats.
```

## 2. Schémas

- **Entrée** :
```json
{ "type": "object", "properties": { "invoice_id": { "type": "string" } }, "required": ["invoice_id"] }
```
- **Sortie** :
```json
{ "type": "object", "properties": { "lines": { "type": "array" } }, "required": ["lines"] }
```

## 3. Stratégie de sûreté

Sans objet : `read-only`.

## 4. Erreurs déclarées

| Code | Signification | Comportement attendu de l'agent |
|---|---|---|
| `NOT_FOUND` | facture inconnue pour ce client | informer l'utilisateur, ne pas réessayer |
| `TIMEOUT` | base indisponible | dégrader vers une réponse partielle |

## 5. Bornes techniques

| | |
|---|---|
| `timeout_s` | 5 |
| `rate_limit_rpm` | 120 |
| `retry_policy` | exponential:2 |
| `max_response_bytes` | 65536 |

## 6. Authentification

- **Variable d'environnement** : `BILLING_DB_TOKEN`

## 7. Exposé à

| Agent | CAP qui l'exige |
|---|---|
| `1-billing-specialist` | `1-2-ExplainInvoiceLine` |

## 8. Tests de contrat (L2)

Fichier : `workspace/pipeline/suites/tool-1-invoice-lookup.yaml`
