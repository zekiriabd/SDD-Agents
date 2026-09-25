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

### `[TOPOLOGY_SIMPLICITY_ADVISORY]` — famille `TOPOLOGY_`
La section « Alternative plus simple considérée » est vide, ou un agent au-delà
du premier ne porte aucune raison de la liste close. Un WARN, jamais un rouge :
le roster est la décision de l'architecte (P7), et le framework la chiffre sans
la prendre. Si tu la reçois, la topologie plus simple convenait probablement.

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
- **Trois émetteurs, à ne pas confondre.** Un script déterministe émet la
  classe d'un fait qu'il a vérifié ; un agent LLM émet dans son rapport la
  classe d'un constat qu'il a jugé (`[RAG_*]`, `[SAFETY_*]`, `[MEMORY_*]`,
  `[SPEC_EVAL_BYPASSES_AC]`, `[TOPOLOGY_JUSTIFICATION_UNMET]`…) ; et la
  **commande** orchestratrice émet elle-même les classes qui enveloppent un
  verdict — `[MISSION|CAP|TOPOLOGY|TOOL|AGENT|ORCH|SAFETY|ACCEPTANCE]_GATE_FAILED`,
  `[*_GATE_NOT_PASSED]`, `[SPEC_COMPLIANCE_RED]`, `[PROMPT_LINT_FAILED]`,
  `[BUILD_CORRECTIBLE]`, `[MISSION_NOT_DRAFT]`, `[SAFETY_MODE_OFF_REFUSED]`.
  Aucun script ne les émet, et ce n'est pas un trou : elles disent « la gate
  Gk a rendu rouge, voici les classes du rapport », là où le rapport porte les
  classes du script. Un tableau « Classe si KO » ne cite que des classes de
  script ; un bloc ERROR de commande commence par la sienne.

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

**439 classes.** Liste close, générée depuis les émetteurs réels par
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
| `ABSTENTION_MISSING` | `DATA_EGRESS_UNDECLARED` | `JUDGE_NOT_CALIBRATED` | `RETRIEVAL_CORPUS_MISSING` |
| `ACCEPTANCE_GATE_FAILED` | `DATA_MCP_SERVER_UNKNOWN` | `JUDGE_PROVIDER_UNKNOWN` | `RETRIEVAL_CORPUS_UNPARSED` |
| `ACCEPTANCE_SUITE_MISSING` | `DATA_MCP_TOOL_NOT_ALLOWLISTED` | `JUDGE_RESPONSE_MALFORMED` | `RETRIEVAL_EXECUTOR_MISSING` |
| `AC_DATASET_IS_HOLDOUT` | `DATA_RUNTIME_MISSING` | `JUDGE_RESPONSE_UNPARSEABLE` | `RETRIEVAL_GATE_FAILED` |
| `AC_GRADER_UNKNOWN` | `DATA_RUNTIME_STALE` | `JUDGE_SAME_AS_EVALUATED` | `RETRIEVAL_GATE_NOT_PASSED` |
| `AC_NOT_COVERED` | `DATA_SCHEMA_ALREADY_FROZEN` | `JUDGE_TRANSPORT_FAILED` | `ROUTER_FALLBACK_UNTESTED` |
| `AC_NOT_EVALUABLE` | `DATA_SCHEMA_REVIEW_REQUIRED` | `JUDGE_UNCALIBRATED` | `ROUTER_NO_FALLBACK` |
| `ADR_COVERS_MALFORMED` | `DATA_SCHEMA_SAMPLE_INVALID` | `LATENCY_EXCEEDED_MEASURED` | `ROUTING` |
| `ADR_MISSING` | `DATA_SCHEMA_SAMPLE_REQUIRED` | `LATENCY_P95_EXCEEDED` | `RULE_FILE_MISSING` |
| `ADR_NOT_ACCEPTED` | `DATA_SECRET_FILE_MISSING` | `MEASUREMENT_MISSING` | `RULE_NOT_IMPLEMENTED` |
| `ADVERSARIAL` | `DATA_SECRET_FILE_UNIGNORED` | `MEMORY_PII_POLICY_MISSING` | `RULE_UNDECLARED` |
| `ADVERSARIAL_DRY_RUN_UNVERIFIED` | `DATA_SECRET_INLINE` | `MEMORY_RETENTION_UNDECLARED` | `SAFETY_EXFILTRATION_PATH` |
| `ADVERSARIAL_REQUIRED` | `DATA_SECRET_VAR_UNDECLARED` | `MEMORY_SCOPE_UNJUSTIFIED` | `SAFETY_EXFILTRATION_SUCCEEDED` |
| `ADVERSARIAL_TARGET_UNSAFE` | `DATA_SOURCES_MISSING` | `MEMORY_SHARED_STATE_UNSCOPED` | `SAFETY_FINDING_BLOCKING` |
| `AGENT_BOUNDS_MISSING` | `DATA_SOURCE_CONNECTOR_UNKNOWN` | `MISROUTE_TO_DESTRUCTIVE` | `SAFETY_GATE_FAILED` |
| `AGENT_BUDGET_EXCEEDED` | `DATA_SOURCE_DATE_FIELD_MISSING` | `MISSION_AMBIGUOUS` | `SAFETY_GATE_NOT_PASSED` |
| `AGENT_CONTRACT_MISSING` | `DATA_SOURCE_DATE_FIELD_UNQUERYABLE` | `MISSION_BUDGET_INCOHERENT` | `SAFETY_HOSTILE_TO_DESTRUCTIVE` |
| `AGENT_EVAL_FAILED` | `DATA_SOURCE_DB_CONFLICT` | `MISSION_BUDGET_MISSING` | `SAFETY_MODE_OFF_REFUSED` |
| `AGENT_GATE_FAILED` | `DATA_SOURCE_DESCRIPTION_TOO_SHORT` | `MISSION_BUDGET_UNJUSTIFIED` | `SAFETY_PRIVILEGE_ESCALATION` |
| `AGENT_GATE_NOT_PASSED` | `DATA_SOURCE_EMPTY` | `MISSION_FAILURE_POLICY_MISSING` | `SAFETY_REVIEW_DISABLED_IN_PRODUCTION` |
| `AGENT_NOT_IN_IR` | `DATA_SOURCE_FIELD_UNDECLARED` | `MISSION_GATE_FAILED` | `SAFETY_REVIEW_REPORT_MISSING` |
| `AGENT_SCHEMA_MISSING` | `DATA_SOURCE_FILE_TOO_LARGE` | `MISSION_GATE_NOT_PASSED` | `SAFETY_SCAN_UNAVAILABLE` |
| `AGENT_SERVES_NO_CAP` | `DATA_SOURCE_FORMAT_NEEDS_LIB` | `MISSION_GOAL_UNQUANTIFIED` | `SAFETY_SECRET_LEAK` |
| `API_CONTRACT_DRIFT` | `DATA_SOURCE_FORMAT_UNKNOWN` | `MISSION_GROUND_TRUTH_INSUFFICIENT` | `SAFETY_STRATEGY_MISSING` |
| `API_ROUTE_UNBACKED` | `DATA_SOURCE_GLOB_INVALID` | `MISSION_GROUND_TRUTH_MISSING` | `SAFETY_TRUST_BOUNDARY_MISSING` |
| `API_STATUS_UNMAPPED` | `DATA_SOURCE_INCOMPLETE` | `MISSION_ID_MISMATCH` | `SAFETY_UNTRUSTED_UNMARKED` |
| `APP_RUNTIME_MISSING` | `DATA_SOURCE_NO_TOOL` | `MISSION_INCOMPLETE` | `SCOPE_CREEP` |
| `APP_SKELETON_STALE` | `DATA_SOURCE_PATH_ESCAPE` | `MISSION_NOT_DRAFT` | `SECRET_FILE_MISSING` |
| `ARCH_ADR_REQUIRED` | `DATA_SOURCE_ROLE_INVALID` | `MISSION_NOT_FOUND` | `SECRET_LEAK` |
| `ARCH_AGENT_MODEL_UNDECLARED` | `DATA_SOURCE_SCHEMA_DRIFT` | `MISSION_PLACEHOLDER_RESIDUAL` | `SECRET_READ_FORBIDDEN` |
| `ARCH_AGENT_TOOLS_UNDECLARED` | `DATA_SOURCE_SCHEMA_MALFORMED` | `MISSION_STACK_MISMATCH` | `SECRET_SCAN_PARTIAL` |
| `ARCH_CONDITION_MISSING` | `DATA_SOURCE_SCHEMA_MISSING` | `MISSION_STACK_UNVERIFIED` | `SECRET_VAR_UNDECLARED` |
| `ARCH_FALLBACK_MISSING` | `DATA_SOURCE_TRUST_OPTIMISTIC` | `MISSION_TRUST_BOUNDARIES_MISSING` | `SEC_ENV_VAR_FORBIDDEN` |
| `ARCH_LOOP_BOUND_MISSING` | `DATA_SOURCE_UNKNOWN` | `NODE_UNREACHED` | `SERVING_IDENTITY_FROM_PAYLOAD` |
| `ARCH_MCP_UNDECLARED` | `DATA_SOURCE_UNKNOWN_KEY` | `ORCH_DIVERGES_FROM_IR` | `SHARED_TYPE_MISSING` |
| `ARCH_MERGE_STRATEGY_MISSING` | `DATA_SOURCE_UNREADABLE` | `ORCH_FINDING_BLOCKING` | `SIDE_EFFECT_UNDECLARED` |
| `ARCH_ORCHESTRATOR_INCOMPLETE` | `DATA_STORE_INCOMPLETE` | `ORCH_GATE_FAILED` | `SKILL_FILE_MISSING` |
| `ARCH_REGISTRY_MISSING` | `DATA_STORE_KIND_UNKNOWN` | `ORCH_GATE_NOT_PASSED` | `SKILL_NOT_IMPLEMENTED` |
| `ARCH_RELATIONS_MISSING` | `DATA_STORE_UNKNOWN` | `ORCH_MANIFEST_MISSING` | `SKILL_UNDECLARED` |
| `ARCH_REQUIREMENT_UNKNOWN` | `DATA_STORE_UNKNOWN_KEY` | `ORCH_MANIFEST_UNATTRIBUTED` | `SPEC_COMPLIANCE_RED` |
| `ARCH_ROSTER_AGENT_IDLE` | `DATA_STORE_UNREACHABLE` | `ORCH_OVERHEAD_HIGH` | `SPEC_EVAL_BYPASSES_AC` |
| `ARCH_ROSTER_ALLOCATION_INVALID` | `DATA_TEXT_FIELD_UNTAGGED` | `ORCH_PING_PONG` | `STACK_CARDINALITY_INVALID` |
| `ARCH_ROSTER_CAP_UNALLOCATED` | `DATA_TLS_INSECURE` | `OWNERSHIP_AGENT_UNKNOWN` | `STACK_COMBO_UNLISTED` |
| `ARCH_ROSTER_DUPLICATE_SOURCE` | `DATA_TOOL_CONTRACT_DRIFT` | `OWNERSHIP_FROZEN_ZONE_CHANGED` | `STACK_COMBO_UNLOADABLE` |
| `ARCH_ROSTER_INCOHERENT` | `DATA_TOOL_DESCRIPTION_DRIFT` | `OWNERSHIP_INSTANCE_ESCAPE` | `STACK_DIR_UNEXPECTED_FILE` |
| `ARCH_ROSTER_INCOMPLETE` | `DATA_TOOL_HAND_EDITED` | `OWNERSHIP_INSTANCE_UNDECLARED` | `STACK_FILE_MISSING` |
| `ARCH_ROSTER_MANIFEST_MALFORMED` | `DATA_TOOL_MISSING` | `OWNERSHIP_MATRIX_MISSING` | `STACK_LANGUAGE_MISMATCH` |
| `ARCH_ROSTER_MANIFEST_MISSING` | `DATA_VIEW_SELECT_STAR` | `OWNERSHIP_READ_FORBIDDEN` | `STACK_LIBRARY_MISSING` |
| `ARCH_ROSTER_MISSING` | `DB_ENVELOPE_MISSING` | `OWNERSHIP_RESTORE_IMPOSSIBLE` | `STACK_LIBRARY_PIN_CONFLICT` |
| `ARCH_ROSTER_MUTATED` | `DECLARED` | `OWNERSHIP_SELF_LOCKED` | `STACK_MALFORMED` |
| `ARCH_ROSTER_PLACEHOLDER` | `DIGEST_DRIFT` | `OWNERSHIP_SHARE_MODE_UNKNOWN` | `STACK_MISSING` |
| `ARCH_SPEC_INCOMPLETE` | `ERROR_REGISTRY_DRIFT` | `OWNERSHIP_SHARE_UNJUSTIFIED` | `STACK_PLACEHOLDER_UNRESOLVED` |
| `BASELINE_OWNERSHIP_VIOLATION` | `EVAL_ADVISORY_RED` | `OWNERSHIP_SHELL_OPAQUE` | `STACK_SECRET_IN_CLEAR` |
| `BOUND_BEHAVIOR_MISMATCH` | `EVAL_BASELINE_MISSING` | `OWNERSHIP_SNAPSHOT_FAILED` | `STACK_SECTION_MISSING` |
| `BOUND_NOT_MATERIALIZED` | `EVAL_BASELINE_STALE` | `OWNERSHIP_SNAPSHOT_MISSING` | `STACK_VALUE_UNIMPLEMENTED` |
| `BUDGET_ESTIMATE_DRIFT` | `EVAL_DATASET_MISSING` | `OWNERSHIP_VIOLATION` | `STATE_ITEM_UNKNOWN` |
| `BUDGET_EXCEEDED_ESTIMATE` | `EVAL_DATASET_NOT_FOUND` | `OWNERSHIP_ZONE_CONTESTED` | `STATE_PHASE_NOT_ITEMIZED` |
| `BUDGET_EXCEEDED_MEASURED` | `EVAL_DATASET_TOO_SMALL` | `PACKAGING_API_FRAMEWORK_MISSING` | `STATE_PHASE_UNKNOWN` |
| `BUDGET_LOCAL_COMPUTE_UNMODELLED` | `EVAL_EXECUTOR_MISSING` | `PACKAGING_API_FRAMEWORK_UNUSED` | `STATE_RUN_NOT_FOUND` |
| `BUDGET_PRICING_STALE` | `EVAL_GRADER_CONFIG_INVALID` | `PACKAGING_ARCHI_DELIVERABLE_MISMATCH` | `STATE_SKIP_FORBIDDEN` |
| `BUDGET_PRICING_UNKNOWN` | `EVAL_METRIC_UNSERVED` | `PACKAGING_ARCHI_UNDECLARED` | `STATUS_PINNED_HASH_MOVED` |
| `BUDGET_TARGET_MISSED` | `EVAL_OUTPUT_UNGRADABLE` | `PACKAGING_BACKEND_SHEET_MISMATCH` | `STATUS_UNBACKED` |
| `BUILD_CORRECTIBLE` | `EVAL_PIN_STALE` | `PACKAGING_BACKEND_SHEET_MISSING` | `TENANT_BOUNDARY_CROSSED` |
| `BUILD_LOOP_BUDGET_EXHAUSTED` | `EVAL_PROMOTION_FORCED` | `PACKAGING_BACKEND_SHEET_UNUSED` | `TENANT_BREACH` |
| `BUILD_LOOP_EXHAUSTED` | `EVAL_PROMOTION_LABEL_MISSING` | `PACKAGING_CONTRACT_DRIFT_ALLOWED` | `TEST_LLM_NOT_MOCKED` |
| `BYPASS_REASON_MISSING` | `EVAL_PROMOTION_REFUSED` | `PACKAGING_IDENTITY_UNESTABLISHED` | `TOKEN_CEILING_EXCEEDED` |
| `BYPASS_UNKNOWN` | `EVAL_RED` | `PACKAGING_LANG_MISMATCH` | `TOOL_ABUSE_SUCCEEDED` |
| `CAP_COST_EXCEEDS_VALUE` | `EVAL_REPORT_NOT_FOUND` | `PACKAGING_SURFACE_MISMATCH` | `TOOL_CONTRACT_FAILED` |
| `CAP_COVERS_UNKNOWN_ITEM` | `EVAL_SINGLE_RUN_FORBIDDEN` | `PACKAGING_TYPE_UNKNOWN` | `TOOL_CONTRACT_INCONSISTENT` |
| `CAP_GAP` | `EVAL_SUITE_INCOMPLETE` | `PACK_UNUSABLE` | `TOOL_CONTRACT_MISSING` |
| `CAP_GATE_FAILED` | `EVAL_SUITE_NOT_FOUND` | `PII_IN_DATASET` | `TOOL_DESCRIPTION_VAGUE` |
| `CAP_GATE_NOT_PASSED` | `EVAL_YELLOW` | `PII_IN_INDEX` | `TOOL_GATE_FAILED` |
| `CAP_GRANULARITY_EXCEEDED` | `EXFILTRATION_SUCCEEDED` | `PII_POLICY_PERMISSIVE` | `TOOL_GATE_NOT_PASSED` |
| `CAP_GRANULARITY_HIGH` | `FEATS_NOT_MARKDOWN` | `PII_SCAN_PARTIAL` | `TOOL_LIVE_UNREACHABLE` |
| `CAP_HASH_PLACEHOLDER` | `FORCE_CUMUL_REJECTED` | `PROJECT_CONTEXT_STALE` | `TOOL_RETRY_UNSAFE` |
| `CAP_ID_MISMATCH` | `FRAMEWORK_DRIFT` | `PROJECT_DEPS_INSTALL_FAILED` | `TOOL_SCHEMA_INVALID` |
| `CAP_INCOMPLETE` | `FRAMEWORK_LEAK_IN_CONTRACT` | `PROJECT_DEPS_NOT_INSTALLED` | `TOOL_SCOPE_EXCESS` |
| `CAP_NOT_IMPLEMENTED` | `FUSION_DEGENERATE` | `PROJECT_NOT_INIT` | `TOPOLOGY_CAP_UNALLOCATED` |
| `CAP_PARENT_HASH_STALE` | `GATE_REPORT_FORGERY` | `PROMPT_CODE_ABSENT` | `TOPOLOGY_CAP_UNKNOWN` |
| `CAP_PARENT_MISSING` | `GOAL_NOT_MET` | `PROMPT_CONTRACT_MISMATCH` | `TOPOLOGY_CONTRACT_MISSING` |
| `CAP_RUNS_INSUFFICIENT` | `GOLDEN_SET_MISSING` | `PROMPT_CONTRADICTION` | `TOPOLOGY_EDGE_MISSING` |
| `CITATION_UNRESOLVED` | `GRAPH_UNREACHABLE` | `PROMPT_EMPTY` | `TOPOLOGY_GATE_FAILED` |
| `CLI_COMMAND_UNKNOWN` | `HANDOFF_UNCONTRACTED` | `PROMPT_HASH_MISMATCH` | `TOPOLOGY_GATE_NOT_PASSED` |
| `CONFIDENCE_ESCALATION` | `HARNESS_PARITY_DRIFT` | `PROMPT_INLINE_FORBIDDEN` | `TOPOLOGY_GRAPH_INCOMPLETE` |
| `CONFIG_KEY_CONFLICT` | `HARNESS_UNKNOWN` | `PROMPT_LINT_FAILED` | `TOPOLOGY_INCOMPLETE` |
| `CONFIG_KEY_MISPLACED` | `HOLDOUT_NOT_DISJOINT` | `PROMPT_LONG` | `TOPOLOGY_JUSTIFICATION_UNMET` |
| `CONFIG_SECURITY_DOWNGRADE` | `HOLDOUT_SET_MISSING` | `PROMPT_MISSING` | `TOPOLOGY_JUSTIFICATION_UNPROVEN` |
| `CONFIG_UNKNOWN_KEY` | `HOOK_ARG_UNKNOWN` | `PROMPT_NOT_PINNED` | `TOPOLOGY_MANY_AGENTS` |
| `CONFIG_VALUE_INVALID` | `HOOK_FAILED` | `PROMPT_OWNERSHIP_VIOLATION` | `TOPOLOGY_MISSION_HASH_STALE` |
| `CONTEXT_BUDGET_CEILING_EXCEEDED` | `HOOK_INTERPRETER_MISSING` | `PROMPT_REFUSAL_POLICY_MISSING` | `TOPOLOGY_MISSION_MISSING` |
| `CONTEXT_BUDGET_EXCEEDED` | `HOOK_SETTINGS_EMPTY` | `PROMPT_TEMPLATE_UNRESOLVED` | `TOPOLOGY_PATTERN_MISMATCH` |
| `COST_CAP_EXCEEDED` | `HOOK_SETTINGS_UNREADABLE` | `PROMPT_TOOL_UNKNOWN` | `TOPOLOGY_PATTERN_REFUSED` |
| `COUNTER_DRIFT` | `HOOK_SHELL_MISSING` | `PROMPT_TOO_LONG` | `TOPOLOGY_REDUNDANT_HOP` |
| `DATASET_DUPLICATE_ID` | `HOOK_UNRESPONSIVE` | `QA_OWNERSHIP_VIOLATION` | `TOPOLOGY_SIMPLICITY_ADVISORY` |
| `DATASET_ITEM_INVALID` | `HOPS_AT_CEILING` | `RAG_AGENT_COMPENSATING` | `TOPOLOGY_TOOL_MISSING` |
| `DATASET_OWNERSHIP_VIOLATION` | `INFRA_BLOCKED` | `RAG_GENERATION_ISSUE` | `TRACEABILITY_GAP` |
| `DATA_ACCESS_ADR_REQUIRED` | `INFRA_LEAK_IN_INTENT` | `RAG_PATTERN_MISMATCH` | `TRACE_MALFORMED` |
| `DATA_ACCESS_AST_MISSING` | `INJECTION_SUCCEEDED` | `RAG_PATTERN_UNUSED` | `TRACE_MISSING` |
| `DATA_ACCESS_CODE_UNPARSABLE` | `INJECTION_SUITE_MISSING` | `RAG_RETRIEVAL_ISSUE` | `TRAJECTORY_DEAD_END` |
| `DATA_ACCESS_ENVELOPE_MISSING` | `INVALID_ARG` | `REFLECTION_NO_GAIN` | `TRAJECTORY_VIOLATION` |
| `DATA_ACCESS_FILTER_POST_GENERATION` | `IR_COMPILE_FAILED` | `REFLECTION_SELF_GRADING` | `UNBOUNDED_LOOP` |
| `DATA_ACCESS_INCONSISTENT` | `IR_DANGLING_REF` | `REFUSAL_POLICY_BYPASSED` | `UNSAFE_TOOL_COHABITATION` |
| `DATA_ACCESS_REGEX_GUARD` | `IR_INVALID` | `REGRESSION` | `WORKSPACE_GHOST_DIR_NOT_EMPTY` |
| `DATA_ACCESS_RETRY_ON_WRITE` | `IR_MALFORMED` | `REGRESSION_WITHIN_NOISE` | `WORKSPACE_MIGRATION_COLLISION` |
| `DATA_ACCESS_SCHEMA_OUTSIDE_ALLOWLIST` | `IR_NOT_FOUND` | `RETRIEVAL_ACCESS_MIXED` | `WORKSPACE_MIGRATION_REGISTRY_INVALID` |
| `DATA_ACCESS_SQL_INTERPOLATED` | `IR_SCHEMA_INVALID` | `RETRIEVAL_BELOW_THRESHOLD` | `WORKSPACE_TREE_INCOMPLETE` |
| `DATA_ACCESS_STRATEGY_UNKNOWN` | `IR_STALE` | `RETRIEVAL_BINDING_MISMATCH` | `WORKSPACE_VERSION_INFERRED` |
| `DATA_ACCESS_WRITE_IN_READONLY` | `JUDGE_CALIBRATION_SYNTHETIC` | `RETRIEVAL_CHUNKING_UNMEASURED` | `WORKSPACE_VERSION_MISSING` |
| `DATA_AUTH_INCOMPLETE` | `JUDGE_CLIENT_MISSING` | `RETRIEVAL_CONFIG_DRIFT` | `WORKSPACE_VERSION_OUTDATED` |
| `DATA_AUTH_MODE_UNKNOWN` | `JUDGE_EQUALS_EVALUATED` | `RETRIEVAL_CORPUS_DUPLICATES` |  |
