---
name: sdda-bootstrap
description: /sdda-bootstrap — Initialisation d'un projet SDD_Agents (STACK.md + workspace/ + smoke)
---
<!-- GÉNÉRÉ par sdda_admin/harness_build.py depuis .sdda/commands/sdda-bootstrap.md.
     NE PAS ÉDITER ICI : toute modification est écrasée au build suivant,
     et le test de parité la signale. Éditer la source. -->

# /sdda-bootstrap — Initialisation d'un projet SDD_Agents

<!-- @llm-only-flags-file : tous les flags CLI de cette commande slash sont interprétés par Claude. -->

Prépare un dépôt à recevoir le pipeline SDD_Agents : génère
`workspace/stack/STACK.md` depuis `.sdda/templates/STACK.md.template`, crée
l'arborescence `workspace/` (missions, caps, topology, contracts, prompts,
datasets, evals, traces, src, .sys) et exécute un smoke check déterministe.

**Usage :** `/sdda-bootstrap [--combo c1|custom] [--dry-run] [--skip-install] [--force]`

**Quand l'utiliser :**
- **Greenfield** : dépôt cloné depuis le template SDD_Agents, aucun
  `workspace/stack/STACK.md` encore rendu.
- **Re-init** : dépôt déjà initialisé mais on repart de zéro (`--force`, après
  confirmation).

**Quand NE PAS l'utiliser :**
- Base de code agentic **existante** (LangChain, LangGraph, Semantic Kernel,
  CrewAI, n8n, Dify…). SDD_Agents construit, il ne rétro-documente pas : un
  système existant se reprend par une MISSION écrite à la main, comme un projet
  neuf dont on connaîtrait déjà les contraintes. `/sdda-bootstrap` ne lit jamais
  de code.

---

## STEP 1 — Pré-checks (déterministes, 0 token)

1. `bootstrap.py` existe à la racine du dépôt.
2. `.sdda/templates/STACK.md.template` existe.
3. Python 3.12+ disponible (`python --version`).
4. `.sdda/INVARIANTS.yml` présent et parsable.

Si l'un échoue → STOP + ERROR :
```
ERROR: /sdda-bootstrap — prérequis manquant
CAUSE: [INFRA_BLOCKED] {détail : fichier absent | version Python < 3.12 | INVARIANTS.yml illisible}
FIX: recloner le dépôt depuis le template SDD_Agents (intact), vérifier Python 3.12+
```

---

## STEP 2 — Détection de l'état du projet

Glob `workspace/feats/missions/*.md` et test `workspace/stack/STACK.md` :

| État | Conditions | Action |
|---|---|---|
| **greenfield** | aucun `STACK.md`, aucune MISSION | informer, proposer `python bootstrap.py` |
| **partial** | `STACK.md` absent mais MISSIONs présentes | WARN incohérence — proposer `--force` |
| **initialisé** | `STACK.md` ≥ 100 octets ET aucun `{{` résiduel | informer « déjà initialisé », proposer `/sdda-mission` ou `--force` |
| **template brut** | `STACK.md` contient des `{{Placeholder}}` | informer « template non rendu », proposer `python bootstrap.py --force` |

---

## STEP 3 — Sortie utilisateur (1 bloc par cas)

**Cas greenfield :**
```
[BOOTSTRAP] Projet vierge détecté. Lancer dans un terminal :
    python bootstrap.py [--combo c1|custom]

  Combo sous engagement SLA :
    c1 = Python 3.12 + LangChain/LangGraph + router/sequential + RAG hybrid
         + pgvector + view-per-agent + MCP + CLI/FastAPI-SSE + pytest-eval + OTel-GenAI
    custom = choix libre — marqué `experimental` tant que non mesuré

  Options :
    --combo c1|custom   préset de stack (saute les questions)
    --dry-run           affiche les actions sans écrire
    --skip-install      passe l'installation des dépendances (CI)
    --force             écrase workspace/ existant

  Après bootstrap : /sdda-mission {Name} puis /sdda-full 1
```

**Cas initialisé :**
```
[BOOTSTRAP] Projet déjà initialisé (workspace/stack/STACK.md présent).
Prochaine étape : /sdda-mission {Name}
Pour repartir de zéro : python bootstrap.py --force
```

**Cas template brut :**
```
[BOOTSTRAP] STACK.md contient des placeholders {{...}} non substitués.
Lancer : python bootstrap.py --force
(le template a été copié tel quel — c'est bootstrap.py qui fait la substitution)
```

**Cas partial :**
```
🟡 [BOOTSTRAP/WARN] {M} MISSION(s) dans workspace/feats/missions/ mais aucun STACK.md.
Toutes les commandes du pipeline émettront [STACK_MISSING].
Lancer : python bootstrap.py (sans --force : les MISSIONs sont conservées)
```

---

## STEP 4 — Ce que `bootstrap.py` produit (référence)

Écrit par le script, **jamais** par cette commande :

```
workspace/
├── stack/                         # CE QU'ON CONFIGURE
│   ├── STACK.md                   #   gitignored — secrets en clair
│   └── sources/  topology/        #   manifestes versionnés
├── feats/                         # CE QU'ON SPÉCIFIE
│   ├── missions/  caps/  topology/
│   ├── contracts/{agents,tools,retrieval,memory,dataaccess/schemas}/
│   ├── decisions/                 #   ADR — un seul endroit
│   └── briefs/
├── src/                           # CE QU'ON PRODUIT — prompts compris
│   └── prompts/
├── proof/                         # CE QUI JUGE — jamais écrit par un dev-*
│   ├── datasets/{golden,holdout,calibration,adversarial}/
│   └── suites/  baselines/  calibration/
├── .sys/                          # ÉTAT INTERNE ET SORTIES DE RUN — régénérable
│   ├── .ir/  .context/  .state/  .validation/  .audit/
│   ├── reports/  traces/runs/
│   └── workspace.json             #   workspaceVersion — ce que migrate_workspace.py fait monter
```

La SSoT de cet arbre est `sdda_scripts.smoke_check.WORKSPACE_TREE` ; ce dessin
n'en est que la lecture. Le sens des cinq entrées : `ARCHITECTURE.md §2.ter`.

Les trois blocs de `STACK.md` qui doivent être **vérifiés par l'humain** après
rendu — les confondre rend le budget d'exécution incalculable
(ARCHITECTURE.md §6) :

| Bloc | Répond à |
|---|---|
| `## Active Harness` | où tourne l'orchestration de construction |
| `## Build Models` | quels modèles paient les 22 Developer Agents |
| `## Runtime Models` | quels modèles fait tourner l'application générée |

`## Project Config` porte les clés lues par toutes les commandes :
`MaxParallel`, `EvalRuns`, `EvalVarianceWarnPct`, `CostPerRunTargetUsd`,
`CostPerRunHardCapUsd`, `LatencyP95TargetMs`, `TokenCeilingPerRun`,
`MaxCostPerRun`, `*Mode`, `*FailOn`.

---

## STEP 5 — Smoke check (exécuté par `bootstrap.py`, rappelé ici)

```bash
python .sdda/sdda.py smoke-check
```

Vérifie : `STACK.md` parsable et sans `{{`, exactement 1 stack active par
catégorie obligatoire (`lang`, `framework`, `orchestration`, `serving`),
`rag`/`dataaccess`/`memory` = `none` ou 1 stack, chaque enforcer listé dans
`INVARIANTS.yml` présent sur disque, `MaxParallel ≥ 1`.

| Exit | Sens |
|:-:|---|
| `0` | projet prêt — `/sdda-mission` peut démarrer |
| `1` | `[STACK_MALFORMED]` — clé manquante ou stack ambiguë, détail sur stderr |
| `2` | `[INVARIANT_ENFORCER_MISSING]` — un enforcer déclaré n'existe pas (doc-theater détecté) |

Un workspace amorcé par un framework plus ancien sort `[WORKSPACE_VERSION_MISSING]`
ou `[WORKSPACE_VERSION_OUTDATED]` : le faire monter avec
`python .sdda/sdda.py migrate-workspace` (`--dry-run` d'abord ;
un répertoire retiré de l'arborescence mais non vide est conservé et signalé
`[WORKSPACE_GHOST_DIR_NOT_EMPTY]`).

---

## Règles de cette commande

- **Ne lance jamais** `bootstrap.py` elle-même : le script est interactif
  (`input()`) et exige le terminal de l'utilisateur, pas un sub-agent.
- **Ne touche jamais** `workspace/` ni `STACK.md`. Lecture seule stricte.
- **Aucun agent invoqué.** 0 token hors le rendu du message.
- **Pré-requis de** : `/sdda-mission`, `/sdda-caps`, `/sdda-topology`,
  `/sdda-build`, `/sdda-eval`, `/sdda-review`, `/sdda-full`, `/sdda-status`,
  `/sdda-help` — toutes émettent `[STACK_MISSING]` ou `[STACK_MALFORMED]` si
  `bootstrap.py` n'a pas tourné.

---

## Chat Output Protocol

Applique `@.sdda/rules/output-protocol.md`. Label `[BOOTSTRAP]`. Sortie en
1 passe, 1 bloc. Erreurs : bloc ERROR/CAUSE/FIX 3 lignes.
