---
name: sdda-mission
description: /sdda-mission — PHASE 0 : élicitation d'une MISSION + MISSION GATE (G0)
---
<!-- GÉNÉRÉ par sdda_admin/harness_build.py depuis .sdda/commands/sdda-mission.md.
     NE PAS ÉDITER ICI : toute modification est écrasée au build suivant,
     et le test de parité la signale. Éditer la source. -->

# /sdda-mission — PHASE 0 : élicitation d'une MISSION

<!-- @llm-only-flags-file : tous les flags CLI de cette commande slash sont interprétés par Claude. -->

Invoque l'agent `po-elicitor` pour produire
`workspace/feats/missions/{n}-{Name}.md` depuis `.sdda/templates/mission.template.md`,
puis applique la **MISSION GATE (G0)** : objectif chiffré, budget d'exécution,
vérité terrain, trust boundaries, failure policy, aucun `<à préciser>` résiduel.

**La question que pose cette phase** (AGENT-ROSTER.md) : *quelle est la vérité
contre laquelle on jugera, et que fait le système quand il ne sait pas ?*

**Usage :**
- `/sdda-mission {Name}` — crée la MISSION `{n}` = max existant + 1
- `/sdda-mission {n}` — ré-élicite une MISSION `Draft` existante (append-only)
- `/sdda-mission {Name} --from-brief workspace/feats/briefs/{brief}.md` — part d'un
  brief déjà rédigé (moins de questions)

---

## STEP 1 — Valider les arguments

| Argument | Contrainte |
|---|---|
| `{Name}` | PascalCase, `[A-Z][A-Za-z0-9]{2,40}`, **ou** |
| `{n}` | entier ≥ 1 désignant une MISSION existante en `Status: Draft` |
| `--from-brief {path}` | optionnel — fichier Markdown lisible |

Si argument absent → demander :
```
Quel est le nom de la MISSION à éliciter ? (PascalCase, ex. : SupportAssistant)
```

Si `{Name}` invalide → ERROR :
```
ERROR: /sdda-mission — argument invalide
CAUSE: [INVALID_ARG] "{argument}" n'est ni un nom PascalCase ni un numéro de MISSION
FIX: relancer /sdda-mission {Name} (ex. /sdda-mission SupportAssistant)
```

Si `{n}` fourni et la MISSION n'est pas `Draft` → ERROR :
```
ERROR: /sdda-mission {n} — MISSION déjà spécifiée
CAUSE: [MISSION_NOT_DRAFT] workspace/feats/missions/{n}-{Name}.md est en Status: {Status} (G0 franchie)
FIX: éditer la MISSION à la main (append-only) puis relancer /sdda-caps {n} — ou créer une nouvelle MISSION
```

---

## STEP 2 — Pré-requis STACK.md

Test `workspace/stack/STACK.md` :
- absent → ERROR `[STACK_MISSING]` — FIX : `/sdda-bootstrap`
- contient `{{` → ERROR `[STACK_MALFORMED]` — FIX : `python bootstrap.py --force`

Lire `## Project Config` : `CapGranularityTarget`, `CostPerRunTargetUsd`,
`CostPerRunHardCapUsd`, `LatencyP95TargetMs`, `TokenCeilingPerRun` (valeurs
par défaut proposées à l'utilisateur pendant l'élicitation). Lire `## Active *`
pour la section `## Required Stack` de la MISSION.

---

## STEP 3 — Résoudre `{n}`

- Mode `{Name}` : `{n}` = max des préfixes `workspace/feats/missions/{k}-*.md` + 1
  (1 si aucun). Si un fichier `*-{Name}.md` existe déjà → ERROR :
  ```
  ERROR: /sdda-mission {Name} — nom déjà utilisé
  CAUSE: [MISSION_AMBIGUOUS] workspace/feats/missions/{k}-{Name}.md existe
  FIX: choisir un autre nom, ou relancer /sdda-mission {k} pour ré-éliciter
  ```
- Mode `{n}` : Glob `workspace/feats/missions/{n}-*.md`. 0 fichier →
  `[MISSION_NOT_FOUND]` ; > 1 fichier → `[MISSION_AMBIGUOUS]` (renommer).

Émettre : `MISSION {n}-{Name} — élicitation démarrée (PHASE 0)`.

---

## STEP 4 — Initialiser l'état du run

```bash
RUN_ID=$(python .sdda/sdda.py state new-run \
  --mission {n} --command "/sdda-mission" --tags "$TAGS")
```

Échec (script absent, FS lecture seule) → WARN 1 ligne, continuer
(observabilité best-effort, jamais bloquante).

---

## STEP 5 — Invoquer `po-elicitor`

Agent : `po-elicitor` (défini dans `.sdda/agents/po-elicitor.md`,
façade compilée `.claude/agents/`). Tier `balanced`. Owner exclusif de
`workspace/feats/missions/{n}-*.md` (Create puis append-only — `ownership.md`).

Le prompt d'invocation n'est plus recopié ici — il est **assemblé** :

```bash
python .sdda/sdda.py spawn-brief --agent po-elicitor --mission {n} \
  --work-item "Éliciter la MISSION {n}-{Name}" --fact "brief={--from-brief path | aucun}" --prompt-only
```

Le brief porte la fiche et son hash, le contexte résolu (fichiers, couches,
octets) confronté au `budget_bytes`, le périmètre d'écriture placeholders
résolus, et les **faits injectés** : stacks actives et défauts de Project
Config. Ces faits existent parce que `STACK.md` est dans ses `forbidden_reads`
— le choix technique ne le regarde pas — alors que G0 contrôlera son
`## Required Stack`. Sans ce pont, la fiche est intenable : soit l'agent viole
l'ownership, soit il laisse une section que la gate refusera.

**`execution: inline` — cet agent ne se spawne pas.** Il interroge un humain, et
un sous-agent ne parle à personne : le lancer comme sous-agent transformerait
ses cinq questions en cinq hypothèses, c'est-à-dire exactement l'invention que
sa fiche interdit. La commande joue donc sa fiche dans le fil principal, celui
qui a l'utilisateur — c'est **la seule phase du pipeline où le dialogue humain
est nominal**.

Exception : en non interactif (`--from-brief`, CI, `/sdda-full` sans humain
disponible), il est spawné avec le brief comme unique source. Tout ce que le
brief ne dit pas reste `<à préciser>`, G0 bloque, et c'est le comportement
voulu — pas un échec de l'agent.

Il **ne spawne aucun autre agent** et **n'écrit nulle part ailleurs**. Attendre
sa fin ; relayer sa sortie (ligne de succès ou bloc ERROR 3 lignes).

Sur ERROR de l'agent → `set-phase --phase mission --status fail` + STOP.

---

## STEP 6 — MISSION GATE (G0) — déterministe, 0 token

```bash
python .sdda/sdda.py validate-mission --mission {n} --json \
  > workspace/.sys/.validation/G0-{n}-{Name}.json
```

Contrôles (INVARIANTS `mission-budget-declared`, G0) :

| # | Contrôle | Classe si KO |
|---|---|---|
| 1 | `## Quantified Goal` : `Metric`, `Target`, `Deadline` renseignés, `Target` numérique ou comparatif | `[MISSION_GOAL_UNQUANTIFIED]` |
| 2 | `## Execution Budget` : les 4 clés présentes, `HardCap ≥ Target`, valeurs > 0 | `[MISSION_BUDGET_MISSING]` |
| 3 | `## Ground Truth` : `Source`, `Owner`, `Volume available` renseignés | `[MISSION_GROUND_TRUTH_MISSING]` |
| 4 | `## Trust Boundaries` : au moins une ligne `Untrusted` ou `NONE` explicite | `[MISSION_TRUST_UNDECLARED]` |
| 5 | `## Failure Policy` : les 4 cas (hors compétence, confiance faible, outil indisponible, budget atteint) | `[MISSION_FAILURE_POLICY_MISSING]` |
| 6 | Aucun `<à préciser>` / `{…}` / `TODO` résiduel dans le fichier | `[MISSION_INCOMPLETE]` |
| 7 | `## Required Stack` ⊆ stacks actives de `STACK.md` | `[MISSION_STACK_MISMATCH]` |
| 8 | Toute Business Rule et tout AC système portent un identifiant stable `BR-i` / `AC-i` | `[MISSION_ID_UNSTABLE]` |
| 9 | Aucun identifiant de framework (`StateGraph`, `Kernel`, `AgentExecutor`, …) — P11 | `[FRAMEWORK_LEAK_IN_CONTRACT]` |

| Exit | Verdict | Action |
|:-:|---|---|
| `0` | 🟢 G0 franchie | `compute_status.py` fait passer la MISSION à `Specified` (partiel : G1 requise pour l'état plein) ; → STEP 7 |
| `1` | 🔴 G0 rouge | STOP + ERROR ci-dessous ; la MISSION reste `Draft` |
| `3` | infra | `[INFRA_BLOCKED]` — STOP |

Format ERROR (exit 1) :
```
ERROR: /sdda-mission {n} — MISSION GATE rouge
CAUSE: [MISSION_GATE_FAILED] {k} contrôle(s) KO : [MISSION_INCOMPLETE] ×{a}, [MISSION_BUDGET_MISSING] ×{b} — rapport workspace/.sys/.validation/G0-{n}-{Name}.json
FIX: compléter les sections listées dans le rapport (ou répondre aux <à préciser>) puis relancer /sdda-mission {n}
```

**Aucun bypass pour G0.** Une MISSION sans vérité terrain ni budget ne doit pas
entrer dans le pipeline : le coût de la découvrir plus tard est le plus élevé
du domaine (PHILOSOPHY P6). `SDDA_BYPASS_MISSION_GATE` n'existe pas.

**State tracking** : `set-phase --phase mission --status {pass|fail}
--payload-json '{"confidence":"{high|medium|low}","untrustedSources":N}'`.

---

## STEP 7 — Recalcul d'état (LIFECYCLE R1)

```bash
python .sdda/sdda.py compute-status --mission {n}
```

L'état est **dérivé** des rapports `workspace/.sys/.validation/{n}-G*.json`,
jamais déclaré. Si l'agent a écrit un `Status:` non couvert par un rapport, le
script émet `[STATUS_UNBACKED]` (WARN) et écrase.

---

## STEP 8 — Récap

```
✅ MISSION {n}-{Name} — PHASE 0 terminée · G0 🟢

Fichier          : workspace/feats/missions/{n}-{Name}.md
Confidence       : {high|medium|low}   (plafonne toutes les CAPs dérivées — R4)
Objectif chiffré : {Metric} {Target} avant {Deadline}
Budget           : ${CostPerRunTargetUsd}/run (cap ${CostPerRunHardCapUsd}) · p95 {LatencyP95TargetMs} ms · {TokenCeilingPerRun} tokens
Ground truth     : {Source} ({Volume} items)
Untrusted        : {liste} → {k} suite(s) d'injection obligatoire(s) en G7
Business Rules   : {B} · AC système : {A}

Prochaine étape :
  - /sdda-caps {n}   pour découper en capabilities mesurables (PHASE 1)
  - ou /sdda-full {n} pour le pipeline complet
```

Si G0 rouge, ne rien ajouter (le bloc ERROR suffit).

---

## Règles de cette commande

- **Un seul agent** (`po-elicitor`), aucun spawn imbriqué.
- **Q/R utilisateur autorisées** pendant STEP 5 uniquement — c'est la nature
  de l'élicitation. Aucune Q/R après.
- **Append-only** sur une MISSION existante : l'agent ajoute, ne réécrit pas
  une section déjà remplie (l'humain reste l'auteur de ce qu'il a validé).
- **Pas de CAP, pas de topologie, pas de code** : réservés aux phases suivantes.
- **Le budget est une exigence fonctionnelle** : l'agent ne peut pas laisser
  `## Execution Budget` vide « pour plus tard ».

---

## Chat Output Protocol

Applique `@.sdda/rules/output-protocol.md`. Label `[MISSION]`, plage
`0-100%`. Erreurs : bloc ERROR/CAUSE/FIX 3 lignes. Bypass verbeux :
`SDDA_CHAT_VERBOSE=1`.
