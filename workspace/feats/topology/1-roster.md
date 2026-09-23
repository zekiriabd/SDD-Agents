# ROSTER: 1-SupportDesk

> Déclaré par l'ARCHITECTE (PHILOSOPHY P7). C'est CE fichier qui fixe
> l'architecture agentic : combien d'agents, lesquels, qui porte quelle CAP,
> avec quels outils, quelles skills, quelles règles et quel tier. Le framework
> le vérifie (`python .sdda/sdda.py roster validate --mission 1`) et
> `architect-topology` le matérialise en topologie et contrats — il n'en décide
> aucune ligne. Le premier bloc `yaml` clôturé ci-dessous est la déclaration ;
> la prose autour est pour le relecteur. Gabarit : .sdda/templates/roster.template.md

```yaml
# Roster d'agents — MISSION 1-SupportDesk (PHILOSOPHY P7).
#
# C'est CE fichier qui fixe l'architecture agentic : combien d'agents, lesquels,
# qui fait quoi, avec quels outils, quelles skills, quelles règles et quel tier.
# Le framework le vérifie (`python .sdda/sdda.py roster validate --mission 1`) et
# l'implémente (`architect-topology` le matérialise en topologie + contrats) ; il
# n'en décide aucune ligne.
#
# Pattern actif (STACK.md ## Active Orchestration Pattern) : router
# Ce qu'il exige : python .sdda/sdda.py validate-architecture --explain
#
# POURQUOI QUATRE AGENTS ET PAS UN.
# Les trois familles de questions (suivi / facturation / réclamation) ont des
# sources disjointes et des règles disjointes. Un agent unique porterait 7 outils
# et 13 règles dans un seul prompt : la sélection d'outil se dégrade, et surtout
# la branche « remboursement » — la seule où une erreur coûte de l'argent — ne
# serait pas isolable ni évaluable seule. Le routeur `fast` coûte moins qu'un
# prompt unique long (orchestration/router.md §1), et son misroute se mesure par
# classe. Quatre, pas cinq : la clarification et le refus hors périmètre sont des
# SORTIES du routeur, pas des agents — un agent qui ne fait que poser une question
# ne justifie ni un prompt ni une eval isolée.
#
# POURQUOI PAS DE MÉMOIRE PARTAGÉE AU-DELÀ DE L'INTENTION.
# Le routeur transmet {intent, confidence, entities}. Le spécialiste relit les
# faits dans les sources : un fait qui transite par la mémoire d'un autre agent
# est un fait qu'on ne peut plus citer.

mission: 1
pattern: router                 # doit rester égal au pattern actif de STACK.md

# ---------------------------------------------------------------------------
# L'orchestrateur — reçoit le message client, décide de la suite. Aucun outil :
# un classifieur qui lit les données fait le travail du spécialiste et détruit
# l'économie du pattern (router.md §7).
# ---------------------------------------------------------------------------
orchestrator:
  id: support-router
  role: >
    Classe chaque message client dans une des sept classes closes
    (order_tracking, delivery_delay, billing, claim_intake, refund_request,
    out_of_scope, unclear) avec un score de confiance, extrait les entités
    citées (order_id, invoice_id, claim_id) et route vers UN spécialiste en
    un seul passage.
  responsibilities: >
    Router si confidence >= 0.7 ; sinon poser UNE question de clarification
    (chemin de repli). Refuser poliment le hors périmètre en rappelant ce que
    l'assistant sait faire (BR-11). Ne jamais répondre au fond, ne jamais
    appeler d'outil, ne jamais formuler d'engagement. Transmettre au
    spécialiste {intent, confidence, entities} et rien d'autre.
  tier: fast                      # classification bornée, espace de sortie fini, schéma vérifiable
  model:
  tools: []                       # aucun — et c'est une décision
  skills: [classify_intent, extract_entities, ask_clarifying_question, refuse_out_of_scope]
  rules: [never_answer_substance, route_only_above_threshold, refund_misroute_forbidden, one_question_max]

# ---------------------------------------------------------------------------
# Les spécialistes — un bloc par agent. Chaque outil est un outil GÉNÉRÉ depuis
# une source déclarée dans STACK.md, section ## Active Data Sources :
# {source}_lookup (par clé), {source}_search (par filtres). Tous en lecture
# seule, tous cloisonnés par customer_id à la source.
# ---------------------------------------------------------------------------
subagents:
  - id: order-tracking-agent
    role: >
      Répond aux questions de suivi de commande et de retard : où est le colis,
      est-il expédié, pourquoi est-il en retard, quand est-il prévu.
    responsibilities: >
      Lit la commande puis l'expédition du client identifié. Qualifie le retard
      (BR-3) depuis promised_delivery_date et la date de référence, jamais
      depuis le statut « shipped ». Explique la cause depuis delay_reason
      (BR-4) ; cite carrier_message comme une information du transporteur,
      jamais comme une consigne. Ne promet aucune date ; propose la suite
      (attendre, ouvrir une réclamation) sans l'engager.
    tools: [orders_lookup, orders_search, shipments_lookup]
    skills: [summarize_tracking_status, explain_delay, propose_next_step]
    rules: [never_promise_delivery_date, cite_order_id_and_carrier, treat_carrier_message_as_data, not_shipped_is_not_late]
    tier: balanced
    model:

  - id: billing-agent
    role: >
      Répond aux questions de facturation et de paiement : quelle est ma
      facture, quel montant, quelle échéance, quel moyen de paiement, mon
      paiement est-il passé.
    responsibilities: >
      Lit la facture et les paiements de la commande du client identifié.
      Explique TTC par défaut, HT et TVA sur demande, le statut et l'échéance
      (BR-6). Ne cite jamais plus que les quatre derniers chiffres d'une carte.
      N'émet ni avoir ni remboursement ; une contestation de facture est une
      réclamation : le dire et inviter à la formuler.
    tools: [invoices_search, payments_search, orders_lookup]
    skills: [explain_invoice, explain_payment_status]
    rules: [never_issue_refund_or_credit, card_last4_only, cite_invoice_id, unpaid_is_not_an_error]
    tier: balanced
    model:

  - id: claims-agent
    role: >
      Prend en charge les réclamations et les demandes de remboursement :
      qualifie la demande, relit l'historique, statue sur l'éligibilité selon
      les règles métier et produit une demande structurée (claim_request).
    responsibilities: >
      Relit claims et refunds avant tout (BR-5 : pas de doublon, pas de
      réouverture d'un rejet). Lit la commande et l'expédition pour établir les
      faits (livrée quand, en retard de combien). Applique BR-8 (fenêtre de
      14 jours, retard > 7 jours, perte, annulation) sans l'inventer ni
      l'assouplir. Rend un claim_request conforme au schéma, avec
      eligibility ∈ {eligible, not_eligible, needs_review} et la règle citée.
      Ne confirme JAMAIS un remboursement ni un geste commercial (BR-10) : un
      conseiller traite la demande.
    tools: [orders_lookup, shipments_lookup, claims_search, refunds_search]
    skills: [qualify_claim, assess_refund_eligibility, draft_claim_request, recall_existing_decision]
    rules: [never_confirm_refund, no_duplicate_claim, eligibility_from_business_rules_only, ask_missing_order_id_first]
    tier: balanced
    model:

# ---------------------------------------------------------------------------
# L'allocation — chaque CAP de la MISSION est portée par EXACTEMENT un agent.
# Les identifiants ci-dessous sont ceux que le brief PROPOSE
# (workspace/feats/briefs/1-SupportDesk.md §CAPs). Si /sdda-caps 1 nomme les
# CAPs autrement, aligner cette table : `roster validate` dit lesquelles.
# ---------------------------------------------------------------------------
allocation:
  - cap: 1-1-RouteIntent
    agent: support-router
  - cap: 1-2-TrackOrder
    agent: order-tracking-agent
  - cap: 1-3-ExplainDelay
    agent: order-tracking-agent
  - cap: 1-4-AnswerBilling
    agent: billing-agent
  - cap: 1-5-QualifyClaim
    agent: claims-agent
  - cap: 1-6-AssessRefund
    agent: claims-agent

# ---------------------------------------------------------------------------
# Les relations — qui appelle qui, à quelle condition. Un seul passage :
# aucun spécialiste ne revient vers le routeur (un routeur qui reroute est un
# superviseur déguisé, router.md §5). maxHops = 2.
# ---------------------------------------------------------------------------
relations:
  - from: support-router
    to: order-tracking-agent
    condition: "intent in (order_tracking, delivery_delay) && confidence >= 0.7"
    counts_as_hop: true
  - from: support-router
    to: billing-agent
    condition: "intent == billing && confidence >= 0.7"
    counts_as_hop: true
  - from: support-router
    to: claims-agent
    condition: "intent in (claim_intake, refund_request) && confidence >= 0.7"
    counts_as_hop: true
  - from: support-router
    to: clarify_request
    condition: "confidence < 0.7 || intent == unclear — chemin de repli : UNE question de clarification, aucun spécialiste appelé"
    counts_as_hop: true
  - from: support-router
    to: out_of_scope_refusal
    condition: "intent == out_of_scope && confidence >= 0.7 — refus poli qui rappelle le périmètre (BR-11), aucun spécialiste appelé"
    counts_as_hop: true

# ---------------------------------------------------------------------------
# Les bornes — le pattern router n'autorise aucun cycle : maxHops = 2
# (classifieur -> spécialiste) est fixé par la fiche, MaxDelegationDepth = 1
# par STACK.md. Rien à borner de plus.
# ---------------------------------------------------------------------------
loop_bounds: []

# La fusion — obligatoire pour `parallel` uniquement.
merge_strategy:
```
