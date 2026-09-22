---
# Règle path-scoped (chargement paresseux) : cette taxonomie est volumineuse et
# n'a pas sa place dans chaque contexte d'agent. Canaux :
#   (a) les agents lisent leur tranche `.sdda/digests/error-classification.{agent}.md`
#   (b) les hooks et scripts la parsent SUR DISQUE, sans passer par un contexte
#   (c) le noyau universel (format ERROR 3L + règle mentale) vit dans
#       output-protocol.md §3-§4, seule règle restée inconditionnelle
# Portée résiduelle : les rapports de validation, où les verdicts se lisent
# taxonomie sous les yeux.
paths:
  - "workspace/.sys/.validation/**"
---

# Règle — Taxonomie des classes d'erreur

## Principe

Tout bloc `ERROR` et `WARN` porte un code `[CLASS]` dans son `CAUSE:`. Cela
permet aux hooks, aux boucles de correction et aux tableaux de bord de classer
**sans interpréter du texte libre**.

Source canonique unique. Un test de réciprocité vérifie que toute classe émise
par un agent, un script ou un hook figure ici, et inversement qu'aucune classe
listée ici n'est morte.

---

## 0. Quick-ref par famille

Les familles servent à **regrouper** — dans les tableaux de bord, dans les
digests par agent, dans les reprises automatiques. Elles ne contraignent pas le
nommage : `[SECRET_LEAK]` appartient à la sécurité sans porter le préfixe.

| Famille | Portée |
|---|---|
| `CONFIG` | configuration en couches, STACK.md, packs de contexte |
| `MISSION` | phase 0, G0 |
| `CAP` / `AC` | phase 1, G1 — mesurabilité et traçabilité |
| `TOPOLOGY` | phase 2, G2 |
| `IR` | compilation et validation de l'IR |
| `TOOL` | contrats d'outils, G3 |
| `RETRIEVAL` / `RAG` / `CITATION` | récupération et index, G4 |
| `DATA` / `DB` / `TENANT` | accès base et cloisonnement |
| `MEMORY` | mémoire, rétention, état partagé |
| `PROMPT` | rédaction et intégrité des prompts |
| `AGENT` / `BOUND` | agents générés, G5 |
| `ORCH` / `TRAJECTORY` / `HANDOFF` / `ROUTER` | orchestration, G6 |
| `EVAL` / `GOLDEN` / `HOLDOUT` / `REGRESSION` | protocole d'évaluation |
| `JUDGE` | graders LLM et calibration |
| `BUDGET` / `COST` / `LATENCY` | économie d'exécution |
| `SAFETY` / `INJECTION` / `SECRET` / `PII` / `EXFILTRATION` | sécurité agentic, G7 |
| `TRACE` | observabilité |
| `STATUS` / `STATE` | machine à états |
| `OWNERSHIP` / `DATASET` / `BASELINE` | matrice d'écriture |

> **Le compte fait foi au §6, pas ici.** Une somme tenue à la main dans un
> tableau dérive dès le premier commit et devient un chiffre que plus personne
> ne vérifie — c'est-à-dire exactement le doc-theater que ce framework refuse.
> Le registre canonique du §6 est **généré depuis les émetteurs réels** par
> `sdda_admin/sync_error_registry.py`, et `--check` le vérifie en CI.

---

## 1. Les classes bloquantes sans bypass

Ces sept-là n'ont **aucun** mécanisme de contournement, pas même audit-loggué.
Leur mode d'échec est un dégât réel ou un mensonge structurel.

| Classe | Ce qu'elle empêche |
|---|---|
| `[DATASET_OWNERSHIP_VIOLATION]` | un `dev-*` modifie le jeu qui le juge |
| `[PROMPT_OWNERSHIP_VIOLATION]` | un `dev-*` réécrit le prompt qu'il implémente |
| `[BASELINE_OWNERSHIP_VIOLATION]` | un agent déplace la baseline de référence |
| `[UNSAFE_TOOL_COHABITATION]` | un outil destructif partage un contexte exposé à du texte non maîtrisé |
| `[SECRET_LEAK]` | un secret entre dans un prompt, une trace ou un dataset |
| `[UNBOUNDED_LOOP]` | un cycle sans borne part en production |
| `[JUDGE_CALIBRATION_SYNTHETIC]` | des labels de calibration produits par un LLM |

---

## 2. Les classes les plus fréquentes, et ce qu'elles signifient vraiment

### `[AC_NOT_EVALUABLE]` — famille `CAP_`
Un critère d'acceptation ne nomme pas métrique + seuil + dataset + grader + runs.
**La classe la plus émise du framework, et c'est voulu** : elle attrape le
problème au moment où il est encore gratuit à corriger. Émise par
`validate_cap.py` et par `qa-evals` dans son droit de veto.

### `[TOPOLOGY_UNJUSTIFIED]` — famille `TOPOLOGY_`
La section « Alternative plus simple écartée » est vide, ou un agent au-delà du
premier ne porte aucune raison de la liste close. Si tu la reçois, la topologie
plus simple convenait probablement.

### `[TOOL_SCOPE_EXCESS]` — famille `SAFETY_`
Un agent porte un outil qu'aucune de ses CAPs n'exige. Presque toujours la copie
d'une liste d'outils globale. C'est une faille de sécurité autant qu'un bruit
pour le modèle : moins l'agent a d'outils, mieux il choisit.

### `[RETRIEVAL_BELOW_THRESHOLD]` — famille `RETRIEVAL_`
L'index n'a pas passé sa gate. **Ne touche pas au prompt** : tant que le recall
est bas, aucune quantité de prompt engineering ne produira de bonnes réponses.

### `[EVAL_BASELINE_STALE]` — famille `EVAL_`
Un hash épinglé a bougé (prompt, modèle, index, schéma d'outil, dataset). Le
résultat ne mesure plus le système actuel. Ce n'est pas un avertissement de
confort : c'est la différence entre un vert valide et un vert périmé.

### `[BUDGET_EXCEEDED_ESTIMATE]` / `[BUDGET_EXCEEDED_MEASURED]` — famille `BUDGET_`
La première arrive avant la génération et coûte une reprise de topologie. La
seconde arrive après et coûte une reprise de tout. C'est pourquoi l'estimation
existe.

### `[STATUS_UNBACKED]` — famille `STATUS_`
Un `Status:` écrit dans un fichier sans rapport de gate correspondant. Le script
écrase. Un état auto-proclamé est le mécanisme exact par lequel un pipeline se
déclare vert.

### `[PACK_UNUSABLE]` — famille `CONFIG_`
Le pack de contexte d'un agent est absent ou périmé. **Erreur, pas warning** :
un pack manquant pèse zéro octet et aurait produit un vert éclatant sur un agent
privé de contexte.

---

## 3. Conventions

- **Nommage** : `[FAMILLE_SUJET_PROBLEME]`, en majuscules anglaises, sans accent.
- **Une classe = un fait constatable.** Si deux situations exigent deux actions
  correctives différentes, ce sont deux classes.
- **Ne jamais inventer une classe** dans un prompt d'agent. Une classe inconnue
  ne sera parsée par personne, donc ne déclenchera aucune reprise et
  n'apparaîtra dans aucun tableau de bord. Émettre avec la famille la plus
  proche et signaler le trou.
- **Les classes `WARN` et `ERROR` partagent l'espace de noms.** C'est la gravité
  du contexte qui décide, pas le code — `[CAP_GRANULARITY_HIGH]` est un WARN à 9
  CAPs et une ERROR à 16.

---

## 4. Réciprocité (gate CI)

```bash
python .sdda/python/sdda_admin/sync_error_registry.py --check
```

Vérifie dans les deux sens :

1. **Émetteur → registre** : tout `[CLASS]` trouvé dans `.sdda/agents/**`,
   `.sdda/commands/**`, `.sdda/rules/**`, `.sdda/python/**` figure au §6.
2. **Registre → émetteur** : toute classe du §6 est émise quelque part.

Échec → `[ERROR_REGISTRY_DRIFT]`, et le message nomme les deux listes.
Correction : lancer le script sans `--check`, il régénère le §6.

Sans cette gate, la taxonomie dérive dans les deux directions à la fois — des
classes émises que personne n'a documentées, et des classes documentées que plus
rien n'émet. Aucune des deux ne se voit, et les deux ruinent le classement
automatique dont dépendent les hooks et les tableaux de bord.

---

## 5. Digests par agent

`.sdda/digests/error-classification.{agent}.md` — la tranche des classes qu'un
agent donné peut émettre, générée depuis ce fichier. C'est ce que l'agent lit en
STEP contexte : quelques centaines d'octets au lieu de la taxonomie entière.

Un digest généré mais lu par personne est un coût de maintenance sans
contrepartie : chaque agent doit déclarer son digest dans son STEP de chargement.

---

## 6. Registre canonique

**348 classes.** Liste close, générée depuis les émetteurs réels par
`sdda_admin/sync_error_registry.py` et vérifiée par `framework_smoke.py` :
toute classe émise doit figurer ici, et toute classe listée ici doit être émise
quelque part.

> Les noms ne sont pas tous préfixés par leur famille. `[SECRET_LEAK]` et
> `[UNBOUNDED_LOOP]` disent ce qu'ils sont ; les rebaptiser
> `[SAFETY_SECRET_LEAK]` les rendrait plus longs sans les rendre plus clairs.
> La famille sert à regrouper dans les tableaux de bord, pas à contraindre le
> nommage.

| | | | |
|---|---|---|---|
| `ABSTENTION_MISSING` | `DATA_SCHEMA_REVIEW_REQUIRED` | `IR_NOT_FOUND` | `REGRESSION` |
| `ACCEPTANCE_GATE_FAILED` | `DATA_SCHEMA_SAMPLE_INVALID` | `IR_STALE` | `RETRIEVAL_BELOW_THRESHOLD` |
| `AC_DATASET_IS_HOLDOUT` | `DATA_SCHEMA_SAMPLE_REQUIRED` | `JUDGE_CALIBRATION_SYNTHETIC` | `RETRIEVAL_BINDING_MISMATCH` |
| `AC_GRADER_UNKNOWN` | `DATA_SECRET_FILE_MISSING` | `JUDGE_CLIENT_MISSING` | `RETRIEVAL_CHUNKING_UNMEASURED` |
| `AC_NOT_COVERED` | `DATA_SECRET_FILE_UNIGNORED` | `JUDGE_NOT_CALIBRATED` | `RETRIEVAL_CONFIG_DRIFT` |
| `AC_NOT_EVALUABLE` | `DATA_SECRET_INLINE` | `JUDGE_RESPONSE_UNPARSEABLE` | `RETRIEVAL_EXECUTOR_MISSING` |
| `AC_RUNS_INSUFFICIENT` | `DATA_SECRET_VAR_UNDECLARED` | `JUDGE_UNCALIBRATED` | `RETRIEVAL_GATE_FAILED` |
| `ADVERSARIAL` | `DATA_SOURCES_MISSING` | `LATENCY_EXCEEDED_MEASURED` | `RETRIEVAL_GATE_NOT_PASSED` |
| `ADVERSARIAL_REQUIRED` | `DATA_SOURCE_CONNECTOR_UNKNOWN` | `LATENCY_P95_EXCEEDED` | `ROUTER_FALLBACK_UNTESTED` |
| `ADVERSARIAL_TARGET_UNSAFE` | `DATA_SOURCE_DATE_FIELD_MISSING` | `MEASUREMENT_MISSING` | `ROUTER_NO_FALLBACK` |
| `AGENT_BOUNDS_MISSING` | `DATA_SOURCE_DATE_FIELD_UNQUERYABLE` | `MEMORY_PII_POLICY_MISSING` | `ROUTING` |
| `AGENT_BUDGET_EXCEEDED` | `DATA_SOURCE_DB_CONFLICT` | `MEMORY_RETENTION_UNDECLARED` | `RULE_NOT_IMPLEMENTED` |
| `AGENT_CONTRACT_MISSING` | `DATA_SOURCE_DESCRIPTION_TOO_SHORT` | `MEMORY_SCOPE_UNJUSTIFIED` | `RULE_UNDECLARED` |
| `AGENT_EVAL_FAILED` | `DATA_SOURCE_EMPTY` | `MEMORY_SHARED_STATE_UNSCOPED` | `SAFETY_EXFILTRATION_PATH` |
| `AGENT_GATE_FAILED` | `DATA_SOURCE_FIELD_UNDECLARED` | `MISROUTE_TO_DESTRUCTIVE` | `SAFETY_EXFILTRATION_SUCCEEDED` |
| `AGENT_GATE_NOT_PASSED` | `DATA_SOURCE_FILE_TOO_LARGE` | `MISSION_AMBIGUOUS` | `SAFETY_FINDING_BLOCKING` |
| `AGENT_NOT_IN_IR` | `DATA_SOURCE_FORMAT_NEEDS_LIB` | `MISSION_BUDGET_INCOHERENT` | `SAFETY_GATE_FAILED` |
| `AGENT_SERVES_NO_CAP` | `DATA_SOURCE_FORMAT_UNKNOWN` | `MISSION_BUDGET_MISSING` | `SAFETY_GATE_NOT_PASSED` |
| `API_CONTRACT_DRIFT` | `DATA_SOURCE_GLOB_INVALID` | `MISSION_BUDGET_UNJUSTIFIED` | `SAFETY_HOSTILE_TO_DESTRUCTIVE` |
| `API_ROUTE_UNBACKED` | `DATA_SOURCE_INCOMPLETE` | `MISSION_FAILURE_POLICY_MISSING` | `SAFETY_MODE_OFF_REFUSED` |
| `API_STATUS_UNMAPPED` | `DATA_SOURCE_NO_TOOL` | `MISSION_GATE_FAILED` | `SAFETY_PRIVILEGE_ESCALATION` |
| `ARCH_ADR_REQUIRED` | `DATA_SOURCE_PATH_ESCAPE` | `MISSION_GATE_NOT_PASSED` | `SAFETY_SCAN_UNAVAILABLE` |
| `ARCH_AGENT_MODEL_UNDECLARED` | `DATA_SOURCE_ROLE_INVALID` | `MISSION_GOAL_UNQUANTIFIED` | `SAFETY_SECRET_LEAK` |
| `ARCH_AGENT_TOOLS_UNDECLARED` | `DATA_SOURCE_SCHEMA_DRIFT` | `MISSION_GROUND_TRUTH_INSUFFICIENT` | `SAFETY_STRATEGY_MISSING` |
| `ARCH_CONDITION_MISSING` | `DATA_SOURCE_SCHEMA_MALFORMED` | `MISSION_GROUND_TRUTH_MISSING` | `SAFETY_TRUST_BOUNDARY_MISSING` |
| `ARCH_FALLBACK_MISSING` | `DATA_SOURCE_SCHEMA_MISSING` | `MISSION_HASH_MISMATCH` | `SAFETY_UNTRUSTED_UNMARKED` |
| `ARCH_LOOP_BOUND_MISSING` | `DATA_SOURCE_TRUST_OPTIMISTIC` | `MISSION_ID_MISMATCH` | `SCOPE_CREEP` |
| `ARCH_MCP_UNDECLARED` | `DATA_SOURCE_UNKNOWN` | `MISSION_ID_UNSTABLE` | `SECRET_LEAK` |
| `ARCH_MERGE_STRATEGY_MISSING` | `DATA_SOURCE_UNKNOWN_KEY` | `MISSION_INCOMPLETE` | `SECRET_SCAN_PARTIAL` |
| `ARCH_ORCHESTRATOR_INCOMPLETE` | `DATA_SOURCE_UNREADABLE` | `MISSION_NOT_DRAFT` | `SERVING_IDENTITY_FROM_PAYLOAD` |
| `ARCH_REGISTRY_MISSING` | `DATA_STORE_INCOMPLETE` | `MISSION_NOT_FOUND` | `SHARED_TYPE_MISSING` |
| `ARCH_RELATIONS_MISSING` | `DATA_STORE_KIND_UNKNOWN` | `MISSION_PLACEHOLDER_RESIDUAL` | `SIDE_EFFECT_UNDECLARED` |
| `ARCH_REQUIREMENT_UNKNOWN` | `DATA_STORE_UNKNOWN` | `MISSION_STACK_MISMATCH` | `SKILL_NOT_IMPLEMENTED` |
| `ARCH_ROSTER_DUPLICATE_SOURCE` | `DATA_STORE_UNKNOWN_KEY` | `MISSION_STACK_UNVERIFIED` | `SKILL_UNDECLARED` |
| `ARCH_ROSTER_INCOHERENT` | `DATA_STORE_UNREACHABLE` | `MISSION_TRUST_BOUNDARIES_MISSING` | `SPEC_COMPLIANCE_RED` |
| `ARCH_ROSTER_INCOMPLETE` | `DATA_TEXT_FIELD_UNTAGGED` | `MISSION_TRUST_UNDECLARED` | `SPEC_EVAL_BYPASSES_AC` |
| `ARCH_ROSTER_MANIFEST_MALFORMED` | `DATA_TLS_INSECURE` | `NODE_UNREACHED` | `STACK_COMBO_UNLOADABLE` |
| `ARCH_ROSTER_MANIFEST_MISSING` | `DATA_TOOL_CONTRACT_DRIFT` | `ORCH_DIVERGES_FROM_IR` | `STACK_LANGUAGE_MISMATCH` |
| `ARCH_ROSTER_MISSING` | `DATA_TOOL_DESCRIPTION_DRIFT` | `ORCH_FINDING_BLOCKING` | `STACK_MALFORMED` |
| `ARCH_ROSTER_MUTATED` | `DATA_TOOL_HAND_EDITED` | `ORCH_GATE_FAILED` | `STACK_MISSING` |
| `ARCH_SPEC_INCOMPLETE` | `DATA_TOOL_MISSING` | `ORCH_GATE_NOT_PASSED` | `STATE_PHASE_UNKNOWN` |
| `BASELINE_OWNERSHIP_VIOLATION` | `DB_ENVELOPE_MISSING` | `ORCH_OVERHEAD_HIGH` | `STATE_RUN_NOT_FOUND` |
| `BOUND_BEHAVIOR_MISMATCH` | `DECLARED` | `ORCH_PING_PONG` | `STATE_SKIP_FORBIDDEN` |
| `BOUND_NOT_MATERIALIZED` | `DIGEST_DRIFT` | `OWNERSHIP_AGENT_UNKNOWN` | `STATUS_PINNED_HASH_MOVED` |
| `BUDGET_ESTIMATE_DRIFT` | `ERROR_REGISTRY_DRIFT` | `OWNERSHIP_MATRIX_MISSING` | `STATUS_UNBACKED` |
| `BUDGET_EXCEEDED_ESTIMATE` | `EVAL_ADVISORY_RED` | `OWNERSHIP_SELF_LOCKED` | `TENANT_BOUNDARY_CROSSED` |
| `BUDGET_EXCEEDED_MEASURED` | `EVAL_BASELINE_MISSING` | `OWNERSHIP_SHARE_MODE_UNKNOWN` | `TENANT_BREACH` |
| `BUDGET_PRICING_STALE` | `EVAL_BASELINE_STALE` | `OWNERSHIP_SHARE_UNJUSTIFIED` | `TEST_LLM_NOT_MOCKED` |
| `BUDGET_TARGET_MISSED` | `EVAL_DATASET_MISSING` | `OWNERSHIP_VIOLATION` | `TOKEN_CEILING_EXCEEDED` |
| `BYPASS_REASON_MISSING` | `EVAL_DATASET_NOT_FOUND` | `OWNERSHIP_ZONE_CONTESTED` | `TOOL_ABUSE_SUCCEEDED` |
| `BYPASS_UNKNOWN` | `EVAL_DATASET_TOO_SMALL` | `PACKAGING_API_FRAMEWORK_MISSING` | `TOOL_CONTRACT_FAILED` |
| `CAP_COST_EXCEEDS_VALUE` | `EVAL_EXECUTOR_MISSING` | `PACKAGING_API_FRAMEWORK_UNUSED` | `TOOL_CONTRACT_INCONSISTENT` |
| `CAP_COVERS_UNKNOWN_ITEM` | `EVAL_GRADER_CONFIG_INVALID` | `PACKAGING_CONTRACT_DRIFT_ALLOWED` | `TOOL_DESCRIPTION_VAGUE` |
| `CAP_GAP` | `EVAL_METRIC_UNSERVED` | `PACKAGING_IDENTITY_UNESTABLISHED` | `TOOL_GATE_FAILED` |
| `CAP_GATE_FAILED` | `EVAL_OUTPUT_UNGRADABLE` | `PACKAGING_LANG_MISMATCH` | `TOOL_GATE_NOT_PASSED` |
| `CAP_GATE_NOT_PASSED` | `EVAL_PIN_STALE` | `PACKAGING_SURFACE_MISMATCH` | `TOOL_LIVE_UNREACHABLE` |
| `CAP_GRANULARITY_EXCEEDED` | `EVAL_PROMOTION_FORCED` | `PACKAGING_TYPE_UNKNOWN` | `TOOL_RETRY_UNSAFE` |
| `CAP_GRANULARITY_HIGH` | `EVAL_PROMOTION_LABEL_MISSING` | `PACK_UNUSABLE` | `TOOL_SCHEMA_INVALID` |
| `CAP_HASH_PLACEHOLDER` | `EVAL_PROMOTION_REFUSED` | `PARENT_HASH_STALE` | `TOOL_SCOPE_EXCESS` |
| `CAP_ID_MISMATCH` | `EVAL_RED` | `PII_IN_DATASET` | `TOPOLOGY_AGENT_UNUSED` |
| `CAP_INCOMPLETE` | `EVAL_REPORT_NOT_FOUND` | `PII_IN_INDEX` | `TOPOLOGY_CAP_UNALLOCATED` |
| `CAP_NOT_IMPLEMENTED` | `EVAL_SINGLE_RUN_FORBIDDEN` | `PII_POLICY_PERMISSIVE` | `TOPOLOGY_CAP_UNKNOWN` |
| `CAP_PARENT_HASH_STALE` | `EVAL_STALE` | `PII_SCAN_PARTIAL` | `TOPOLOGY_CONTRACT_MISSING` |
| `CAP_PARENT_MISSING` | `EVAL_SUITE_NOT_FOUND` | `PROMPT_CODE_ABSENT` | `TOPOLOGY_EDGE_MISSING` |
| `CAP_PREMATURE_ALLOCATION` | `EVAL_YELLOW` | `PROMPT_CONTRACT_MISMATCH` | `TOPOLOGY_GATE_FAILED` |
| `CITATION_UNRESOLVED` | `EXFILTRATION_SUCCEEDED` | `PROMPT_CONTRADICTION` | `TOPOLOGY_GATE_NOT_PASSED` |
| `CONFIDENCE_ESCALATION` | `FORCE_CUMUL_REJECTED` | `PROMPT_EMPTY` | `TOPOLOGY_GRAPH_INCOMPLETE` |
| `CONFIG_SECURITY_DOWNGRADE` | `FRAMEWORK_LEAK_IN_CONTRACT` | `PROMPT_HASH_MISMATCH` | `TOPOLOGY_INCOMPLETE` |
| `CONFIG_UNKNOWN_KEY` | `FUSION_DEGENERATE` | `PROMPT_INLINE_DETECTED` | `TOPOLOGY_JUSTIFICATION_UNMET` |
| `CONTEXT_BUDGET_EXCEEDED` | `GOAL_NOT_MET` | `PROMPT_INLINE_FORBIDDEN` | `TOPOLOGY_JUSTIFICATION_UNPROVEN` |
| `COST_CAP_EXCEEDED` | `GOLDEN_SET_MISSING` | `PROMPT_LINT_FAILED` | `TOPOLOGY_MANY_AGENTS` |
| `DATASET_DUPLICATE_ID` | `GRAPH_UNREACHABLE` | `PROMPT_LONG` | `TOPOLOGY_MISSION_HASH_STALE` |
| `DATASET_ITEM_INVALID` | `HANDOFF_UNCONTRACTED` | `PROMPT_MISSING` | `TOPOLOGY_MISSION_MISSING` |
| `DATASET_OWNERSHIP_VIOLATION` | `HARNESS_PARITY_DRIFT` | `PROMPT_OWNERSHIP_VIOLATION` | `TOPOLOGY_PATTERN_MISMATCH` |
| `DATA_ACCESS_ADR_REQUIRED` | `HARNESS_UNKNOWN` | `PROMPT_REFUSAL_POLICY_MISSING` | `TOPOLOGY_PATTERN_REFUSED` |
| `DATA_ACCESS_ENVELOPE_MISSING` | `HOLDOUT_NOT_DISJOINT` | `PROMPT_TEMPLATE_UNRESOLVED` | `TOPOLOGY_REDUNDANT_HOP` |
| `DATA_ACCESS_FILTER_POST_GENERATION` | `HOPS_AT_CEILING` | `PROMPT_TOOL_UNKNOWN` | `TOPOLOGY_SIMPLICITY_ADVISORY` |
| `DATA_ACCESS_INCONSISTENT` | `INFRA_BLOCKED` | `PROMPT_TOO_LONG` | `TOPOLOGY_TOOL_MISSING` |
| `DATA_ACCESS_STRATEGY_UNKNOWN` | `INFRA_LEAK_IN_INTENT` | `QA_OWNERSHIP_VIOLATION` | `TOPOLOGY_UNJUSTIFIED` |
| `DATA_AUTH_INCOMPLETE` | `INJECTION_SUCCEEDED` | `RAG_AGENT_COMPENSATING` | `TRACEABILITY_DANGLING` |
| `DATA_AUTH_MODE_UNKNOWN` | `INJECTION_SUITE_MISSING` | `RAG_GENERATION_ISSUE` | `TRACEABILITY_GAP` |
| `DATA_EGRESS_UNDECLARED` | `INVALID_ARG` | `RAG_PATTERN_MISMATCH` | `TRACE_MALFORMED` |
| `DATA_MCP_SERVER_UNKNOWN` | `INVARIANT_ENFORCER_MISSING` | `RAG_PATTERN_UNUSED` | `TRACE_MISSING` |
| `DATA_MCP_TOOL_NOT_ALLOWLISTED` | `IR_COMPILE_FAILED` | `RAG_RETRIEVAL_ISSUE` | `TRAJECTORY_DEAD_END` |
| `DATA_RUNTIME_MISSING` | `IR_DANGLING_REF` | `REFLECTION_NO_GAIN` | `TRAJECTORY_VIOLATION` |
| `DATA_RUNTIME_STALE` | `IR_INVALID` | `REFLECTION_SELF_GRADING` | `UNBOUNDED_LOOP` |
| `DATA_SCHEMA_ALREADY_FROZEN` | `IR_MALFORMED` | `REFUSAL_POLICY_BYPASSED` | `UNSAFE_TOOL_COHABITATION` |
