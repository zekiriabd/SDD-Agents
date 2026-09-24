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

**427 classes.** Liste close, générée depuis les émetteurs réels par
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
| `ABSTENTION_MISSING` | `DATA_AUTH_MODE_UNKNOWN` | `JUDGE_PROVIDER_UNKNOWN` | `RETRIEVAL_GATE_NOT_PASSED` |
| `ACCEPTANCE_GATE_FAILED` | `DATA_EGRESS_UNDECLARED` | `JUDGE_RESPONSE_MALFORMED` | `ROUTER_FALLBACK_UNTESTED` |
| `ACCEPTANCE_SUITE_MISSING` | `DATA_MCP_SERVER_UNKNOWN` | `JUDGE_RESPONSE_UNPARSEABLE` | `ROUTER_NO_FALLBACK` |
| `AC_DATASET_IS_HOLDOUT` | `DATA_MCP_TOOL_NOT_ALLOWLISTED` | `JUDGE_SAME_AS_EVALUATED` | `ROUTING` |
| `AC_GRADER_UNKNOWN` | `DATA_RUNTIME_MISSING` | `JUDGE_TRANSPORT_FAILED` | `RULE_FILE_MISSING` |
| `AC_NOT_COVERED` | `DATA_RUNTIME_STALE` | `JUDGE_UNCALIBRATED` | `RULE_NOT_IMPLEMENTED` |
| `AC_NOT_EVALUABLE` | `DATA_SCHEMA_ALREADY_FROZEN` | `LATENCY_EXCEEDED_MEASURED` | `RULE_UNDECLARED` |
| `AC_RUNS_INSUFFICIENT` | `DATA_SCHEMA_REVIEW_REQUIRED` | `LATENCY_P95_EXCEEDED` | `SAFETY_EXFILTRATION_PATH` |
| `ADR_COVERS_MALFORMED` | `DATA_SCHEMA_SAMPLE_INVALID` | `MEASUREMENT_MISSING` | `SAFETY_EXFILTRATION_SUCCEEDED` |
| `ADR_MISSING` | `DATA_SCHEMA_SAMPLE_REQUIRED` | `MEMORY_PII_POLICY_MISSING` | `SAFETY_FINDING_BLOCKING` |
| `ADR_NOT_ACCEPTED` | `DATA_SECRET_FILE_MISSING` | `MEMORY_RETENTION_UNDECLARED` | `SAFETY_GATE_FAILED` |
| `ADVERSARIAL` | `DATA_SECRET_FILE_UNIGNORED` | `MEMORY_SCOPE_UNJUSTIFIED` | `SAFETY_GATE_NOT_PASSED` |
| `ADVERSARIAL_DRY_RUN_UNVERIFIED` | `DATA_SECRET_INLINE` | `MEMORY_SHARED_STATE_UNSCOPED` | `SAFETY_HOSTILE_TO_DESTRUCTIVE` |
| `ADVERSARIAL_REQUIRED` | `DATA_SECRET_VAR_UNDECLARED` | `MISROUTE_TO_DESTRUCTIVE` | `SAFETY_MODE_OFF_REFUSED` |
| `ADVERSARIAL_TARGET_UNSAFE` | `DATA_SOURCES_MISSING` | `MISSION_AMBIGUOUS` | `SAFETY_PRIVILEGE_ESCALATION` |
| `AGENT_BOUNDS_MISSING` | `DATA_SOURCE_CONNECTOR_UNKNOWN` | `MISSION_BUDGET_INCOHERENT` | `SAFETY_REVIEW_REPORT_MISSING` |
| `AGENT_BUDGET_EXCEEDED` | `DATA_SOURCE_DATE_FIELD_MISSING` | `MISSION_BUDGET_MISSING` | `SAFETY_SCAN_UNAVAILABLE` |
| `AGENT_CONTRACT_MISSING` | `DATA_SOURCE_DATE_FIELD_UNQUERYABLE` | `MISSION_BUDGET_UNJUSTIFIED` | `SAFETY_SECRET_LEAK` |
| `AGENT_EVAL_FAILED` | `DATA_SOURCE_DB_CONFLICT` | `MISSION_FAILURE_POLICY_MISSING` | `SAFETY_STRATEGY_MISSING` |
| `AGENT_GATE_FAILED` | `DATA_SOURCE_DESCRIPTION_TOO_SHORT` | `MISSION_GATE_FAILED` | `SAFETY_TRUST_BOUNDARY_MISSING` |
| `AGENT_GATE_NOT_PASSED` | `DATA_SOURCE_EMPTY` | `MISSION_GATE_NOT_PASSED` | `SAFETY_UNTRUSTED_UNMARKED` |
| `AGENT_NOT_IN_IR` | `DATA_SOURCE_FIELD_UNDECLARED` | `MISSION_GOAL_UNQUANTIFIED` | `SCOPE_CREEP` |
| `AGENT_SERVES_NO_CAP` | `DATA_SOURCE_FILE_TOO_LARGE` | `MISSION_GROUND_TRUTH_INSUFFICIENT` | `SECRET_FILE_MISSING` |
| `API_CONTRACT_DRIFT` | `DATA_SOURCE_FORMAT_NEEDS_LIB` | `MISSION_GROUND_TRUTH_MISSING` | `SECRET_LEAK` |
| `API_ROUTE_UNBACKED` | `DATA_SOURCE_FORMAT_UNKNOWN` | `MISSION_HASH_MISMATCH` | `SECRET_READ_FORBIDDEN` |
| `API_STATUS_UNMAPPED` | `DATA_SOURCE_GLOB_INVALID` | `MISSION_ID_MISMATCH` | `SECRET_SCAN_PARTIAL` |
| `APP_RUNTIME_MISSING` | `DATA_SOURCE_INCOMPLETE` | `MISSION_ID_UNSTABLE` | `SECRET_VAR_UNDECLARED` |
| `APP_SKELETON_STALE` | `DATA_SOURCE_NO_TOOL` | `MISSION_INCOMPLETE` | `SEC_ENV_VAR_FORBIDDEN` |
| `ARCH_ADR_REQUIRED` | `DATA_SOURCE_PATH_ESCAPE` | `MISSION_NOT_DRAFT` | `SERVING_IDENTITY_FROM_PAYLOAD` |
| `ARCH_AGENT_MODEL_UNDECLARED` | `DATA_SOURCE_ROLE_INVALID` | `MISSION_NOT_FOUND` | `SHARED_TYPE_MISSING` |
| `ARCH_AGENT_TOOLS_UNDECLARED` | `DATA_SOURCE_SCHEMA_DRIFT` | `MISSION_PLACEHOLDER_RESIDUAL` | `SIDE_EFFECT_UNDECLARED` |
| `ARCH_CONDITION_MISSING` | `DATA_SOURCE_SCHEMA_MALFORMED` | `MISSION_STACK_MISMATCH` | `SKILL_FILE_MISSING` |
| `ARCH_FALLBACK_MISSING` | `DATA_SOURCE_SCHEMA_MISSING` | `MISSION_STACK_UNVERIFIED` | `SKILL_NOT_IMPLEMENTED` |
| `ARCH_LOOP_BOUND_MISSING` | `DATA_SOURCE_TRUST_OPTIMISTIC` | `MISSION_TRUST_BOUNDARIES_MISSING` | `SKILL_UNDECLARED` |
| `ARCH_MCP_UNDECLARED` | `DATA_SOURCE_UNKNOWN` | `MISSION_TRUST_UNDECLARED` | `SPEC_COMPLIANCE_RED` |
| `ARCH_MERGE_STRATEGY_MISSING` | `DATA_SOURCE_UNKNOWN_KEY` | `NODE_UNREACHED` | `SPEC_EVAL_BYPASSES_AC` |
| `ARCH_ORCHESTRATOR_INCOMPLETE` | `DATA_SOURCE_UNREADABLE` | `ORCH_DIVERGES_FROM_IR` | `STACK_CARDINALITY_INVALID` |
| `ARCH_REGISTRY_MISSING` | `DATA_STORE_INCOMPLETE` | `ORCH_FINDING_BLOCKING` | `STACK_COMBO_UNLISTED` |
| `ARCH_RELATIONS_MISSING` | `DATA_STORE_KIND_UNKNOWN` | `ORCH_GATE_FAILED` | `STACK_COMBO_UNLOADABLE` |
| `ARCH_REQUIREMENT_UNKNOWN` | `DATA_STORE_UNKNOWN` | `ORCH_GATE_NOT_PASSED` | `STACK_DIR_UNEXPECTED_FILE` |
| `ARCH_ROSTER_AGENT_IDLE` | `DATA_STORE_UNKNOWN_KEY` | `ORCH_MANIFEST_MISSING` | `STACK_FILE_MISSING` |
| `ARCH_ROSTER_ALLOCATION_INVALID` | `DATA_STORE_UNREACHABLE` | `ORCH_MANIFEST_UNATTRIBUTED` | `STACK_LANGUAGE_MISMATCH` |
| `ARCH_ROSTER_CAP_UNALLOCATED` | `DATA_TEXT_FIELD_UNTAGGED` | `ORCH_OVERHEAD_HIGH` | `STACK_LIBRARY_MISSING` |
| `ARCH_ROSTER_DUPLICATE_SOURCE` | `DATA_TLS_INSECURE` | `ORCH_PING_PONG` | `STACK_LIBRARY_PIN_CONFLICT` |
| `ARCH_ROSTER_INCOHERENT` | `DATA_TOOL_CONTRACT_DRIFT` | `OWNERSHIP_AGENT_UNKNOWN` | `STACK_MALFORMED` |
| `ARCH_ROSTER_INCOMPLETE` | `DATA_TOOL_DESCRIPTION_DRIFT` | `OWNERSHIP_MATRIX_MISSING` | `STACK_MISSING` |
| `ARCH_ROSTER_MANIFEST_MALFORMED` | `DATA_TOOL_HAND_EDITED` | `OWNERSHIP_READ_FORBIDDEN` | `STACK_PLACEHOLDER_UNRESOLVED` |
| `ARCH_ROSTER_MANIFEST_MISSING` | `DATA_TOOL_MISSING` | `OWNERSHIP_SELF_LOCKED` | `STACK_SECRET_IN_CLEAR` |
| `ARCH_ROSTER_MISSING` | `DATA_VIEW_SELECT_STAR` | `OWNERSHIP_SHARE_MODE_UNKNOWN` | `STACK_SECTION_MISSING` |
| `ARCH_ROSTER_MUTATED` | `DB_ENVELOPE_MISSING` | `OWNERSHIP_SHARE_UNJUSTIFIED` | `STACK_VALUE_UNIMPLEMENTED` |
| `ARCH_ROSTER_PLACEHOLDER` | `DECLARED` | `OWNERSHIP_VIOLATION` | `STATE_ITEM_UNKNOWN` |
| `ARCH_SPEC_INCOMPLETE` | `DIGEST_DRIFT` | `OWNERSHIP_ZONE_CONTESTED` | `STATE_PHASE_NOT_ITEMIZED` |
| `BASELINE_OWNERSHIP_VIOLATION` | `ERROR_REGISTRY_DRIFT` | `PACKAGING_API_FRAMEWORK_MISSING` | `STATE_PHASE_UNKNOWN` |
| `BOUND_BEHAVIOR_MISMATCH` | `EVAL_ADVISORY_RED` | `PACKAGING_API_FRAMEWORK_UNUSED` | `STATE_RUN_NOT_FOUND` |
| `BOUND_NOT_MATERIALIZED` | `EVAL_BASELINE_MISSING` | `PACKAGING_ARCHI_DELIVERABLE_MISMATCH` | `STATE_SKIP_FORBIDDEN` |
| `BUDGET_ESTIMATE_DRIFT` | `EVAL_BASELINE_STALE` | `PACKAGING_ARCHI_UNDECLARED` | `STATUS_PINNED_HASH_MOVED` |
| `BUDGET_EXCEEDED_ESTIMATE` | `EVAL_DATASET_MISSING` | `PACKAGING_BACKEND_SHEET_MISMATCH` | `STATUS_UNBACKED` |
| `BUDGET_EXCEEDED_MEASURED` | `EVAL_DATASET_NOT_FOUND` | `PACKAGING_BACKEND_SHEET_MISSING` | `TENANT_BOUNDARY_CROSSED` |
| `BUDGET_PRICING_STALE` | `EVAL_DATASET_TOO_SMALL` | `PACKAGING_BACKEND_SHEET_UNUSED` | `TENANT_BREACH` |
| `BUDGET_PRICING_UNKNOWN` | `EVAL_EXECUTOR_MISSING` | `PACKAGING_CONTRACT_DRIFT_ALLOWED` | `TEST_LLM_NOT_MOCKED` |
| `BUDGET_TARGET_MISSED` | `EVAL_GRADER_CONFIG_INVALID` | `PACKAGING_IDENTITY_UNESTABLISHED` | `TOKEN_CEILING_EXCEEDED` |
| `BUILD_CORRECTIBLE` | `EVAL_METRIC_UNSERVED` | `PACKAGING_LANG_MISMATCH` | `TOOL_ABUSE_SUCCEEDED` |
| `BUILD_LOOP_BUDGET_EXHAUSTED` | `EVAL_OUTPUT_UNGRADABLE` | `PACKAGING_SURFACE_MISMATCH` | `TOOL_CONTRACT_FAILED` |
| `BUILD_LOOP_EXHAUSTED` | `EVAL_PIN_STALE` | `PACKAGING_TYPE_UNKNOWN` | `TOOL_CONTRACT_INCONSISTENT` |
| `BYPASS_REASON_MISSING` | `EVAL_PROMOTION_FORCED` | `PACK_UNUSABLE` | `TOOL_CONTRACT_MISSING` |
| `BYPASS_UNKNOWN` | `EVAL_PROMOTION_LABEL_MISSING` | `PARENT_HASH_STALE` | `TOOL_DESCRIPTION_VAGUE` |
| `CAP_COST_EXCEEDS_VALUE` | `EVAL_PROMOTION_REFUSED` | `PII_IN_DATASET` | `TOOL_GATE_FAILED` |
| `CAP_COVERS_UNKNOWN_ITEM` | `EVAL_RED` | `PII_IN_INDEX` | `TOOL_GATE_NOT_PASSED` |
| `CAP_GAP` | `EVAL_REPORT_NOT_FOUND` | `PII_POLICY_PERMISSIVE` | `TOOL_LIVE_UNREACHABLE` |
| `CAP_GATE_FAILED` | `EVAL_SINGLE_RUN_FORBIDDEN` | `PII_SCAN_PARTIAL` | `TOOL_RETRY_UNSAFE` |
| `CAP_GATE_NOT_PASSED` | `EVAL_STALE` | `PROMPT_CODE_ABSENT` | `TOOL_SCHEMA_INVALID` |
| `CAP_GRANULARITY_EXCEEDED` | `EVAL_SUITE_INCOMPLETE` | `PROMPT_CONTRACT_MISMATCH` | `TOOL_SCOPE_EXCESS` |
| `CAP_GRANULARITY_HIGH` | `EVAL_SUITE_NOT_FOUND` | `PROMPT_CONTRADICTION` | `TOPOLOGY_AGENT_UNUSED` |
| `CAP_HASH_PLACEHOLDER` | `EVAL_YELLOW` | `PROMPT_EMPTY` | `TOPOLOGY_CAP_UNALLOCATED` |
| `CAP_ID_MISMATCH` | `EXFILTRATION_SUCCEEDED` | `PROMPT_HASH_MISMATCH` | `TOPOLOGY_CAP_UNKNOWN` |
| `CAP_INCOMPLETE` | `FEATS_NOT_MARKDOWN` | `PROMPT_INLINE_DETECTED` | `TOPOLOGY_CONTRACT_MISSING` |
| `CAP_NOT_IMPLEMENTED` | `FORCE_CUMUL_REJECTED` | `PROMPT_INLINE_FORBIDDEN` | `TOPOLOGY_EDGE_MISSING` |
| `CAP_PARENT_HASH_STALE` | `FRAMEWORK_DRIFT` | `PROMPT_LINT_FAILED` | `TOPOLOGY_GATE_FAILED` |
| `CAP_PARENT_MISSING` | `FRAMEWORK_LEAK_IN_CONTRACT` | `PROMPT_LONG` | `TOPOLOGY_GATE_NOT_PASSED` |
| `CAP_PREMATURE_ALLOCATION` | `FUSION_DEGENERATE` | `PROMPT_MISSING` | `TOPOLOGY_GRAPH_INCOMPLETE` |
| `CITATION_UNRESOLVED` | `GATE_REPORT_FORGERY` | `PROMPT_NOT_PINNED` | `TOPOLOGY_INCOMPLETE` |
| `CLI_COMMAND_UNKNOWN` | `GOAL_NOT_MET` | `PROMPT_OWNERSHIP_VIOLATION` | `TOPOLOGY_JUSTIFICATION_UNMET` |
| `CONFIDENCE_ESCALATION` | `GOLDEN_SET_MISSING` | `PROMPT_REFUSAL_POLICY_MISSING` | `TOPOLOGY_JUSTIFICATION_UNPROVEN` |
| `CONFIG_KEY_CONFLICT` | `GRAPH_UNREACHABLE` | `PROMPT_TEMPLATE_UNRESOLVED` | `TOPOLOGY_MANY_AGENTS` |
| `CONFIG_KEY_MISPLACED` | `HANDOFF_UNCONTRACTED` | `PROMPT_TOOL_UNKNOWN` | `TOPOLOGY_MISSION_HASH_STALE` |
| `CONFIG_SECURITY_DOWNGRADE` | `HARNESS_PARITY_DRIFT` | `PROMPT_TOO_LONG` | `TOPOLOGY_MISSION_MISSING` |
| `CONFIG_UNKNOWN_KEY` | `HARNESS_UNKNOWN` | `QA_OWNERSHIP_VIOLATION` | `TOPOLOGY_PATTERN_MISMATCH` |
| `CONFIG_VALUE_INVALID` | `HOLDOUT_NOT_DISJOINT` | `RAG_AGENT_COMPENSATING` | `TOPOLOGY_PATTERN_REFUSED` |
| `CONTEXT_BUDGET_EXCEEDED` | `HOLDOUT_SET_MISSING` | `RAG_GENERATION_ISSUE` | `TOPOLOGY_REDUNDANT_HOP` |
| `COST_CAP_EXCEEDED` | `HOPS_AT_CEILING` | `RAG_PATTERN_MISMATCH` | `TOPOLOGY_SIMPLICITY_ADVISORY` |
| `COUNTER_DRIFT` | `INFRA_BLOCKED` | `RAG_PATTERN_UNUSED` | `TOPOLOGY_TOOL_MISSING` |
| `DATASET_DUPLICATE_ID` | `INFRA_LEAK_IN_INTENT` | `RAG_RETRIEVAL_ISSUE` | `TOPOLOGY_UNJUSTIFIED` |
| `DATASET_ITEM_INVALID` | `INJECTION_SUCCEEDED` | `REFLECTION_NO_GAIN` | `TRACEABILITY_DANGLING` |
| `DATASET_OWNERSHIP_VIOLATION` | `INJECTION_SUITE_MISSING` | `REFLECTION_SELF_GRADING` | `TRACEABILITY_GAP` |
| `DATA_ACCESS_ADR_REQUIRED` | `INVALID_ARG` | `REFUSAL_POLICY_BYPASSED` | `TRACE_MALFORMED` |
| `DATA_ACCESS_AST_MISSING` | `INVARIANT_ENFORCER_MISSING` | `REGRESSION` | `TRACE_MISSING` |
| `DATA_ACCESS_CODE_UNPARSABLE` | `IR_COMPILE_FAILED` | `REGRESSION_WITHIN_NOISE` | `TRAJECTORY_DEAD_END` |
| `DATA_ACCESS_ENVELOPE_MISSING` | `IR_DANGLING_REF` | `RETRIEVAL_ACCESS_MIXED` | `TRAJECTORY_VIOLATION` |
| `DATA_ACCESS_FILTER_POST_GENERATION` | `IR_INVALID` | `RETRIEVAL_BELOW_THRESHOLD` | `UNBOUNDED_LOOP` |
| `DATA_ACCESS_INCONSISTENT` | `IR_MALFORMED` | `RETRIEVAL_BINDING_MISMATCH` | `UNSAFE_TOOL_COHABITATION` |
| `DATA_ACCESS_REGEX_GUARD` | `IR_NOT_FOUND` | `RETRIEVAL_CHUNKING_UNMEASURED` | `WORKSPACE_GHOST_DIR_NOT_EMPTY` |
| `DATA_ACCESS_RETRY_ON_WRITE` | `IR_SCHEMA_INVALID` | `RETRIEVAL_CONFIG_DRIFT` | `WORKSPACE_MIGRATION_COLLISION` |
| `DATA_ACCESS_SCHEMA_OUTSIDE_ALLOWLIST` | `IR_STALE` | `RETRIEVAL_CORPUS_DUPLICATES` | `WORKSPACE_MIGRATION_REGISTRY_INVALID` |
| `DATA_ACCESS_SQL_INTERPOLATED` | `JUDGE_CALIBRATION_SYNTHETIC` | `RETRIEVAL_CORPUS_MISSING` | `WORKSPACE_TREE_INCOMPLETE` |
| `DATA_ACCESS_STRATEGY_UNKNOWN` | `JUDGE_CLIENT_MISSING` | `RETRIEVAL_CORPUS_UNPARSED` | `WORKSPACE_VERSION_MISSING` |
| `DATA_ACCESS_WRITE_IN_READONLY` | `JUDGE_EQUALS_EVALUATED` | `RETRIEVAL_EXECUTOR_MISSING` | `WORKSPACE_VERSION_OUTDATED` |
| `DATA_AUTH_INCOMPLETE` | `JUDGE_NOT_CALIBRATED` | `RETRIEVAL_GATE_FAILED` |  |
