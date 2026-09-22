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
python .sdda/sdda.py sync-error-registry --check
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

**378 classes.** Liste close, générée depuis les émetteurs réels par
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
| `ABSTENTION_MISSING` | `DATA_MCP_TOOL_NOT_ALLOWLISTED` | `JUDGE_CLIENT_MISSING` | `RETRIEVAL_GATE_NOT_PASSED` |
| `ACCEPTANCE_GATE_FAILED` | `DATA_RUNTIME_MISSING` | `JUDGE_NOT_CALIBRATED` | `ROUTER_FALLBACK_UNTESTED` |
| `ACCEPTANCE_SUITE_MISSING` | `DATA_RUNTIME_STALE` | `JUDGE_RESPONSE_UNPARSEABLE` | `ROUTER_NO_FALLBACK` |
| `AC_DATASET_IS_HOLDOUT` | `DATA_SCHEMA_ALREADY_FROZEN` | `JUDGE_UNCALIBRATED` | `ROUTING` |
| `AC_GRADER_UNKNOWN` | `DATA_SCHEMA_REVIEW_REQUIRED` | `LATENCY_EXCEEDED_MEASURED` | `RULE_NOT_IMPLEMENTED` |
| `AC_NOT_COVERED` | `DATA_SCHEMA_SAMPLE_INVALID` | `LATENCY_P95_EXCEEDED` | `RULE_UNDECLARED` |
| `AC_NOT_EVALUABLE` | `DATA_SCHEMA_SAMPLE_REQUIRED` | `MEASUREMENT_MISSING` | `SAFETY_EXFILTRATION_PATH` |
| `AC_RUNS_INSUFFICIENT` | `DATA_SECRET_FILE_MISSING` | `MEMORY_PII_POLICY_MISSING` | `SAFETY_EXFILTRATION_SUCCEEDED` |
| `ADVERSARIAL` | `DATA_SECRET_FILE_UNIGNORED` | `MEMORY_RETENTION_UNDECLARED` | `SAFETY_FINDING_BLOCKING` |
| `ADVERSARIAL_REQUIRED` | `DATA_SECRET_INLINE` | `MEMORY_SCOPE_UNJUSTIFIED` | `SAFETY_GATE_FAILED` |
| `ADVERSARIAL_TARGET_UNSAFE` | `DATA_SECRET_VAR_UNDECLARED` | `MEMORY_SHARED_STATE_UNSCOPED` | `SAFETY_GATE_NOT_PASSED` |
| `AGENT_BOUNDS_MISSING` | `DATA_SOURCES_MISSING` | `MISROUTE_TO_DESTRUCTIVE` | `SAFETY_HOSTILE_TO_DESTRUCTIVE` |
| `AGENT_BUDGET_EXCEEDED` | `DATA_SOURCE_CONNECTOR_UNKNOWN` | `MISSION_AMBIGUOUS` | `SAFETY_MODE_OFF_REFUSED` |
| `AGENT_CONTRACT_MISSING` | `DATA_SOURCE_DATE_FIELD_MISSING` | `MISSION_BUDGET_INCOHERENT` | `SAFETY_PRIVILEGE_ESCALATION` |
| `AGENT_EVAL_FAILED` | `DATA_SOURCE_DATE_FIELD_UNQUERYABLE` | `MISSION_BUDGET_MISSING` | `SAFETY_SCAN_UNAVAILABLE` |
| `AGENT_GATE_FAILED` | `DATA_SOURCE_DB_CONFLICT` | `MISSION_BUDGET_UNJUSTIFIED` | `SAFETY_SECRET_LEAK` |
| `AGENT_GATE_NOT_PASSED` | `DATA_SOURCE_DESCRIPTION_TOO_SHORT` | `MISSION_FAILURE_POLICY_MISSING` | `SAFETY_STRATEGY_MISSING` |
| `AGENT_NOT_IN_IR` | `DATA_SOURCE_EMPTY` | `MISSION_GATE_FAILED` | `SAFETY_TRUST_BOUNDARY_MISSING` |
| `AGENT_SERVES_NO_CAP` | `DATA_SOURCE_FIELD_UNDECLARED` | `MISSION_GATE_NOT_PASSED` | `SAFETY_UNTRUSTED_UNMARKED` |
| `API_CONTRACT_DRIFT` | `DATA_SOURCE_FILE_TOO_LARGE` | `MISSION_GOAL_UNQUANTIFIED` | `SCOPE_CREEP` |
| `API_ROUTE_UNBACKED` | `DATA_SOURCE_FORMAT_NEEDS_LIB` | `MISSION_GROUND_TRUTH_INSUFFICIENT` | `SECRET_LEAK` |
| `API_STATUS_UNMAPPED` | `DATA_SOURCE_FORMAT_UNKNOWN` | `MISSION_GROUND_TRUTH_MISSING` | `SECRET_SCAN_PARTIAL` |
| `APP_RUNTIME_MISSING` | `DATA_SOURCE_GLOB_INVALID` | `MISSION_HASH_MISMATCH` | `SERVING_IDENTITY_FROM_PAYLOAD` |
| `APP_SKELETON_STALE` | `DATA_SOURCE_INCOMPLETE` | `MISSION_ID_MISMATCH` | `SHARED_TYPE_MISSING` |
| `ARCH_ADR_REQUIRED` | `DATA_SOURCE_NO_TOOL` | `MISSION_ID_UNSTABLE` | `SIDE_EFFECT_UNDECLARED` |
| `ARCH_AGENT_MODEL_UNDECLARED` | `DATA_SOURCE_PATH_ESCAPE` | `MISSION_INCOMPLETE` | `SKILL_NOT_IMPLEMENTED` |
| `ARCH_AGENT_TOOLS_UNDECLARED` | `DATA_SOURCE_ROLE_INVALID` | `MISSION_NOT_DRAFT` | `SKILL_UNDECLARED` |
| `ARCH_CONDITION_MISSING` | `DATA_SOURCE_SCHEMA_DRIFT` | `MISSION_NOT_FOUND` | `SPEC_COMPLIANCE_RED` |
| `ARCH_FALLBACK_MISSING` | `DATA_SOURCE_SCHEMA_MALFORMED` | `MISSION_PLACEHOLDER_RESIDUAL` | `SPEC_EVAL_BYPASSES_AC` |
| `ARCH_LOOP_BOUND_MISSING` | `DATA_SOURCE_SCHEMA_MISSING` | `MISSION_STACK_MISMATCH` | `STACK_CARDINALITY_INVALID` |
| `ARCH_MCP_UNDECLARED` | `DATA_SOURCE_TRUST_OPTIMISTIC` | `MISSION_STACK_UNVERIFIED` | `STACK_COMBO_UNLOADABLE` |
| `ARCH_MERGE_STRATEGY_MISSING` | `DATA_SOURCE_UNKNOWN` | `MISSION_TRUST_BOUNDARIES_MISSING` | `STACK_FILE_MISSING` |
| `ARCH_ORCHESTRATOR_INCOMPLETE` | `DATA_SOURCE_UNKNOWN_KEY` | `MISSION_TRUST_UNDECLARED` | `STACK_LANGUAGE_MISMATCH` |
| `ARCH_REGISTRY_MISSING` | `DATA_SOURCE_UNREADABLE` | `NODE_UNREACHED` | `STACK_MALFORMED` |
| `ARCH_RELATIONS_MISSING` | `DATA_STORE_INCOMPLETE` | `ORCH_DIVERGES_FROM_IR` | `STACK_MISSING` |
| `ARCH_REQUIREMENT_UNKNOWN` | `DATA_STORE_KIND_UNKNOWN` | `ORCH_FINDING_BLOCKING` | `STACK_PLACEHOLDER_UNRESOLVED` |
| `ARCH_ROSTER_AGENT_IDLE` | `DATA_STORE_UNKNOWN` | `ORCH_GATE_FAILED` | `STACK_SECTION_MISSING` |
| `ARCH_ROSTER_ALLOCATION_INVALID` | `DATA_STORE_UNKNOWN_KEY` | `ORCH_GATE_NOT_PASSED` | `STATE_ITEM_UNKNOWN` |
| `ARCH_ROSTER_CAP_UNALLOCATED` | `DATA_STORE_UNREACHABLE` | `ORCH_MANIFEST_MISSING` | `STATE_PHASE_NOT_ITEMIZED` |
| `ARCH_ROSTER_DUPLICATE_SOURCE` | `DATA_TEXT_FIELD_UNTAGGED` | `ORCH_MANIFEST_UNATTRIBUTED` | `STATE_PHASE_UNKNOWN` |
| `ARCH_ROSTER_INCOHERENT` | `DATA_TLS_INSECURE` | `ORCH_OVERHEAD_HIGH` | `STATE_RUN_NOT_FOUND` |
| `ARCH_ROSTER_INCOMPLETE` | `DATA_TOOL_CONTRACT_DRIFT` | `ORCH_PING_PONG` | `STATE_SKIP_FORBIDDEN` |
| `ARCH_ROSTER_MANIFEST_MALFORMED` | `DATA_TOOL_DESCRIPTION_DRIFT` | `OWNERSHIP_AGENT_UNKNOWN` | `STATUS_PINNED_HASH_MOVED` |
| `ARCH_ROSTER_MANIFEST_MISSING` | `DATA_TOOL_HAND_EDITED` | `OWNERSHIP_MATRIX_MISSING` | `STATUS_UNBACKED` |
| `ARCH_ROSTER_MISSING` | `DATA_TOOL_MISSING` | `OWNERSHIP_READ_FORBIDDEN` | `TENANT_BOUNDARY_CROSSED` |
| `ARCH_ROSTER_MUTATED` | `DB_ENVELOPE_MISSING` | `OWNERSHIP_SELF_LOCKED` | `TENANT_BREACH` |
| `ARCH_ROSTER_PLACEHOLDER` | `DECLARED` | `OWNERSHIP_SHARE_MODE_UNKNOWN` | `TEST_LLM_NOT_MOCKED` |
| `ARCH_SPEC_INCOMPLETE` | `DIGEST_DRIFT` | `OWNERSHIP_SHARE_UNJUSTIFIED` | `TOKEN_CEILING_EXCEEDED` |
| `BASELINE_OWNERSHIP_VIOLATION` | `ERROR_REGISTRY_DRIFT` | `OWNERSHIP_VIOLATION` | `TOOL_ABUSE_SUCCEEDED` |
| `BOUND_BEHAVIOR_MISMATCH` | `EVAL_ADVISORY_RED` | `OWNERSHIP_ZONE_CONTESTED` | `TOOL_CONTRACT_FAILED` |
| `BOUND_NOT_MATERIALIZED` | `EVAL_BASELINE_MISSING` | `PACKAGING_API_FRAMEWORK_MISSING` | `TOOL_CONTRACT_INCONSISTENT` |
| `BUDGET_ESTIMATE_DRIFT` | `EVAL_BASELINE_STALE` | `PACKAGING_API_FRAMEWORK_UNUSED` | `TOOL_CONTRACT_MISSING` |
| `BUDGET_EXCEEDED_ESTIMATE` | `EVAL_DATASET_MISSING` | `PACKAGING_CONTRACT_DRIFT_ALLOWED` | `TOOL_DESCRIPTION_VAGUE` |
| `BUDGET_EXCEEDED_MEASURED` | `EVAL_DATASET_NOT_FOUND` | `PACKAGING_IDENTITY_UNESTABLISHED` | `TOOL_GATE_FAILED` |
| `BUDGET_PRICING_STALE` | `EVAL_DATASET_TOO_SMALL` | `PACKAGING_LANG_MISMATCH` | `TOOL_GATE_NOT_PASSED` |
| `BUDGET_PRICING_UNKNOWN` | `EVAL_EXECUTOR_MISSING` | `PACKAGING_SURFACE_MISMATCH` | `TOOL_LIVE_UNREACHABLE` |
| `BUDGET_TARGET_MISSED` | `EVAL_GRADER_CONFIG_INVALID` | `PACKAGING_TYPE_UNKNOWN` | `TOOL_RETRY_UNSAFE` |
| `BUILD_LOOP_BUDGET_EXHAUSTED` | `EVAL_METRIC_UNSERVED` | `PACK_UNUSABLE` | `TOOL_SCHEMA_INVALID` |
| `BUILD_LOOP_EXHAUSTED` | `EVAL_OUTPUT_UNGRADABLE` | `PARENT_HASH_STALE` | `TOOL_SCOPE_EXCESS` |
| `BYPASS_REASON_MISSING` | `EVAL_PIN_STALE` | `PII_IN_DATASET` | `TOPOLOGY_AGENT_UNUSED` |
| `BYPASS_UNKNOWN` | `EVAL_PROMOTION_FORCED` | `PII_IN_INDEX` | `TOPOLOGY_CAP_UNALLOCATED` |
| `CAP_COST_EXCEEDS_VALUE` | `EVAL_PROMOTION_LABEL_MISSING` | `PII_POLICY_PERMISSIVE` | `TOPOLOGY_CAP_UNKNOWN` |
| `CAP_COVERS_UNKNOWN_ITEM` | `EVAL_PROMOTION_REFUSED` | `PII_SCAN_PARTIAL` | `TOPOLOGY_CONTRACT_MISSING` |
| `CAP_GAP` | `EVAL_RED` | `PROMPT_CODE_ABSENT` | `TOPOLOGY_EDGE_MISSING` |
| `CAP_GATE_FAILED` | `EVAL_REPORT_NOT_FOUND` | `PROMPT_CONTRACT_MISMATCH` | `TOPOLOGY_GATE_FAILED` |
| `CAP_GATE_NOT_PASSED` | `EVAL_SINGLE_RUN_FORBIDDEN` | `PROMPT_CONTRADICTION` | `TOPOLOGY_GATE_NOT_PASSED` |
| `CAP_GRANULARITY_EXCEEDED` | `EVAL_STALE` | `PROMPT_EMPTY` | `TOPOLOGY_GRAPH_INCOMPLETE` |
| `CAP_GRANULARITY_HIGH` | `EVAL_SUITE_NOT_FOUND` | `PROMPT_HASH_MISMATCH` | `TOPOLOGY_INCOMPLETE` |
| `CAP_HASH_PLACEHOLDER` | `EVAL_YELLOW` | `PROMPT_INLINE_DETECTED` | `TOPOLOGY_JUSTIFICATION_UNMET` |
| `CAP_ID_MISMATCH` | `EXFILTRATION_SUCCEEDED` | `PROMPT_INLINE_FORBIDDEN` | `TOPOLOGY_JUSTIFICATION_UNPROVEN` |
| `CAP_INCOMPLETE` | `FORCE_CUMUL_REJECTED` | `PROMPT_LINT_FAILED` | `TOPOLOGY_MANY_AGENTS` |
| `CAP_NOT_IMPLEMENTED` | `FRAMEWORK_LEAK_IN_CONTRACT` | `PROMPT_LONG` | `TOPOLOGY_MISSION_HASH_STALE` |
| `CAP_PARENT_HASH_STALE` | `FUSION_DEGENERATE` | `PROMPT_MISSING` | `TOPOLOGY_MISSION_MISSING` |
| `CAP_PARENT_MISSING` | `GOAL_NOT_MET` | `PROMPT_OWNERSHIP_VIOLATION` | `TOPOLOGY_PATTERN_MISMATCH` |
| `CAP_PREMATURE_ALLOCATION` | `GOLDEN_SET_MISSING` | `PROMPT_REFUSAL_POLICY_MISSING` | `TOPOLOGY_PATTERN_REFUSED` |
| `CITATION_UNRESOLVED` | `GRAPH_UNREACHABLE` | `PROMPT_TEMPLATE_UNRESOLVED` | `TOPOLOGY_REDUNDANT_HOP` |
| `CLI_COMMAND_UNKNOWN` | `HANDOFF_UNCONTRACTED` | `PROMPT_TOOL_UNKNOWN` | `TOPOLOGY_SIMPLICITY_ADVISORY` |
| `CONFIDENCE_ESCALATION` | `HARNESS_PARITY_DRIFT` | `PROMPT_TOO_LONG` | `TOPOLOGY_TOOL_MISSING` |
| `CONFIG_SECURITY_DOWNGRADE` | `HARNESS_UNKNOWN` | `QA_OWNERSHIP_VIOLATION` | `TOPOLOGY_UNJUSTIFIED` |
| `CONFIG_UNKNOWN_KEY` | `HOLDOUT_NOT_DISJOINT` | `RAG_AGENT_COMPENSATING` | `TRACEABILITY_DANGLING` |
| `CONTEXT_BUDGET_EXCEEDED` | `HOLDOUT_SET_MISSING` | `RAG_GENERATION_ISSUE` | `TRACEABILITY_GAP` |
| `COST_CAP_EXCEEDED` | `HOPS_AT_CEILING` | `RAG_PATTERN_MISMATCH` | `TRACE_MALFORMED` |
| `COUNTER_DRIFT` | `INFRA_BLOCKED` | `RAG_PATTERN_UNUSED` | `TRACE_MISSING` |
| `DATASET_DUPLICATE_ID` | `INFRA_LEAK_IN_INTENT` | `RAG_RETRIEVAL_ISSUE` | `TRAJECTORY_DEAD_END` |
| `DATASET_ITEM_INVALID` | `INJECTION_SUCCEEDED` | `REFLECTION_NO_GAIN` | `TRAJECTORY_VIOLATION` |
| `DATASET_OWNERSHIP_VIOLATION` | `INJECTION_SUITE_MISSING` | `REFLECTION_SELF_GRADING` | `UNBOUNDED_LOOP` |
| `DATA_ACCESS_ADR_REQUIRED` | `INVALID_ARG` | `REFUSAL_POLICY_BYPASSED` | `UNSAFE_TOOL_COHABITATION` |
| `DATA_ACCESS_ENVELOPE_MISSING` | `INVARIANT_ENFORCER_MISSING` | `REGRESSION` | `WORKSPACE_GHOST_DIR_NOT_EMPTY` |
| `DATA_ACCESS_FILTER_POST_GENERATION` | `IR_COMPILE_FAILED` | `REGRESSION_WITHIN_NOISE` | `WORKSPACE_MIGRATION_COLLISION` |
| `DATA_ACCESS_INCONSISTENT` | `IR_DANGLING_REF` | `RETRIEVAL_BELOW_THRESHOLD` | `WORKSPACE_MIGRATION_REGISTRY_INVALID` |
| `DATA_ACCESS_STRATEGY_UNKNOWN` | `IR_INVALID` | `RETRIEVAL_BINDING_MISMATCH` | `WORKSPACE_TREE_INCOMPLETE` |
| `DATA_AUTH_INCOMPLETE` | `IR_MALFORMED` | `RETRIEVAL_CHUNKING_UNMEASURED` | `WORKSPACE_VERSION_MISSING` |
| `DATA_AUTH_MODE_UNKNOWN` | `IR_NOT_FOUND` | `RETRIEVAL_CONFIG_DRIFT` | `WORKSPACE_VERSION_OUTDATED` |
| `DATA_EGRESS_UNDECLARED` | `IR_STALE` | `RETRIEVAL_EXECUTOR_MISSING` |  |
| `DATA_MCP_SERVER_UNKNOWN` | `JUDGE_CALIBRATION_SYNTHETIC` | `RETRIEVAL_GATE_FAILED` |  |
