---
name: sdda-roster
description: /sdda-roster — Le roster d'agents de l'ARCHITECTE : gabarit pré-rempli, puis vérification (P7, 0 token)
---
# /sdda-roster — Déclarer le roster, le vérifier

<!-- @llm-only-flags-file : tous les flags CLI de cette commande slash sont interprétés par Claude. -->

Le roster est **la** décision d'architecture agentic : combien d'agents,
lesquels, qui porte quelle CAP, avec quels outils, quelles skills, quelles
règles et quel tier. Cette commande ne la prend pas. Elle fait les deux choses
qu'un humain n'a pas à faire lui-même :

```
scaffold   écrire workspace/stack/topology/{n}-roster.yml PRÉ-REMPLI depuis la MISSION,
           les CAPs et le pattern actif de STACK.md — ce qui se DÉRIVE est écrit,
           ce qui se DÉCIDE reste `<à préciser>`
validate   vérifier que la déclaration est complète pour le pattern actif, que chaque
           CAP est portée par exactement un agent, que chaque subagent sert une CAP
           ou dit pourquoi il existe, qu'aucun trou ne reste, et qu'aucune API de
           framework n'y est nommée (P11)
```

**Aucun agent, aucun token.** Un roster écrit par un LLM serait l'architecture
que personne n'a décidée ; un roster vérifié par un LLM serait une opinion. Les
deux appartiennent à des scripts (`roster.py`), et la partie qui reste — remplir
les trous — appartient à l'architecte.

> **PHILOSOPHY P7 — le framework vérifie, il ne comble rien.** `<à préciser>`
> laissé dans un manifeste est refusé par `validate`, et `/sdda-topology` ne
> démarre pas tant que le roster est rouge. Le trou serait sinon comblé au
> moment de la génération par un modèle, sans que personne ne l'ait décidé ni
> relu.

**Usage :**
- `/sdda-roster {n}` — scaffold si le manifeste manque, puis validate
- `/sdda-roster {n} --validate` — vérification seule (exit 1 si rouge)
- `/sdda-roster {n} --force --reason "…"` — regénérer un manifeste existant
  (bypass **audité** : la déclaration en place est détruite)

---

## STEP 1 — Valider les arguments

`{n}` entier ≥ 1 **obligatoire**. `--validate`, `--force`, `--reason "…"`
optionnels ; `--force` sans `--reason` ni `SDDA_BYPASS_REASON` → le script
refuse (`[BYPASS_REASON_MISSING]`), rien n'est écrit.

Absent → demander `Quel est le numéro de la MISSION dont il faut déclarer le roster ? (ex. : 1)`.
Non numérique → ERROR :
```
ERROR: /sdda-roster — argument invalide
CAUSE: [INVALID_ARG] "{argument}" n'est pas un entier
FIX: relancer /sdda-roster {n} avec n entier (ex. /sdda-roster 1)
```

---

## STEP 2 — Pré-conditions (déterministes)

1. `workspace/stack/STACK.md` présent (`[STACK_MISSING]` → `python bootstrap.py`).
2. MISSION `{n}` unique (`[MISSION_NOT_FOUND]` / `[MISSION_AMBIGUOUS]`).
3. `## Active Orchestration Pattern` active **exactement une** fiche
   (`[STACK_MALFORMED]` sinon) : le pattern décide de ce que le roster doit
   contenir (`registry/architecture-requirements.yml`).
4. CAPs de la MISSION présentes. Sans CAP, le scaffold s'écrit quand même,
   `allocation:` vide et WARN `[ARCH_ROSTER_CAP_UNALLOCATED]` — mais c'est
   `/sdda-caps {n}` qu'il faut lancer d'abord : on n'alloue pas ce qui n'est
   pas découpé.

Le script vérifie les quatre ; la commande relaie ses blocs ERROR tels quels.

---

## STEP 3 — Scaffold (sauf `--validate`)

```bash
python .sdda/python/sdda_scripts/roster.py scaffold --mission {n} \
  $( [ "$FORCE" = "true" ] && echo --force --reason "$REASON" ) --json
```

| `data.action` | Sens | Ligne émise |
|---|---|---|
| `written` | manifeste créé | `manifeste écrit -> {path} ({k} <à préciser> à remplir)` |
| `kept` | manifeste déjà là, **rien réécrit** | `manifeste déjà présent -> {path} ({k} <à préciser> restant(s))` |
| `overwritten` | `--force` + raison : réécrit, bypass journalisé | `manifeste RÉÉCRIT -> {path} · bypass {audit}` |
| `refused` | `--force` sans raison | ERROR `[BYPASS_REASON_MISSING]`, STOP |

Ce que le scaffold **dérive** (et écrit) : `mission`, `pattern`, la liste des
CAPs dans `allocation:`, la présence ou l'absence des blocs `subagents:`,
`relations:`, `loop_bounds:`, `merge_strategy:` selon ce que le pattern exige.
Ce qu'il **ne décide pas** (et laisse `<à préciser>`) : les identifiants,
rôles, responsabilités, tiers, outils, skills, règles, les conditions des
relations, les bornes, la stratégie de fusion, et **quel agent porte quelle
CAP**.

Idempotent : un manifeste existant n'est jamais réécrit sans `--force`. Un
manifeste sans trou est une décision prise ; un manifeste à trous est une
décision en cours — ni l'un ni l'autre ne s'écrase en silence.

---

## STEP 4 — Validate

```bash
python .sdda/python/sdda_scripts/roster.py validate --mission {n} --json
```

| # | Contrôle | Classe si KO |
|---|---|---|
| 1 | Aucun `<à préciser>` (ni valeur entièrement entre chevrons) | `[ARCH_ROSTER_PLACEHOLDER]` |
| 2 | `mission:` = `{n}` ; `pattern:` = pattern actif de STACK.md | `[ARCH_ROSTER_INCOHERENT]` |
| 3 | Complétude pour le pattern actif — même code que G2 (`validate_architecture.check_requirements`) : subagents min/max, relations, repli, bornes, fusion, tier ou modèle, clé `tools:` explicite | `[ARCH_SPEC_INCOMPLETE]` · `[ROUTER_NO_FALLBACK]` · `[ARCH_*]` |
| 4 | Chaque CAP `{n}-*` portée par **exactement un** agent déclaré ; aucune CAP inconnue, aucun agent inconnu | `[ARCH_ROSTER_CAP_UNALLOCATED]` · `[ARCH_ROSTER_ALLOCATION_INVALID]` |
| 5 | Chaque subagent porte une CAP, ou a une `reason:` (une des 5 raisons closes de P7) | `[ARCH_ROSTER_AGENT_IDLE]` |
| 6 | Aucun identifiant de framework (P11) | `[FRAMEWORK_LEAK_IN_CONTRACT]` |

| Exit | Action |
|:-:|---|
| `0` | 🟢 — récap, STEP 5 |
| `1` | 🔴 — relayer les findings (`fichier:$.chemin`), STOP |

**Aucun rapport de gate n'est écrit.** `roster` n'est pas une part connue de G2
(`gate_reports.GATE_PARTS_ADVISORY`) : un rapport que la machine à états ignore
serait un faux vert qui attend. C'est `/sdda-topology` qui rejoue ces contrôles
au sein de G2, sur la même source.

```
ERROR: /sdda-roster {n} — roster incomplet
CAUSE: [ARCH_ROSTER_PLACEHOLDER] `$.subagents[0].id` est encore `<à préciser>` ; [ARCH_ROSTER_CAP_UNALLOCATED] CAP `{n}-2-{Name}` n'est portée par aucun agent
FIX: l'architecte tranche dans workspace/stack/topology/{n}-roster.yml — le framework ne comble aucun trou (P7) ; puis /sdda-roster {n} --validate
```

---

## STEP 5 — Récap

```
{✅|🔴} /sdda-roster {n}-{MissionName} — roster {validé|à compléter}

Manifeste        : workspace/stack/topology/{n}-roster.yml ({written|kept|overwritten})
Pattern actif    : {pattern} (STACK.md ## Active Orchestration Pattern)
Roster           : orchestrateur `{id}` · {S} subagent(s) · {R} relation(s) · {L} borne(s)
Allocation       : {A}/{C} CAPs portées   {✅ | 🔴 non allouées : {liste}}
Trous            : {k} `<à préciser>`     {✅ | 🔴 → l'architecte tranche}

Prochaine étape :
  - {k > 0 → éditer le manifeste, puis /sdda-roster {n} --validate}
  - {🟢 → /sdda-topology {n} : architect-topology matérialise CE roster — il n'en change aucune ligne}
```

---

## Règles de cette commande

- **Aucun agent invoqué.** Deux scripts déterministes, 0 token. Un roster
  généré par un LLM n'est pas un roster déclaré.
- **Le framework ne comble aucun trou** (P7). Il dérive, il vérifie, il
  s'arrête.
- **Jamais d'écrasement silencieux** : `--force` exige une raison et laisse
  une ligne dans `workspace/.sys/.audit/bypasses.jsonl` (R5).
- **Aucun rapport de gate.** Le verdict vit dans le code de sortie et le JSON ;
  G2 le rejoue.
- **Zone de l'humain** : `workspace/stack/` (ownership.md). Aucun agent du
  pipeline n'y écrit ; `architect-topology` la **lit** et la matérialise.

---

## Chat Output Protocol

Applique `@.sdda/rules/output-protocol.md`. Label `[ROSTER]`, plage `0-100%`.
Sortie 1 passe. Erreurs : bloc ERROR/CAUSE/FIX 3 lignes.
