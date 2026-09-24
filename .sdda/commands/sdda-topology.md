---
name: sdda-topology
description: /sdda-topology — PHASE 2 : topologie + contrats (parallèle) + compilation IR + TOPOLOGY GATE (G2)
---
# /sdda-topology — PHASE 2 : topologie, contrats, IR

<!-- @llm-only-flags-file : tous les flags CLI de cette commande slash sont interprétés par Claude. -->

> ⚠️ **Commande interne** — invoquée par `/sdda-full` STEP 4.
> Utilisateur final : préférer `/sdda-full {n}`.

Décide l'architecture de la MISSION `{n}` en trois temps :

```
2.1  architect-topology                         (SEUL — fixe le périmètre)
       -> workspace/pipeline/topology/{n}-topology.md   (graphe Mermaid inclus, §4)
2.1b gen-source-tools --scope contracts         (script, 0 token — si declared-sources)
       -> workspace/pipeline/contracts/tools/{n}-{source}-{kind}.tool.md  (squelettes)
2.2  architect-tools ∥ architect-rag ∥ architect-data ∥ architect-memory
       -> workspace/pipeline/contracts/{tools,retrieval,memory}/**   (PARALLÈLE, borné MaxParallel)
2.9  ir_compiler.py  (script déterministe, 0 token)
       -> workspace/.sys/.ir/{n}-system.ir.json
     [TOPOLOGY GATE]  validate_packaging.py (le livrable est-il réalisable ?)
                      + validate_architecture.py (complétude de la déclaration, P7)
                      + validate_ir.py + estimate_budget.py  (sur l'IR, pas sur la prose)
```

**La question que pose cette phase** : *quelle est la topologie la plus simple
qui tienne, et pourquoi pas plus simple encore ?* (PHILOSOPHY P7 — le biais par
défaut est **un agent, des outils**, et il doit être vaincu par écrit.)

**Usage :**
- `/sdda-topology {n}`
- `/sdda-topology {n} --recompile-only` — saute les agents, recompile l'IR et rejoue G2
  (après édition manuelle d'un contrat)

---

## STEP 1 — Valider les arguments

`{n}` entier ≥ 1 **obligatoire**. `--recompile-only` optionnel.

Si absent → demander `Quel est le numéro de la MISSION à architecturer ? (ex. : 1)`.
Si non numérique → ERROR `[INVALID_ARG]` (format 3 lignes, cf. `/sdda-caps`).

---

## STEP 2 — Pré-conditions

1. `workspace/stack/STACK.md` présent et rendu (`[STACK_MISSING]` / `[STACK_MALFORMED]`).
2. MISSION `{n}` unique (`[MISSION_NOT_FOUND]` / `[MISSION_AMBIGUOUS]`).
3. **G1 franchie** :
   ```bash
   python .sdda/sdda.py compute-status --mission {n} --require-gate G1
   ```
   Exit ≠ 0 → ERROR :
   ```
   ERROR: /sdda-topology {n} — CAPs non spécifiées
   CAUSE: [CAP_GATE_NOT_PASSED] rapports workspace/.sys/.validation/G1-{n}-{MissionName}.json / G1-{n}-{m}-{Cap}.json absents, rouges ou périmés
   FIX: relancer /sdda-caps {n} (G1 doit être verte : on n'architecture pas des AC non mesurables)
   ```
   3.bis **Roster déclaré** — si `feats/{n}-roster.md` existe, il doit
   être complet **avant** de payer l'architecte :
   ```bash
   python .sdda/sdda.py roster validate --mission {n} --if-present
   ```
   Exit 1 → STOP + ERROR (les findings du script, `fichier:$.chemin`) :
   ```
   ERROR: /sdda-topology {n} — roster incomplet
   CAUSE: [ARCH_ROSTER_PLACEHOLDER] `$.subagents[0].tier` est encore `<à préciser>` — workspace/feats/{n}-roster.md
   FIX: l'architecte tranche, puis /sdda-roster {n} --validate ; le framework ne comble aucun trou (P7)
   ```
   Roster absent → WARN `[ARCH_ROSTER_MANIFEST_MISSING]`, continuer : le
   repli `## 2. Roster déclaré` reste accepté par G2 (et `/sdda-roster {n}`
   est la forme recommandée).
4. Lire `## Project Config` : `MaxParallel`, `MaxAgentsWarnAt`,
   `CostPerRunTargetUsd`, `CostPerRunHardCapUsd`, `LatencyP95TargetMs`,
   `TokenCeilingPerRun`, `MaxIterations`, `MaxToolCalls`, `MaxDelegationDepth`,
   `AgentTimeoutSec`, `OnBoundExceeded`. Lire `## Active Orchestration`,
   `## Active RAG`, `## Active Data Access`, `## Active Memory`, `## Active Tools`.

```bash
RUN_ID=${SDDA_RUN_ID:-$(python .sdda/sdda.py state new-run \
  --mission {n} --command "/sdda-topology" --tags "$TAGS")}
```

5. **Packs et budget de contexte** — avant tout spawn :

```bash
python .sdda/sdda.py context-pack check --agent all --json || \
python .sdda/sdda.py context-pack build --agent all
python .sdda/sdda.py spawn-brief --agent architect-topology --mission {n} --prompt-only
```

`[PACK_UNUSABLE]` (pack absent ou périmé) et `[CONTEXT_BUDGET_EXCEEDED]`
(contexte au-dessus du `budget_bytes` de `loader.yml`) **refusent le spawn** :
un agent qui déborde ne rend pas une sortie plus courte, il rend une sortie
tronquée et confiante.

Si `--recompile-only` → STEP 4.bis (0 token, idempotent) puis STEP 6.

---

## STEP 3 — Idempotence

Glob `workspace/pipeline/topology/{n}-topology.md`. Si présent ET `MISSION hash` ==
hash courant ET tous les `Parent MISSION hash` des CAPs sont à jour → proposer
le court-circuit :

```
⊘ /sdda-topology {n}: topologie à jour (hash MISSION/CAPs inchangés).
  Recompilation IR + G2 seulement. Pour forcer la re-conception : supprimer workspace/pipeline/topology/{n}-*.
```

→ STEP 6. Sinon → STEP 4.

---

## STEP 4 — `architect-topology` (SEUL — barrière)

Agent : `architect-topology` (`.sdda/agents/architect-topology.md`). Tier
**`deep`** par défaut (`tier_floor: balanced`, `tier_ceiling: deep` — bornes
d'`agent-bounds.yaml`, non surchargeables par le Project Config ; aucun agent
n'a de plancher `deep`, sinon le mode `dynamic` serait une décoration). Owner
exclusif de
`workspace/pipeline/topology/{n}-*`.

Il s'exécute **seul et avant** les quatre architectes de contrats : c'est lui
qui fixe le périmètre (quels agents, quels outils, quels retrievers, quelle
mémoire existent). Lancer les contrats en parallèle de la topologie produirait
des contrats pour des composants qui n'existeront pas.

Prompt d'invocation :
```
Concevoir la topologie de la MISSION {n}-{MissionName} à partir de ses {C} CAPs.
Roster DÉCLARÉ : {workspace/feats/{n}-roster.md (validé) | aucun roster — repli ## 2. Roster déclaré}.
Le recopier tel quel ; ne renommer, n'ajouter ni ne retirer aucun agent (P7). Ne PAS écrire dans {n}-roster.md.
Template : .sdda/templates/topology.template.md. Graphe : bloc ```mermaid de ## 4 (aucun .mmd).
Procédure imposée (AGENT-ROSTER.md §2) : partir de UN agent + des outils ; pour chaque
CAP demander « un outil suffit-il ? » ; n'escalader qu'avec une des 5 raisons closes de P7 ;
remplir « Alternative plus simple considérée » (vide = WARN [TOPOLOGY_SIMPLICITY_ADVISORY] : l'architecture t'appartient, P7).
Budget déclaré : ${CostPerRunTargetUsd} cible / ${CostPerRunHardCapUsd} cap · p95 {LatencyP95TargetMs} ms.
Bornes minimales par agent : MaxIterations={…} MaxToolCalls={…} MaxDelegationDepth={…}
AgentTimeoutSec={…} OnBoundExceeded={…}. Patterns autorisés : {## Active Orchestration}.
MaxAgentsWarnAt={…}. Ne nommer AUCUNE API de framework. Choisir des TIERS, jamais des modèles.
Remplir ## Allocated To de chaque CAP (agents / tools / retrievers) — seul droit d'écriture
hors workspace/pipeline/topology/, mode Edit-section exclusif.
Écrire pour chaque agent du produit un contrat workspace/pipeline/contracts/agents/{n}-{agent}.agent.md
(template agent-contract.template.md) — SANS prompt (owner dev-prompt, PHASE 4).
Reporter au §5 Skills les `skills:` déclarées par l'architecte au roster, telles quelles : une
skill dit ce que l'agent SAIT FAIRE, un outil ce qu'il a LE DROIT D'APPELER — ne jamais convertir
l'une en l'autre. N'en inventer aucune (P7) ; §5 vide est une déclaration valide.
```

Attendre la fin. ERROR → `set-phase --phase topology --status fail` + STOP.

Post-check déterministe immédiat (avant de payer les 4 contrats), **dans cet
ordre** — la complétude de la déclaration d'abord, sa cohérence ensuite :

```bash
python .sdda/sdda.py validate-packaging    --mission {n}   # qu'est-ce qu'on LIVRE ?
python .sdda/sdda.py validate-architecture --mission {n}   # P7 : l'architecte a-t-il décidé ?
python .sdda/sdda.py validate-topology     --mission {n} --pre   # pré-contrôle : aucun rapport de gate
```

`validate_packaging.py` passe **en premier** et c'est délibéré : il ne coûte
rien, et il tranche la question qui conditionne les autres. Un `ApiFramework`
incohérent avec le langage actif n'échoue pas ici mais à la compilation, trois
phases plus loin — après avoir payé les contrats, les prompts et les agents. Il
écrit la part `packaging` du rapport G2.

| Exit | Sens | Action |
|:-:|---|---|
| `0` | roster complet pour le pattern actif, sections obligatoires présentes | → STEP 5 |
| `1` | `[ARCH_ROSTER_MISSING]` / `[ARCH_SPEC_INCOMPLETE]` / `[ARCH_*]` — la spécification ne suffit pas à construire l'architecture choisie | STOP + ERROR — **rendre la main à l'architecte**, ne rien inventer |
| `1` | `[ROUTER_NO_FALLBACK]` — pattern `router` sans chemin « aucune classe » | STOP + ERROR |
| `0` + WARN | `[TOPOLOGY_SIMPLICITY_ADVISORY]` — un agent au-delà du premier sans raison nommée | **continuer** : l'architecture appartient à l'architecte (P7) |

```
ERROR: /sdda-topology {n} — spécification incomplète pour l'architecture choisie
CAUSE: [ARCH_SPEC_INCOMPLETE] `orchestration/supervisor` exige au moins 2 subagent(s) déclaré(s), 1 trouvé(s)
FIX: compléter `## 2. Roster déclaré` dans workspace/pipeline/topology/{n}-topology.md —
     c'est l'ARCHITECTE qui déclare les agents, leurs rôles et leurs outils.
     Le framework ne les invente pas (PHILOSOPHY P7).
     Ce que le pattern actif exige : validate_architecture.py --explain
```

**State tracking** : `set-phase --phase topology --status pass
--payload-json '{"rootPattern":"…","agents":N,"tools":T,"retrievers":R}'`.

---

## STEP 4.bis — Contrats des sources déclarées (script, 0 token)

Si `## Active Data Access` = `declared-sources` :

```bash
python .sdda/sdda.py gen-source-tools --write --scope contracts --mission {n}
```

Il crée le squelette de contrat `workspace/pipeline/contracts/tools/{n}-{source}-{kind}.tool.md`
de chaque outil de source (`lookup`, `search`, `count`) et **ne touche jamais**
à un contrat existant. Joué ici, **avant** les architectes parallèles et
**avant** `ir-compiler` : `architect-tools` complète ces squelettes au STEP 5
(§7), et l'IR les compile au STEP 6 — donc G2 les juge.

Ils étaient générés par `dev-backend` en PHASE 3.0 (`gen-source-tools --write`,
contrats et code d'un seul coup) : après la compilation de l'IR et après G2.
L'IR ne voyait pas les outils de source, G2 non plus, et le code des wrappers
atterrissait dans la zone de `dev-data` sous la main de l'agent de la coquille.
Le code, lui, se génère en PHASE 3, dans la couche de `dev-data`
(`/sdda-build` STEP 3.1, `--scope code`).

| Exit | Cause | Action |
|:-:|---|---|
| `0` | contrats créés ou déjà présents, aucune dérive (`name`, `Trust`) | → STEP 5 |
| `1` | `[DATA_SOURCE_SCHEMA_MISSING]` — une source sans schéma figé | **STOP humain** : `gen-source-tools --infer --source {id}`, **relire** le schéma, puis relancer. Un contrat se dérive du schéma figé ; sans lui il n'y a rien à contracter |
| `1` | `[DATA_TOOL_CONTRACT_DRIFT]` | STOP + ERROR : le contrat existant contredit la déclaration de la source |

---

## STEP 5 — Contrats en parallèle (borné `MaxParallel`)

Construire le `BATCH` depuis la topologie et `STACK.md` :

| Condition | Agent ajouté | Écrit dans | Tier |
|---|---|---|:-:|
| ≥ 1 outil alloué | `architect-tools` | `workspace/pipeline/contracts/tools/{n}-{tool}.tool.md` | balanced |
| `## Active RAG` ≠ `none` ET ≥ 1 retriever alloué | `architect-rag` | `workspace/pipeline/contracts/retrieval/{n}-{index}.retrieval.md` | **deep** |
| `## Active Data Access` ≠ `none` | `architect-data` | `workspace/pipeline/contracts/tools/{n}-{view}.tool.md` (data tools) + ADR | balanced |
| `## Active Memory` ≠ `none` | `architect-memory` | `workspace/pipeline/contracts/memory/{n}-memory.md` | balanced |

Agents absents du `BATCH` → ligne `⊘ {agent}: skipped ({raison})`.

**Dispatch** : un seul message multi-`Agent`, **au plus `MaxParallel` agents
simultanés**. Si `|BATCH| > MaxParallel`, découper en vagues séquentielles
(`architect-tools` et `architect-rag` en première vague — les plus longs).
Les chemins d'écriture sont disjoints par la matrice d'ownership :
`architect-tools` et `architect-data` écrivent tous deux sous
`contracts/tools/` mais sur des basenames disjoints (`{tool}` vs `{view}`),
vérifiés par `audit_ownership.py` en post-step.

Prompt commun (adapté par agent) :
```
MISSION {n}-{MissionName}. Topologie : workspace/pipeline/topology/{n}-topology.md (périmètre CLOS —
ne créer aucun composant absent de l'allocation). Template : .sdda/templates/{x}-contract.template.md.
Neutralité framework (P11). Tout outil déclare sideEffectClass ∈ {read-only, write-scoped,
write-destructive, external-side-effect} et une safetyStrategy si non read-only (P8).
Tout retriever déclare ses seuils de gate (RetrievalRecallAtK={…}, RetrievalNdcgMin={…},
GroundednessMin={…}, CitationResolveRateMin={…}) et NOMME son golden set (produit par qa-evals).
Toute mémoire déclare rétention et politique PII.
```

Attendre **tous** les agents de la vague. Un ERROR n'annule pas les autres :
collecter, puis STOP si ≥ 1 ERROR avec la liste.

Post-step déterministe :

```bash
python .sdda/sdda.py audit-ownership --mission {n} --phase 2
python .sdda/sdda.py validate-tool-contract --mission {n} --static
python .sdda/sdda.py validate-data-access --mission {n}
```

`[OWNERSHIP_VIOLATION]`, `[SIDE_EFFECT_UNDECLARED]`, `[DB_ENVELOPE_MISSING]`
→ STOP + ERROR (le contrat fautif est nommé). Ces contrôles sont rejoués en G3
mais on ne compile pas un IR depuis des contrats qu'on sait invalides.

**State tracking** : `set-phase --phase contracts --status {pass|fail}
--payload-json '{"tools":T,"retrievers":R,"memory":bool,"dataAccess":bool}'`.

---

## STEP 6 — PHASE 2.9 : compilation de l'IR (script, 0 token)

D'abord la passe **complète** de la topologie — celle qui vérifie que chaque
contrat annoncé au §« Contrats produits » et chaque contrat d'agent existe sur
disque. Jouée ici, après les architectes parallèles et avant l'IR, y compris en
`--recompile-only` :

```bash
python .sdda/sdda.py validate-topology --mission {n}
python .sdda/sdda.py ir-compiler --mission {n} \
  --out workspace/.sys/.ir/{n}-system.ir.json
```

`validate-topology` exit 1 (`[TOPOLOGY_CONTRACT_MISSING]`, `[AGENT_CONTRACT_MISSING]`)
→ STOP + ERROR : on ne compile pas un IR qui référence un contrat absent.

Seule cette passe écrit la part `topology` de G2. La passe `--pre` du STEP 4
n'écrit plus rien : elle rendait une part verte sans qu'aucun contrat ait été
vérifié, et c'était la seule passe que la commande lançait — le code annonçait
une passe complète « rejouée avant la compilation de l'IR » que personne
n'exécutait.

Entrées : MISSION, CAPs, TOPOLOGY (graphe compris), `contracts/**`, `STACK.md`.
Sortie : `{n}-system.ir.json` conforme à `.sdda/registry/ir.schema.json`, avec
`compiledFrom.{missionHash,capHashes,topologyHash,stackHash}`.

| Exit | Action |
|:-:|---|
| `0` | → STEP 7 |
| `1` | STOP + ERROR `[IR_COMPILE_FAILED]` (section manquante, référence à un composant non déclaré — `fichier:section` sur stderr) |
| `3` | STOP + ERROR `[INFRA_BLOCKED]` |

> L'IR est **régénérable et jetable** (AGENTIC-IR.md §1) : jamais édité à la
> main. Toute divergence IR ↔ Markdown se résout en faveur du Markdown puis
> par recompilation.

---

## STEP 7 — TOPOLOGY GATE (G2) — déterministe, 0 token, sur l'IR

```bash
python .sdda/sdda.py validate-ir --ir workspace/.sys/.ir/{n}-system.ir.json --json \
  > workspace/.sys/.validation/{n}-G2-ir.recap.json
python .sdda/sdda.py estimate-budget --ir workspace/.sys/.ir/{n}-system.ir.json --json \
  > workspace/.sys/.validation/{n}-G2-budget.recap.json
```

> **Deux fichiers, jamais `>>`.** Concaténer deux documents JSON dans un même
> fichier ne produit pas un JSON : produit ainsi, l'ancien `{n}-G2-topology.json`
> n'était relisible par personne. Ces deux-là ne servent qu'au récap — les rapports de
> gate qui comptent sont écrits par les scripts eux-mêmes, sous les noms que
> `gate_reports` sait relire. D'où le suffixe `.recap` : il dit que ces fichiers
> ne sont pas des rapports de gate, et évite qu'on les prenne pour tels.

Les 11 contrôles de `AGENTIC-IR.md §4` + budget :

| # | Contrôle | Classe si KO |
|---|---|---|
| 1 | Schéma `ir.schema.json` | `[IR_INVALID]` |
| 2 | Références closes (tools, retrievers, refs de nœuds) | `[IR_DANGLING_REF]` |
| 3 | Atteignabilité : tout nœud depuis `entryNode`, tout chemin vers un `terminalNode` | `[GRAPH_UNREACHABLE]` |
| 4 | Tout cycle coupé par une borne (`maxHops`, `maxIterations`, condition décrémentante) | `[UNBOUNDED_LOOP]` |
| 5 | Σ `budgetUsd` sur le plus long chemin ≤ `costPerRunHardCapUsd` | `[BUDGET_EXCEEDED_ESTIMATE]` |
| 6 | Toute CAP dans un `servesCaps` ou portée par un outil/retriever, et dans `traceability` | `[TRACEABILITY_GAP]` |
| 7 | Moindre privilège : tout outil câblé à un agent exigé par ≥ 1 de ses CAPs | `[TOOL_SCOPE_EXCESS]` |
| 8 | Tout outil non `read-only` porte une `safetyStrategy` | `[SIDE_EFFECT_UNDECLARED]` |
| 9 | Tout agent avec une entrée `untrusted` a une suite d'injection dans `evaluation.suites` | `[INJECTION_SUITE_MISSING]` |
| 10 | Aucun identifiant de framework dans l'IR | `[FRAMEWORK_LEAK_IN_CONTRACT]` |
| 11 | Tout agent porte les 5 bornes + `onBoundExceeded` | `[AGENT_BOUNDS_MISSING]` |
| 12 | Justification P7 par agent > 1 (rejoué depuis la topologie) | `[TOPOLOGY_UNJUSTIFIED]` |
| B | Budget estimé (coût, latence p95, tokens) ≤ budget déclaré de la MISSION | `[BUDGET_EXCEEDED_ESTIMATE]` |

| Verdict | Condition | Action |
|---|---|---|
| 🟢 | 0 KO | MISSION + TOPOLOGY → `Architected` ; → STEP 8 |
| 🟡 | 0 KO bloquant, mais `agents > MaxAgentsWarnAt` ou coût estimé > `CostPerRunTargetUsd` (sous le cap) | continuer + WARN dans le récap |
| 🔴 | ≥ 1 KO | STOP + ERROR |

```
ERROR: /sdda-topology {n} — TOPOLOGY GATE rouge
CAUSE: [TOPOLOGY_GATE_FAILED] [UNBOUNDED_LOOP] cycle supervisor→billing→supervisor sans borne ; [BUDGET_EXCEEDED_ESTIMATE] $0.41 estimé > cap $0.25 — rapports workspace/.sys/.validation/G2-{n}-{MissionName}.{topology,ir,budget}.json
FIX: corriger workspace/pipeline/topology/{n}-topology.md et/ou les contrats, puis /sdda-topology {n} --recompile-only
```

**Bypass** (INVARIANTS `budget-estimated-before-code`) : `SDDA_BYPASS_BUDGET_ESTIMATE=1`
neutralise **uniquement** le contrôle B (le budget), jamais 1-12. Audit-loggué
dans `workspace/.sys/.audit/bypasses.jsonl` via `audit_bypass.py`. Un cycle non
borné ou une référence fantôme ne se bypassent pas.

**State tracking** : `set-phase --phase topology_gate --status {pass|warn|fail}
--payload-json '{"estCostUsd":x,"estP95Ms":y,"estTokens":z,"agents":N}'`.

---

## STEP 8 — Recalcul d'état + récap

```bash
python .sdda/sdda.py compute-status --mission {n}
```

```
✅ MISSION {n}-{MissionName} — PHASE 2 terminée · G2 {🟢|🟡}

Topologie        : {rootPattern} · {N} agent(s) · {T} outil(s) · {R} retriever(s) · mémoire {oui|non}
Alternative écartée : {topologie N-1} — disqualifiée par « {critère} »
Justifications P7  : {agent-2}: {raison} · {agent-3}: {raison}
Contrats         : agents {N} · tools {T} · retrieval {R} · memory {0|1}
IR               : workspace/.sys/.ir/{n}-system.ir.json ({nodes} nœuds, {edges} arêtes, maxHops {h})
Budget estimé    : ${est}/run (cible ${target}, cap ${cap}) · p95 {ms} ms · {tok} tokens  {✅|🟡}
Effets de bord   : read-only {a} · write-scoped {b} · write-destructive {c} · external {d}
Untrusted inputs : {k} agent(s) → suites d'injection déclarées {k}/{k}

Prochaine étape :
  - /sdda-build {n}   pour matérialiser le socle, les agents et l'orchestration (PHASES 3→5)
  - ou /sdda-full {n} pour le pipeline complet
```

---

## Règles de cette commande

- **Barrière stricte** : `architect-topology` termine **avant** tout contrat.
- **Parallélisme borné** par `MaxParallel` ; sûr par ownership (chemins disjoints).
- **Aucun agent ne spawne un autre agent** — la commande orchestre les vagues.
- **Aucun prompt écrit** ici (owner `dev-prompt`, PHASE 4). **Aucun code.**
- **Aucun dataset** créé — seulement nommé (owner `qa-evals`).
- **La gate juge l'IR, pas la prose** : un contrat convaincant mais
  incompilable est rouge.

---

## Chat Output Protocol

Applique `@.sdda/rules/output-protocol.md`. Label `[TOPOLOGY]`, plage
`0-100%` (architecte 0-40 %, contrats 40-80 %, IR + gate 80-100 %). Erreurs :
bloc ERROR/CAUSE/FIX 3 lignes. Bypass verbeux : `SDDA_CHAT_VERBOSE=1`.
