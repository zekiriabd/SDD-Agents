# TOOL CONTRACT: 1-zendesk-create-ticket

MISSION: 1-SupportAssistant
Status: Draft
Side Effect Class: external-side-effect
Trust: trusted

---

## 1. Nom et description

- **name** : `zendesk_create_ticket`
- **description** :

```
Ouvre un ticket d'escalade humaine avec l'état partiel de la conversation.
Utiliser uniquement quand la question est hors compétence ou concerne un remboursement.
```

## 2. Schémas

- **Entrée** :
```json
{ "type": "object", "properties": { "conversation_id": { "type": "string" }, "summary": { "type": "string" } }, "required": ["conversation_id", "summary"] }
```
- **Sortie** :
```json
{ "type": "object", "properties": { "ticket_id": { "type": "string" } }, "required": ["ticket_id"] }
```

## 3. Stratégie de sûreté

À définir plus tard.

## 4. Erreurs déclarées

| Code | Signification | Comportement attendu de l'agent |
|---|---|---|
| `RATE_LIMITED` | quota Zendesk atteint | backoff-then-escalate |
| `AUTH_FAILED` | token invalide | échec explicite, jamais de contournement |

## 5. Bornes techniques

| | |
|---|---|
| `timeout_s` | 10 |
| `rate_limit_rpm` | 60 |
| `retry_policy` | exponential:3 |
| `max_response_bytes` | 8192 |

## 6. Authentification

- **Variable d'environnement** : `ZENDESK_TOKEN`

## 7. Exposé à

| Agent | CAP qui l'exige |
|---|---|
| `1-billing-specialist` | `1-2-ExplainInvoiceLine` |

## 8. Tests de contrat (L2)

Fichier : `workspace/evals/suites/tool-1-zendesk-create-ticket.yaml`
