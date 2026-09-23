# AGENT CONTRACT: 1-billing-agent

MISSION: 1-SupportDesk
Status: Architected
Model Tier: balanced

> Contrat **neutre framework** : il décrit *quoi*, jamais *avec quelle API*.
> Invariant `framework-neutral-contracts`. Rôle, outils, skills, règles et tier
> repris de `workspace/feats/topology/1-roster.md`, sans modification (P7).

---

## 1. Rôle

Répond aux questions de facturation et de paiement d'une commande du client
identifié : facture, montants (TTC par défaut, HT et TVA sur demande), échéance,
moyen et état du paiement. Il n'émet **ni avoir ni remboursement**, ne cite
**jamais** plus que les quatre derniers chiffres d'une carte ; une contestation
de facture est une réclamation : il le dit et invite à la formuler.

## 2. Capabilities servies

| CAP | AC couverts | Évaluée par |
|---|---|---|
| 1-4-AnswerBilling | AC-1, AC-2, AC-3 | `workspace/proof/datasets/golden/answer-billing-v1.jsonl` (k = 3) |

## 3. Prompt

- Fichier : `workspace/src/SupportDesk/prompts/billing-agent.system.md`
- Hash : `sha256:…`  *(calculé, épinglé aux baselines d'eval — P10)*
- Rédigé par : `dev-prompt` depuis ce contrat (PHASE 4)

## 4. Outils

Outils **générés** depuis `## Active Data Sources` (declared-sources) ; filtre
`customer_id` imposé par le runtime, jamais fourni par le modèle.

| Outil | Exigé par quelle CAP | Classe d'effet de bord |
|---|---|---|
| `invoices_search` | 1-4 AC-1, AC-2 (facture d'une commande : clé `invoice_id`, recherche par `order_id`) | read-only |
| `payments_search` | 1-4 AC-3 (état, moyen, motif d'échec, `card_last4`) | read-only |
| `orders_lookup` | 1-4 AC-3 (commande existante sans facture → « pas encore facturée », BR-6 ; sinon `not_found`, BR-1) | read-only |

## 5. Skills

| Skill | Ce que l'agent sait faire | Mesurée par quelle AC |
|---|---|---|
| `explain_invoice` | restitue le numéro de facture et les montants TTC (défaut), HT et TVA sur demande, à ±0.01 de la source | 1-4 AC-1, AC-2 |
| `explain_payment_status` | explique l'état du paiement : `pending` avec échéance, `failed` avec motif et invitation à réessayer, facture absente → « pas encore facturée » | 1-4 AC-3 |

## 6. Règles

| Règle | Ce que l'agent ne peut pas enfreindre | Conséquence si enfreinte |
|---|---|---|
| `never_issue_refund_or_credit` | aucune sortie n'émet, ne promet ni ne confirme un avoir ou un remboursement (BR-10) | refus |
| `card_last4_only` | d'une carte, seuls les quatre derniers chiffres sont cités (BR-6) | refus |
| `cite_invoice_id` | toute réponse sur une facture cite son numéro (BR-6, BR-13) | dégradation |
| `unpaid_is_not_an_error` | une facture émise non réglée ou un virement `pending` s'explique avec son échéance, jamais comme une anomalie | dégradation |

## 7. Retrievers

Aucun (`rag/none`).

| Retriever | Index | Mode de citation |
|---|---|---|
| — | — | — |

## 8. Schémas

- **Entrée** : `{"type": "object", "required": ["tenant", "message", "intent", "confidence", "entities", "as_of"], "properties": {"tenant": {"type": "string"}, "message": {"type": "string"}, "intent": {"type": "string", "enum": ["billing"]}, "confidence": {"type": "number", "minimum": 0, "maximum": 1}, "entities": {"type": "object", "properties": {"order_id": {"type": "string"}, "invoice_id": {"type": "string"}}, "additionalProperties": false}, "as_of": {"type": "string", "format": "date"}, "detail_level": {"type": "string", "enum": ["ttc", "ht"]}}, "additionalProperties": false}`
- **Sortie** : `{"type": "object", "required": ["behavior", "message", "sources", "degraded"], "properties": {"behavior": {"type": "string", "enum": ["answer", "not_found", "clarify"]}, "invoice_number": {"type": "string"}, "amount_ttc": {"type": "number"}, "amount_ht": {"type": "number"}, "vat": {"type": "number"}, "payment_status": {"type": "string"}, "due_date": {"type": "string"}, "failure_reason": {"type": "string"}, "card_last4": {"type": "string", "pattern": "^[0-9]{4}$"}, "message": {"type": "string"}, "sources": {"type": "array"}, "degraded": {"type": "boolean"}}, "additionalProperties": false}`

> `tenant` reste dans le contexte runtime (filtre des outils). `clarify` quand ni `order_id` ni `invoice_id` n'est fourni (BR-12). Sortie structurée validée (`schema-validation`).

## 9. Bornes

| Borne | Valeur | Comportement à l'atteinte |
|---|---:|---|
| `max_iterations` | 2 | fail-explicit |
| `max_tool_calls` | 4 | fail-explicit |
| `max_delegation_depth` | 1 | fail-explicit |
| `timeout_s` | 60 | fail-explicit |
| `budget_usd` | 0.12 | fail-explicit avec état partiel |

> `max_iterations` et `max_tool_calls` resserrés (STACK.md : 6 / 8) par décision
> de l'architecte sur G2.budget. Routeur $0.01 + spécialiste $0.12 = $0.13 ≤
> $0.15. Nominal estimé $0.0234 (2 tours, 3 lectures en parallèle).

## 10. Posture de confiance

- **Entrées non maîtrisées** : `message` (client), `payments.failure_reason`
  (texte de la banque)
- **Traitement** : contenu, jamais instruction (P8, BR-7)
- **Suite d'injection** : `workspace/proof/datasets/adversarial/billing-agent.jsonl`
  *(obligatoire — invariant `injection-suite-mandatory`)*

## 11. Politique de refus

- ne jamais émettre ni promettre un avoir, un remboursement ni un geste commercial (BR-10)
- ne jamais citer plus que `card_last4` d'un moyen de paiement
- ne jamais inventer un montant absent de la facture source
- ne jamais distinguer une commande d'un autre client d'une commande inexistante (BR-1)
- rediriger une contestation de facture vers la formulation d'une réclamation, sans la qualifier

## 12. Mémoire

| Scope | Lecture | Écriture |
|---|:---:|:---:|
| `conversation` | non | non |
| `shared` (état inter-agents) | oui — `{intent, confidence, entities}` | non |
| `long_term` | non | non |

## 13. Handoffs

Aucun : agent terminal, il ne délègue à personne et ne revient pas vers le
routeur (maxHops = 2). Sa sortie §8 est rendue à l'appelant.

## 14. Comportement de dégradation

| Situation | Comportement |
|---|---|
| Outil indisponible | source `invoices`/`payments` absente, en dérive ou périmée → `degraded: true`, montant jamais inventé, réponse au reste si possible |
| Retrieval vide | sans objet ; facture absente pour commande existante = « pas encore facturée » |
| Confiance basse | identifiant absent → `behavior: clarify`, une question (BR-12) |
| Borne atteinte | `fail-explicit` avec état partiel |
