# ROSTER: {n}-{MissionName}

> Déclaré par l'ARCHITECTE (PHILOSOPHY P7). C'est CE fichier qui fixe
> l'architecture agentic : combien d'agents, lesquels, qui porte quelle CAP,
> avec quels outils, quelles skills, quelles règles et quel tier. Le framework
> le vérifie et l'implémente ; il n'en décide aucune ligne.
>
> Emplacement : `workspace/feats/topology/{n}-roster.md`. Le premier bloc
> `yaml` clôturé ci-dessous EST la déclaration ; la prose autour est pour le
> relecteur. `feats/` ne contient que du Markdown, et le roster est la
> première ligne de la spécification — pas une configuration : il se relit en
> revue à côté de la topologie qu'il commande.
>
> Ce fichier appartient à l'HUMAIN. `architect-topology` le lit et n'y écrit
> jamais ; le voisin `{n}-topology.md` est à lui.
>
> Pré-remplir : `python .sdda/sdda.py roster scaffold --mission {n}`
> Vérifier    : `python .sdda/sdda.py roster validate --mission {n}`
> Ce qui est OBLIGATOIRE dépend du pattern actif de `STACK.md ## Active
> Orchestration Pattern` : `python .sdda/sdda.py validate-architecture --explain`
> (spécification : `.sdda/registry/architecture-requirements.yml`).

```yaml
mission: 1
pattern: router                   # doit rester égal au pattern actif de STACK.md

# ---------------------------------------------------------------------------
# L'orchestrateur — celui qui reçoit l'entrée et décide de la suite.
# Même en `single-agent`, il se déclare : « un agent » n'est pas « pas
# d'architecture », c'est l'architecture la plus simple.
# ---------------------------------------------------------------------------
orchestrator:
  id: support-orchestrator
  role: >
    Classe l'intention entrante et route vers le spécialiste adéquat.
  responsibilities: >
    Router ou demander une clarification. Ne répond jamais au fond,
    ne formule jamais d'engagement commercial.
  tier: fast                      # fast | balanced | deep
  model:                          # optionnel — prime sur le tier s'il est renseigné
  tools: []                       # liste explicite ; [] signifie « aucun », et c'est une décision
  rules: >
    Route si confidence >= 0.7, sinon clarification. maxHops = 6.

# ---------------------------------------------------------------------------
# Les subagents — un bloc par agent. Vide pour `single-agent`.
# ---------------------------------------------------------------------------
subagents:
  - id: billing-specialist
    role: >
      Répond aux questions de facturation.
    responsibilities: >
      Explique une ligne de facture à partir des documents récupérés.
      N'émet jamais de remboursement ni d'avoir.
    tools: [invoice_lookup, zendesk_create_ticket]
    skills: [explain_invoice_line, cite_source]
    # Ce que l'agent NE PEUT PAS enfreindre. Jumelles des skills : nommées ici
    # par l'architecte, implémentées par `dev-prompt` dans `## Règles` du
    # prompt, vérifiées par symétrie ([RULE_NOT_IMPLEMENTED] / [RULE_UNDECLARED]).
    # Une règle n'ayant ni schéma ni effet de bord, aucune gate ne peut
    # l'exécuter pour la juger — c'est la seule vérification possible.
    rules: [never_issue_refund, always_cite_invoice_id]
    tier: balanced
    model:

# ---------------------------------------------------------------------------
# L'allocation — chaque CAP de la MISSION est portée par EXACTEMENT un agent.
# Les identifiants sont ceux de workspace/feats/caps/{n}-{m}-{Name}.md.
# ---------------------------------------------------------------------------
allocation:
  - cap: 1-1-ClassifyIntent
    agent: support-orchestrator
  - cap: 1-2-ExplainInvoiceLine
    agent: billing-specialist

# ---------------------------------------------------------------------------
# Les relations — qui appelle qui, à quelle condition.
# Une condition vide est refusée : « le contexte suit » n'est pas une condition.
# ---------------------------------------------------------------------------
relations:
  - from: support-orchestrator
    to: billing-specialist
    condition: "intent == 'billing' && confidence >= 0.7"
    counts_as_hop: true
  - from: support-orchestrator
    to: clarify_request
    condition: "aucune classe au-dessus du seuil — chemin de repli"
    counts_as_hop: true
  - from: billing-specialist
    to: support-orchestrator
    condition: "needs_reclassification"
    counts_as_hop: true

# ---------------------------------------------------------------------------
# Les bornes — obligatoires dès que le pattern autorise un cycle.
# Une boucle non bornée est un bug, pas une propriété émergente (P12).
# ---------------------------------------------------------------------------
loop_bounds:
  - loop: "support-orchestrator <-> billing-specialist"
    bound: "maxHops = 6"
    on_exceeded: fail-explicit    # fail-explicit | degrade | escalate-human

# ---------------------------------------------------------------------------
# La fusion — obligatoire pour `parallel` uniquement.
# Réconcilier des sorties contradictoires est le vrai travail.
# ---------------------------------------------------------------------------
merge_strategy:                   # vote | priorité déclarée | synthèse par un agent dédié | échec si divergence
```
