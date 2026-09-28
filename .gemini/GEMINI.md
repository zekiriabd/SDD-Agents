<!-- GÉNÉRÉ par sdda_admin/harness_build.py depuis .sdda/memory/core.md.
     NE PAS ÉDITER ICI : toute modification est écrasée au build suivant,
     et le test de parité la signale. Éditer la source. -->

# SDD_Agents — carte opératoire

Ce fichier est chargé par le harnais dans CHAQUE session et CHAQUE sous-agent :
il est donc repayé à chaque tour de chacun. Il ne porte que ce qu'une action
exige ; le reste se lit **à la demande**, jamais par défaut :

| Besoin | Où lire |
|---|---|
| architecture complète, raisons des choix | `.sdda/ARCHITECTURE.fr.md` (§ utile seulement) |
| principes P1–P12 | `.sdda/PHILOSOPHY.fr.md` |
| qui lit / écrit quoi, budgets de contexte | `.sdda/loader.yml` (ton entrée seulement) |
| invariants porteurs et leur enforcer | `.sdda/INVARIANTS.yml` |
| taxonomie d'erreurs | ta tranche `.sdda/digests/error-classification.{agent}.md` |

## 1. Trois couches

- **Framework** `.sdda/` — source neutre : agents, commandes, règles, stacks,
  templates, registres, outillage Python 0 token.
- **Façades** `.claude/`, `.codex/`, `.gemini/`, `.agents/`, `AGENTS.md`,
  `GEMINI.md` — **générées** par `python .sdda/sdda.py harness-build` ; ne
  jamais les éditer à la main.
- **Workspace** `workspace/` — le projet de l'utilisateur.

## 2. Workspace : ce que l'humain fournit, ce que le framework produit

- Humain : `stack/STACK.md` (choix techniques, **noms** de variables, jamais de
  valeur) · `feats/` (brief `{n}-{Name}.md`, roster `{n}-roster.md` — Markdown
  seul) · `assets/` (données + `.env`) · `seed/` (vérité terrain).
- Pipeline : `pipeline/` — missions, caps, topology, contracts, decisions (ADR),
  datasets, suites, baselines, calibration, fixtures.
- Application : `src/{App}/` — layout plat ; prompts, skills, rules **dans**
  l'application. Son `CLAUDE.md` (écrit par `project-init`, jamais à la main)
  remplace STACK.md pour les `dev-*` ; les autres agents reçoivent STACK.md
  tranché dans leur pack (`.sys/.context/packs/{agent}.md`).
- État et sorties de run : `.sys/` — IR (`.ir/{n}-system.ir.json` et ses vues
  par agent `.ir/views/`), rapports de gate (`.validation/`), traces.

## 3. Pipeline et gates

`/sdda-full {n}` enchaîne : mission (G0) → caps (G1) → roster vérifié →
topologie + contrats + IR (G2) → socle outils/RAG/data (G3, G4) → prompts +
agents (G5) → orchestration + surface (G6) → eval → revue (G7) → acceptation
sur holdout (G8). Chaque gate est un script déterministe qui écrit son rapport ;
un verdict se **lit** dans `.sys/.validation/`, il ne se déclare pas.
`Profile: poc` remplace les phases 3→5 par `dev-prompt ∥ qa-evals` puis un seul
`dev-app`, gates rapportées sans bloquer.

## 4. Règles sans exception

1. **Frontière du jugement** : aucun `dev-*` n'écrit sous
   `pipeline/datasets|suites|baselines|calibration|fixtures/`, ni
   `src/{App}/prompts|skills|rules/` (réservés à `qa-evals` et `dev-prompt`).
2. **Aucun agent ne spawne un agent** : l'orchestration appartient aux commandes.
3. **Ownership** : un agent n'écrit que ses `writes:` de `loader.yml` ;
   `dev-agent` = une instance par agent, liée à son répertoire
   (`SDDA-INSTANCE: {agent}` en première ligne du prompt).
4. **Secrets** : aucun agent ne lit `.env` (`[SECRET_READ_FORBIDDEN]`) ; STACK.md
   et les contrats ne portent que des noms de variables.
5. **Rapports de gate infalsifiables** : aucun `.json` sous `.sys/.validation/`
   par Write/Edit ou shell — seuls les scripts les écrivent.
6. **Faits vs hypothèses** : un chiffre produit par script peut devenir un
   critère d'acceptation ; une sortie d'agent LLM jamais sans mesure.
7. **Tiers, pas de modèles** : un agent déclare `fast|balanced|deep`. Build
   models (`capability-matrix.yml`) ≠ runtime models (`## Runtime Models`).
8. **Prompts hashés, chargés par chemin** : aucun prompt inline dans le code.
9. **Holdout disjoint du golden**, vérifié par hash ; on n'itère jamais dessus.
10. **Bornes en code** : `maxIterations`, `maxToolCalls`, `maxDelegationDepth`,
    `timeoutSec`, `budgetUsd` et leur `onBoundExceeded`.

## 5. Outillage

Toute sous-commande déterministe : `python .sdda/sdda.py {cmd}` (aucune
installation requise). Un script absent ou en échec s'arrête et se rapporte —
jamais une sortie inventée. Erreur = bloc ERROR/CAUSE `[CLASS]`/FIX de trois
lignes, classe prise dans ta tranche du registre.

## 6. Développer le framework lui-même

- Éditer `.sdda/` seulement, puis régénérer : `sync-digests`,
  `sync-error-registry`, `sync-counters`, `harness-build`, et vérifier par
  `smoke-check` et `pytest .sdda/python/tests`.
- Ce fichier est `.sdda/memory/core.md`. Le garder court : son plafond est
  vérifié par `smoke-check` (`cost.static`) — un ajout qui ne sert pas à chaque
  tour va dans `ARCHITECTURE.fr.md` ou une règle.
- Des sous-agents qui éditent le framework : `SDDA_FRAMEWORK_DEV=1`.
