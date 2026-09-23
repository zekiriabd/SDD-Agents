---
# Règle path-scoped : injectée au contact des artefacts arbitrés ci-dessous.
# Portée volontairement étroite — la matrice n'intéresse que ceux qui écrivent.
paths:
  - "workspace/feats/missions/**"
  - "workspace/feats/caps/**"
  - "workspace/feats/topology/**"
  - "workspace/feats/contracts/**"
  - "workspace/src/{App}/prompts/**"
  - "workspace/proof/datasets/**"
  - "workspace/proof/**"
  - "workspace/src/**"
  - "workspace/.sys/.context/**"
---

# Règle — Ownership

## Principe

SDD_Agents lance des agents **en parallèle** (phases 2, 3, 4, 6, 7B). Pour
éviter les conflits d'écriture, chaque fichier partagé a **un propriétaire
unique** ou un **mode d'écriture sérialisé**.

Load-bearing : une violation produit des écrasements silencieux, des fichiers
corrompus et des résultats non déterministes — c'est-à-dire un pipeline dont on
ne peut plus rien conclure.

---

## 1. La matrice

| Chemin | Owner exclusif | Mode | Phase |
|---|---|---|---|
| `workspace/stack/STACK.md` | **humain (Tech Lead)** | édition manuelle — aucun agent n'écrit ; versionné, noms de variables seulement | — |
| `workspace/src/{App}/.env` | **humain** | les valeurs des secrets du RUNTIME — gitignoré, avec l'application qui les consomme ; lu par aucun agent, le harnais de construction compris | — |
| `workspace/feats/briefs/*.md` | **humain** | ce qu'il dépose : specs, matière d'une MISSION | — |
| `workspace/feats/topology/{n}-roster.md` | **humain (architecte)** | la décision d'architecture (P7) — `architect-topology` la lit, ne l'écrit jamais | — |
| `workspace/proof/seed/**` | **humain** | la vérité terrain — `qa-evals` la lit et en dérive `datasets/`, jamais l'inverse | — |
| `workspace/feats/missions/{n}-*.md` | `po-elicitor` | Create puis append-only | 0 |
| `workspace/feats/caps/{n}-{m}-*.md` | `po-capabilities` | Create exclusif (1 fichier = 1 CAP) | 1 |
| `workspace/feats/caps/**` champ `Allocated To` | `architect-topology` | **Edit narrow** (ce champ seul) | 2 |
| `workspace/feats/topology/{n}-topology.md` | `architect-topology` | Create exclusif — graphe Mermaid inclus (§4) ; **pas** `{n}-roster.md`, même répertoire, autre owner | 2 |
| `workspace/feats/contracts/agents/*` | `architect-topology` (squelette) → `dev-prompt` (§Prompt: ref + hash) | Sérialisé par section | 2, 4 |
| `workspace/feats/contracts/tools/{n}-*.tool.md` | `architect-tools` | Create exclusif | 2 |
| `workspace/feats/contracts/tools/{n}-data-*.tool.md` | `architect-data` | Create exclusif — **préfixe `data-` réservé**, namespace disjoint | 2 |
| `workspace/feats/contracts/retrieval/*` | `architect-rag` | Create exclusif | 2 |
| `workspace/feats/contracts/memory/*` | `architect-memory` | Create exclusif | 2 |
| `workspace/.sys/.ir/*.ir.json` | **script `ir_compiler.py` uniquement** | régénéré, jamais édité | 2.9 |
| `workspace/src/{App}/prompts/{agent}.system.md` | `dev-prompt` | Create + Edit exclusif — l'EXÉCUTABLE hashé, dans l'application | 4 |
| `workspace/src/{App}/skills/{skill}.md` | `dev-prompt` | Create + Edit exclusif — un fragment par compétence déclarée au roster ; la matière du prompt, cité sous `## Compétences` | 4 |
| `workspace/src/{App}/rules/{rule}.md` | `dev-prompt` | Create + Edit exclusif — un fragment par règle déclarée au roster ; cité sous `## Règles` | 4 |
| `workspace/src/{App}/memory/**` | `dev-orchestration` | Create + Edit exclusif — l'implémentation du contrat de `architect-memory` ; une mémoire est un état qui survit au tour | 5 |
| `workspace/src/**/tools/**` | `dev-tools` | Edit-augment exclusif | 3 |
| `workspace/src/**/retrieval/**` | `dev-retrieval` | Edit-augment exclusif | 3 |
| `workspace/src/**/data/**` | `dev-data` | Edit-augment exclusif | 3 |
| `workspace/src/**/agents/{agent}/**` | `dev-agent` (1 instance par agent) | Edit-augment exclusif, répertoires disjoints | 4 |
| `workspace/src/**/orchestration/**` | `dev-orchestration` | Create + Edit exclusif | 5 |
| `workspace/src/**/serving/**` | `dev-api` | Edit-augment exclusif | 5 |
| `workspace/src/{App}/*` (fichiers de projet, README, Dockerfile, .env.example) | `dev-backend` | Create + Edit — la coquille ; les fichiers générés par script se régénèrent, ne s'éditent pas | 3, 5 |
| `workspace/src/**/app/**` (composition, config, Domaine) | `dev-backend` | Create + Edit exclusif — **rien du moteur** : aucun droit sur agents/, tools/, orchestration/, retrieval/, data/, serving/ | 3, 5 |
| `workspace/src/tests/**` (transverses) | `qa-tests` | Create/Edit exclusif — **jamais le code de production** | 6 |
| `workspace/src/**/{couche}/tests/**` | le `dev-*` de la couche | Create/Edit — ses propres tests de contrat | 3-5 |
| `workspace/proof/datasets/**` | `qa-evals` | Create exclusif | 6 |
| `workspace/proof/suites/**` | `qa-evals` | Create exclusif | 6 |
| `workspace/proof/baselines/**` | **script déterministe uniquement** | write atomique + lock | 6, 9 |
| `workspace/.sys/reports/**` | `eval_runner.py` | Create (horodaté) | 6 |
| `workspace/proof/calibration/**` | `qa-evals` + **humain** (les labels) | Sérialisé | 6 |
| `workspace/.sys/traces/**` | runtime + hooks | Append-only | tout |

> **Le `**` de `workspace/src/**/{couche}/**` compte.** La profondeur du chemin
> applicatif est fixée par la **fiche de langage**, pas par cette matrice : un
> projet Python en `uv --lib` produit
> `workspace/src/{AppName}/data/`, un autre écosystème produira
> autre chose. La matrice dit **qui possède quoi**, jamais où l'écosystème range
> ses paquets. Le `**` doit donc matcher **zéro segment ou plus** — un enforcer
> qui exigerait au moins un segment déclarerait hors zone toutes les écritures
> d'un projet à arborescence plate, et un enforcer qui dit l'inverse de la
> vérité est pire qu'aucun enforcer.
| `workspace/.sys/.context/constitution.md` | **séquentiel** : `po-elicitor` (§1-§3) → `po-capabilities` (§3 acteurs) → `architect-topology` (§4 architecture) | Append-only par section | 0, 1, 2 |
| `workspace/feats/decisions/ADR-*.md` | `architect-topology`, `architect-data` | Numérotation atomique par horodatage | 2 |
| `workspace/.sys/.validation/**` | scripts de gate | Create exclusif | tout |
| `workspace/.sys/.audit/**` | hooks framework | Append-only | tout |

---

## 2. Les trois barrières non négociables

Elles ne sont pas du confort de parallélisme : ce sont des garde-fous
épistémiques. Sans elles, le pipeline se félicite lui-même.

### 2.1 Seul `qa-evals` écrit dans `workspace/proof/datasets/`

**L'agent qui écrit le code ne peut pas modifier le jeu qui le juge.**

C'est la barrière la plus importante du framework. Dans un pipeline classique,
un agent qui truque ses tests est un risque ; ici, c'est le **résultat par
défaut** — parce que la façon la plus simple de faire passer une eval est
d'ajuster son dataset, et qu'un LLM optimise pour le vert qu'on lui demande.

Violation → `[DATASET_OWNERSHIP_VIOLATION]`, bloquant, sans bypass.

**Aucune exception, pas même pour l'adversarial.** `review-adversarial`
découvre des attaques réussies qui doivent devenir des items permanents — mais
il ne les écrit pas lui-même. Il dépose ses trouvailles dans
`workspace/.sys/.validation/adversarial-findings/{n}.jsonl`, et un **script
déterministe** les promeut dans `workspace/proof/datasets/adversarial/`.

Le détour coûte une étape et préserve un invariant sans trou : « seul
`qa-evals` écrit dans `datasets/` » se vérifie mécaniquement, alors qu'une
règle avec une exception se vérifie au cas par cas — c'est-à-dire mal.

### 2.2 Aucun `dev-*` n'écrit dans `workspace/src/{App}/prompts/`

**L'agent qui implémente ne réécrit pas la spécification qu'il implémente.**

Si `dev-agent` peut éditer le prompt, alors l'écart entre le contrat et le code
se résout silencieusement en faveur du code, et `review-spec`
compare le code à un prompt qui a été aligné sur lui.

Violation → `[PROMPT_OWNERSHIP_VIOLATION]`, bloquant, sans bypass.

#### Le cas des skills — deux owners, une symétrie

Une **skill** traverse deux fichiers déjà possédés, et c'est voulu :

| Où | Quoi | Owner |
|---|---|---|
| `contracts/agents/*` §5 | la **déclaration** — reprise du roster de l'architecte | `architect-topology` |
| `prompts/{agent}.system.md` `## Compétences` | l'**implémentation** — le comportement nommé | `dev-prompt` |

Aucun des deux ne peut écrire chez l'autre, donc aucun ne peut résoudre seul un
désaccord entre déclaration et implémentation. `lint_prompts.py` le constate
dans les deux sens : `[SKILL_NOT_IMPLEMENTED]` si le prompt ne porte pas une
skill déclarée, `[SKILL_UNDECLARED]` si le prompt en porte une que personne n'a
déclarée.

C'est la seule vérification possible : une skill n'a ni schéma, ni auth, ni
effet de bord — aucune gate ne peut l'exécuter pour la juger. Sans cette
symétrie, une skill déclarée au roster disparaissait silencieusement à la
compilation de l'IR, et personne ne l'implémentait jamais.

### 2.3 Aucun agent n'écrit dans `workspace/proof/baselines/`

Une baseline se déplace par une **action explicite et tracée**
(`BaselinePromotionPolicy: explicit`), jamais par écrasement. Sinon la dérive
lente devient invisible : chaque run repart de son propre résultat et le système
descend sans que rien ne l'annonce.

Violation → `[BASELINE_OWNERSHIP_VIOLATION]`, bloquant.

---

## 3. Modes d'écriture

- **Create exclusif** — l'owner crée le fichier ; personne d'autre n'y touche.
  Une re-création préserve les édits humains (`PreserveHumanEdits: true`,
  détectés par empreinte du contenu généré).
- **Edit-augment exclusif** — l'owner peut modifier le fichier existant ; les
  autres agents le lisent au plus.
- **Edit narrow** — l'agent ne modifie qu'une section ou un champ nommé. Toute
  écriture hors de ce périmètre est une violation.
- **Append-only par section** — plusieurs agents écrivent dans le même fichier,
  chacun dans sa section, dans un ordre imposé. Jamais de réécriture globale.
- **Write atomique + lock** — écriture dans un temporaire puis `rename`, sous
  verrou `O_EXCL` avec TTL. Réservé aux fichiers que la console et le pipeline
  peuvent toucher en même temps.

---

## 4. Parallélisme sûr

Deux instances de `dev-agent` tournent en parallèle sur
`workspace/src/**/agents/{agent-a}/` et `workspace/src/**/agents/{agent-b}/` :
répertoires **disjoints**, aucun fichier partagé en écriture.

Ce qui est partagé — les schémas communs, les types, la configuration — est
créé **avant** la phase parallèle, par `dev-orchestration` en pré-passe, puis
gelé en lecture seule (**first-write wins + lock**). Un `dev-agent` qui a besoin
d'un type absent ne le crée pas : il le signale.

```
ERROR: agent dev-agent — type partagé manquant
CAUSE: [SHARED_TYPE_MISSING] `BillingAnswer` absent de src/shared/, requis par
       le contrat de 1-billing-specialist
FIX: relancer la pré-passe dev-orchestration, ou compléter l'IR
```

---

## 5. Constitution — sérialisation

`workspace/.sys/.context/constitution.md` est la SSoT cross-MISSION (acteurs,
glossaire, décisions d'architecture transverses). Ordre d'écriture imposé :

| Phase | Agent | Sections |
|:---:|---|---|
| 0 | `po-elicitor` | §1 date · §2 glossaire · §3 acteurs (bootstrap) |
| 1 | `po-capabilities` | §3 acteurs (append) |
| 2 | `architect-topology` | §4 architecture · §5 index des ADRs |

**Append-only** : aucun agent ne supprime ni ne réécrit ce qu'un autre a posé.
Un terme de glossaire contredit se signale, il ne s'écrase pas.

---

## 6. Ce que les agents ne touchent jamais

| Chemin | Pourquoi |
|---|---|
| `workspace/stack/STACK.md` | c'est la décision de l'opérateur. Un agent qui édite la config qui le contraint n'est plus contraint. |
| `workspace/proof/calibration/*.labels.json` | les labels sont **humains**. Un juge calibré contre des labels générés par un LLM n'est pas calibré. |
| `workspace/.sys/traces/**` en écriture manuelle | les traces sont des faits émis par le runtime. |
| `.sdda/**` | le framework ne se modifie pas lui-même pendant un run. |
