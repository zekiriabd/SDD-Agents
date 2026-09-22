---
name: sdda-caps
description: /sdda-caps — PHASE 1 : découpe d'une MISSION en capabilities mesurables + CAP GATE (G1)
---
<!-- GÉNÉRÉ par sdda_admin/harness_build.py depuis .sdda/commands/sdda-caps.md.
     NE PAS ÉDITER ICI : toute modification est écrasée au build suivant,
     et le test de parité la signale. Éditer la source. -->

# /sdda-caps — PHASE 1 : découpe en capabilities

<!-- @llm-only-flags-file : tous les flags CLI de cette commande slash sont interprétés par Claude. -->

> ⚠️ **Commande interne** — invoquée par `/sdda-full` STEP 3.
> Utilisateur final : préférer `/sdda-full {n}` (pré-conditions, idempotence, état).

Invoque l'agent `po-capabilities` pour découper la MISSION `{n}` en
CAPABILITYs (`workspace/caps/{n}-{m}-{Name}.md`, 1 fichier = 1 CAP), puis
applique la **CAP GATE (G1)** : chaque AC nomme **métrique + seuil + dataset +
grader + k runs** ; chaque `BR-i` / `AC-i` de la MISSION est couvert par ≥ 1 CAP.

C'est la règle la plus structurante du framework (PHILOSOPHY P2) : elle force
« comment saura-t-on que ça marche ? » **avant** la première ligne de code.

**Usage :**
- `/sdda-caps {n}` — découpe la MISSION `{n}`
- `/sdda-caps {n} --allow-large-mission` — bypass conscient de `CapGranularityHardCap`

---

## STEP 1 — Valider les arguments

- `{n}` (entier ≥ 1, **obligatoire**).
- `--allow-large-mission` (optionnel) — dépasse `CapGranularityHardCap`
  (défaut 15). Effet : `export SDDA_ALLOW_LARGE_MISSION=1` + ligne dans
  `workspace/.sys/.audit/bypasses.jsonl`. Préférer découper la MISSION.

Si `{n}` absent → demander :
```
Quel est le numéro de la MISSION à découper ? (ex. : 1 pour workspace/missions/1-SupportAssistant.md)
```

Si non numérique → ERROR :
```
ERROR: /sdda-caps — argument invalide
CAUSE: [INVALID_ARG] "{argument}" n'est pas un entier
FIX: relancer /sdda-caps {n} avec n entier (ex. /sdda-caps 1)
```

### Propagation `--allow-large-mission`

```bash
if [[ "$@" == *--allow-large-mission* ]]; then
  export SDDA_ALLOW_LARGE_MISSION=1
  python .sdda/sdda.py audit-bypass \
    --command "/sdda-caps {n}" --bypass CapGranularityHardCap \
    --reason "${SDDA_BYPASS_REASON:-non renseignée}"
fi
```

---

## STEP 2 — Vérifier la MISSION et son état

Glob `workspace/missions/{n}-*.md` :
- 0 fichier → ERROR `[MISSION_NOT_FOUND]` — FIX : `/sdda-mission {Name}`
- > 1 fichier → ERROR `[MISSION_AMBIGUOUS]` — FIX : renommer

Vérifier que **G0 est franchie** — l'état est un fait dérivé, pas une ligne
`Status:` (LIFECYCLE R1) :

```bash
python .sdda/sdda.py compute-status --mission {n} --require-gate G0
```

Exit ≠ 0 → ERROR :
```
ERROR: /sdda-caps {n} — MISSION non spécifiée
CAUSE: [MISSION_GATE_NOT_PASSED] aucun rapport workspace/.sys/.validation/{n}-G0-mission.json vert
FIX: relancer /sdda-mission {n} (G0 doit être verte avant toute découpe)
```

Stocker `{MissionName}`, `{MissionConfidence}`, le hash sha256 du fichier
MISSION (8 premiers caractères → `Parent MISSION hash` des CAPs).

---

## STEP 3 — Idempotence et CAPs existantes

Glob `workspace/caps/{n}-*.md` :

| Situation | Action |
|---|---|
| aucune CAP | génération complète |
| CAPs présentes, `Parent MISSION hash` == hash courant | `⊘ /sdda-caps {n}: CAPs à jour (hash MISSION inchangé)` — sauter STEP 5, aller à STEP 6 (re-jouer G1 seulement) |
| CAPs présentes, hash différent | WARN `[MISSION_HASH_MISMATCH]` : la MISSION a bougé sous les CAPs. L'agent **régénère** en conservant les identifiants `{m}` des CAPs dont le `Statement` est inchangé, et marque `Status: Draft` les autres |

> Le hash de MISSION dans la CAP est le descendant direct du `Parent FEAT hash`
> de SDD_Pro : il rend visible une spécification qui a changé après découpe.

---

## STEP 4 — Initialiser l'état du run

```bash
RUN_ID=${SDDA_RUN_ID:-$(python .sdda/sdda.py state new-run \
  --mission {n} --command "/sdda-caps" --tags "$TAGS")}
```

(`SDDA_RUN_ID` est propagé par `/sdda-full` — continuité de l'audit-trail.)

---

## STEP 5 — Invoquer `po-capabilities`

Agent : `po-capabilities` (`.sdda/agents/po-capabilities.md`). Tier
`balanced`. Owner exclusif de `workspace/caps/{n}-*.md` (Create exclusif).

Prompt d'invocation :
```
Découper la MISSION {n}-{MissionName} en capabilities. Template :
.sdda/templates/capability.template.md.
Cible CapGranularityTarget={…}, warn à {CapGranularityWarnAt}, hard cap {CapGranularityHardCap}
{(bypass SDDA_ALLOW_LARGE_MISSION actif)}.
Parent MISSION hash : sha256:{hash-8}. Confidence plafond : {MissionConfidence} (R4).
Chaque AC : metric + threshold + dataset (chemin sous workspace/datasets/) + grader + runs
(3, ou 5 si Criticality: critical). Laisser ## Allocated To vide (décision de PHASE 2).
Couvrir chaque BR-i et AC-i de la MISSION dans ## Covers d'au moins une CAP.
```

Contraintes que l'agent respecte (rappelées dans son prompt système) :
- une CAP **n'est pas un agent** — l'allocation appartient à `architect-topology` ;
- un AC du type « répond de manière utile » est **interdit** ; l'agent choisit
  un grader de la liste close (`exact`, `regex`, `schema`, `numeric-tolerance`, `semantic-similarity`, `trajectory`, `cost`, `latency`, `llm-judge`) ;
- `Confidence` ≤ celle de la MISSION (la confiance ne monte jamais — R4) ;
- il **ne crée pas** les datasets (owner : `qa-evals`, PHASE 6) — il les
  **nomme** ;
- aucun spawn d'autre agent.

Attendre la fin ; relayer la sortie. ERROR → `set-phase --phase caps --status fail` + STOP.

### STEP 5.bis — Résolution du sentinel de hash (déterministe)

L'agent n'a pas `Bash` et écrit `Parent MISSION hash: sha256:COMPUTE_REQUIRED`.
Résoudre en post-step (0 token, ~50 ms, idempotent) :

```bash
python .sdda/sdda.py resolve-cap-hash-sentinel --mission {n}
```

| Exit | Action |
|:-:|---|
| `0` | continuer |
| `2` | STOP + ERROR `[CAP_HASH_PLACEHOLDER]` (sentinel persiste — vérifier permissions FS) |
| `3` | STOP + ERROR `[INFRA_BLOCKED]` |

---

## STEP 6 — CAP GATE (G1) — déterministe, 0 token

```bash
python .sdda/sdda.py validate-cap --mission {n} --json \
  > workspace/.sys/.validation/{n}-G1-cap.json
```

Contrôles (INVARIANTS `cap-ac-must-be-evaluable`, G1) :

| # | Contrôle | Classe si KO |
|---|---|---|
| 1 | Chaque AC a `metric`, `threshold`, `dataset`, `grader`, `runs` non vides | `[AC_NOT_EVALUABLE]` |
| 2 | `threshold` est un comparateur numérique (`>= 0.85`, `<= 1200`, `== 0`) | `[AC_NOT_EVALUABLE]` |
| 3 | `grader` ∈ liste close ; `llm-judge` ⇒ la CAP nomme la grille (`notes` ou `Metadata.judgeRubric`) | `[AC_GRADER_UNKNOWN]` |
| 4 | `runs ≥ EvalRuns` ; `runs ≥ 5` si `Criticality: critical` | `[AC_RUNS_INSUFFICIENT]` |
| 5 | `dataset` pointe sous `workspace/datasets/golden/` (jamais `holdout/`) | `[AC_DATASET_IS_HOLDOUT]` |
| 6 | Chaque `BR-i` / `AC-i` de la MISSION apparaît dans ≥ 1 `## Covers` | `[TRACEABILITY_GAP]` |
| 7 | Chaque `Covers` référence un identifiant existant dans la MISSION | `[TRACEABILITY_DANGLING]` |
| 8 | `Parent MISSION hash` == hash courant de la MISSION | `[MISSION_HASH_MISMATCH]` |
| 9 | `Confidence` CAP ≤ `Confidence` MISSION | `[CONFIDENCE_ESCALATION]` |
| 10 | `## Allocated To` vide ou `<à déterminer>` (pas de topologie figée en PHASE 1) | `[CAP_PREMATURE_ALLOCATION]` |
| 11 | Nombre de CAPs ≤ `CapGranularityHardCap` sauf bypass | `[CAP_GRANULARITY_EXCEEDED]` |
| 12 | Aucun identifiant de framework — P11 | `[FRAMEWORK_LEAK_IN_CONTRACT]` |

| Exit | Verdict | Action |
|:-:|---|---|
| `0` | 🟢 G1 franchie | MISSION + CAPs → `Specified` ; → STEP 7 |
| `1` | 🔴 G1 rouge | STOP + ERROR ; les CAPs restent `Draft` |
| `3` | infra | `[INFRA_BLOCKED]` — STOP |

Format ERROR :
```
ERROR: /sdda-caps {n} — CAP GATE rouge
CAUSE: [CAP_GATE_FAILED] [AC_NOT_EVALUABLE] ×{a} (CAP {n}-{m} AC-{i}: "…"), [TRACEABILITY_GAP] ×{b} (BR-{j} non couvert) — rapport workspace/.sys/.validation/{n}-G1-cap.json
FIX: relancer /sdda-caps {n} (l'agent lit le rapport et corrige les AC listés) — ou éditer les CAPs à la main
```

**Aucun bypass pour G1.** Un AC non mesurable n'est pas un critère ; le laisser
passer rend toute la chaîne d'évaluation décorative.

**State tracking** : `set-phase --phase caps --status {pass|fail}
--payload-json '{"capCount":N,"critical":K}'`.

---

## STEP 7 — Recalcul d'état + récap

```bash
python .sdda/sdda.py compute-status --mission {n}
```

```
✅ MISSION {n}-{MissionName} — PHASE 1 terminée · G1 🟢

CAPs générées    : {C} fichiers dans workspace/caps/  ({K} critical → k=5)
AC mesurables    : {A} (graders : llm-judge {x} · exact {y} · trajectory {z} · …)
Datasets nommés  : {D} sous workspace/datasets/golden/  (à produire par qa-evals, PHASE 6)
Juges LLM à calibrer : {J} (invariant llm-judge-calibrated)
Couverture       : {B}/{B} BR · {S}/{S} AC système

Prochaine étape :
  - /sdda-topology {n}  pour décider la topologie et écrire les contrats (PHASE 2)
  - ou /sdda-full {n}   pour le pipeline complet
```

Si G1 rouge, ne rien ajouter.

---

## Règles de cette commande

- **Un seul agent** (`po-capabilities`), aucun spawn imbriqué.
- **Pas de Q/R utilisateur** après STEP 1 (l'agent est autonome).
- **Pas de modification de la MISSION parente** (owner : `po-elicitor`).
- **Pas de dataset créé** (owner : `qa-evals`), **pas de topologie**, **pas
  de code**.
- **1 fichier = 1 CAP** : la matrice d'ownership interdit un fichier agrégé.

---

## Chat Output Protocol

Applique `@.sdda/rules/output-protocol.md`. Label `[CAPS]`, plage `0-100%`.
Erreurs : bloc ERROR/CAUSE/FIX 3 lignes. Bypass verbeux : `SDDA_CHAT_VERBOSE=1`.
