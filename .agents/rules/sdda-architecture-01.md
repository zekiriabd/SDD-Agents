---
trigger: model_decision
description: "Architecture SDD_Agents, partie 1/4 (1. Les trois couches) — lire avant toute action du pipeline SDD_Agents"
---
<!-- GÉNÉRÉ par sdda_admin/harness_build.py depuis .sdda/ARCHITECTURE.fr.md.
     NE PAS ÉDITER ICI : toute modification est écrasée au build suivant,
     et le test de parité la signale. Éditer la source. -->

# SDD_Agents — Architecture

Document de référence : arborescence, couches, pipeline, gates, abstraction
harness/provider. Découle de [PHILOSOPHY.fr.md](PHILOSOPHY.fr.md).

> Page de référence en anglais : [ARCHITECTURE.md](ARCHITECTURE.md). Ce jumeau
> français porte le même contenu technique, et c'est **lui** que
> `harness_build.py` compile dans le fichier mémoire des harnais
> (`.claude/CLAUDE.md`, `.codex/AGENTS.md`, `.gemini/GEMINI.md`) : les Developer
> Agents lisent des prompts français, leur servir l'architecture dans une autre
> langue ferait deux vocabulaires pour une même règle. S'il manque, le build
> compile l'anglais et le dit.

---

## 1. Les trois couches

Identique en esprit à SDD_Pro, adapté à l'agentic.

| Couche | Emplacement | Nature | Qui la lit |
|---|---|---|---|
| **Framework** | `.sdda/` | Source neutre : agents, commandes, règles, stacks, templates, invariants | Compilée vers les façades harness |
| **Façades harness** | `.claude/`, `.codex/`, `.gemini/`, `.agents/` | Générées depuis `.sdda/` par `harness_build.py` | Le harnais actif |
| **Workspace** | `workspace/` | Le projet de l'utilisateur : spécifications, contrats, prompts, datasets, code généré | Les agents, au runtime |

> `.sdda/` (et non `.sdd/`) : nom court volontaire — il est référencé des centaines
> de fois dans 24 prompts d'agents ; deux caractères de moins sont des tokens
> économisés à chaque invocation. Distinct de `.sdd/` pour permettre de vendorer
> SDD_Pro et SDD_Agents dans un même dépôt.

---

## 2. Arborescence

```
SDD-Agents/
├── README.md                          # anglais par défaut ; jumeau README.fr.md
├── README.fr.md                       #   (convention : .sdda/docs/README.md)
├── CHANGELOG.md                       # Keep a Changelog ; une release = un tag v*
├── LICENSE                            # MIT — couvre .sdda/ et le paquet sdda
├── AGENTS.md · GEMINI.md              # GÉNÉRÉS : pointeurs vers .codex/ et .gemini/,
│                                      #   que Codex CLI et Gemini CLI lisent à la racine
├── bootstrap.py                       # interactif : STACK.md + workspace + smoke
├── plugin.json                        # 🟡 planifié — découverte marketplace (comme SDD_Pro)
├── .github/
│   ├── workflows/ci.yml               # smoke, --check des générateurs, pytest Linux+Windows,
│   │                                  #   lint (ruff + mypy), couverture avec plancher
│   ├── workflows/release.yml          # tag v* -> wheel + sdist ; actions épinglées par SHA
│   └── dependabot.yml
│
├── .sdda/                             # ── FRAMEWORK (source neutre) ──────────
│   ├── PHILOSOPHY.md · PHILOSOPHY.fr.md
│   ├── ARCHITECTURE.md · ARCHITECTURE.fr.md   # le .fr.md est la source des fichiers mémoire
│   ├── INVARIANTS.yml                 # contrats porteurs + enforcer sur disque (compte : sync_counters)
│   ├── config.base.yml                # couche 1/3 du Project Config
│   ├── loader.yml                     # reads/writes/forbidden_reads + budget + cache par agent
│   ├── agent-bounds.yaml              # tier_default / floor / ceiling par agent
│   ├── capability-matrix.yml          # harnais x mécanismes ; tier -> modèle de CONSTRUCTION
│   ├── agents/                        # 24 Developer Agents (cf. docs/AGENT-ROSTER.md)
│   ├── commands/                      # 11 commandes slash
│   ├── rules/                         # règles opérationnelles
│   │   ├── ownership.md               # matrice d'écriture (hérité SDD_Pro)
│   │   ├── output-protocol.md
│   │   ├── error-classification.md    # taxonomie [CLASS] agentic
│   │   ├── eval-protocol.md           # k-runs, variance, calibration, baselines
│   │   ├── prompt-authoring.md        # comment un contrat devient un prompt
│   │   ├── agent-safety.md            # injection, scopes, effets de bord
│   │   └── budget-and-loop.md         # bornes, coût, escalade
│   ├── skills/                        # 🟡 planifié — skills auto-déclenchées des
│   │                                  #   Developer Agents. À ne pas confondre avec
│   │                                  #   les skills des agents du PRODUIT, qui
│   │                                  #   vivent au §5 des contrats et dans les
│   │                                  #   prompts (cf. §7, rules/ownership.md §2.2)
│   ├── providers/                     # anthropic · openai · google · azure-openai · local-ollama
│   │                                  #   tarifs, URL, variable de clé — lus par pricing et le juge
│   ├── stacks/                        # ── LE CATALOGUE — 67 fiches sur disque ─
│   │   │                    # Chaque fiche déclare `Languages:` (un langage,
│   │   │                    # plusieurs, ou `*` si elle n'en suppose aucun).
│   │   │                    # C'est la SSoT du couplage : preflight_stack_combo
│   │   │                    # refuse une fiche d'un autre runtime que le
│   │   │                    # langage actif -> [STACK_LANGUAGE_MISMATCH].
│   │   ├── lang/            python.md · csharp.md · typescript.md · kotlin.md · java.md
│   │   │                    # une combo de bootstrap C1 par langage : C1 · C1-NET · C1-TS
│   │   │                    # · C1-KT · C1-JAVA (RAG hybride pgvector jusqu'au bout)
│   │   ├── archi/           mvc.md · ddd.md · microservice.md     [*]
│   │   │                    # hérité de SDD_Pro : l'architecture de la COQUILLE
│   │   │                    # (entrée, composition, config, Domaine) — le moteur
│   │   │                    # garde son découpage par ownership
│   │   ├── backend/         python-fastapi.md · node-express.md · nestjs.md
│   │   │                    kotlin-spring-boot.md [kotlin, java] · dotnet-minimalapi.md
│   │   │                    # hérité de SDD_Pro : la maison HTTP autour de la
│   │   │                    # surface, active seulement si backend-api
│   │   ├── framework/       langchain.md · langgraph.md · ms-agent-framework.md
│   │   │                    langgraph-js.md [typescript] · spring-ai.md [kotlin, java]
│   │   │                      (+ .libs.json chacun)
│   │   ├── orchestration/   single-agent.md · router.md · sequential.md
│   │   ├── rag/             none.md · hybrid.md [python] · hybrid-dotnet.md
│   │   │                    hybrid-node.md · hybrid-jvm.md
│   │   ├── vectorstore/     pgvector.md · pgvector-dotnet.md · pgvector-node.md
│   │   │                    pgvector-jvm.md (+ .libs.json chacun)
│   │   ├── embedding/       voyage.md · bge-local.md
│   │   ├── rerank/          none.md · cohere-rerank.md
│   │   │                    bge-reranker-local.md (+ .libs.json)
│   │   ├── dataaccess/      view-per-agent.md [python] · -dotnet · -node · -jvm
│   │   │                    declared-sources.md [python] · none.md
│   │   ├── memory/          buffer.md
│   │   ├── tools/           mcp.md [python] · mcp-dotnet.md · mcp-node.md · mcp-jvm.md
│   │   ├── eval/            pytest-eval.md · xunit-eval.md · vitest-eval.md
│   │   │                    junit-eval.md (+ .libs.json chacun)
│   │   ├── observability/   otel-genai.md · -dotnet · -node · -jvm (+ .libs.json)
│   │   │                    # tous écrivent le MÊME fichier de trace JSONL
│   │   ├── guardrails/      schema-validation.md · pii-redaction.md
│   │   │                    injection-detection.md   [*] (code de référence Python)
│   │   └── serving/         cli.md · cli-dotnet.md · cli-node.md · cli-kotlin.md
│   │                        cli-java.md · fastapi-sse.md · aspnet-minimal.md
│   │                        http-sse-node.md · spring-sse.md · batch.md
│   │                        # surface = PAR OÙ L'ON ENTRE ; le LIVRABLE
│   │                        # (DeliverableType) vit dans ## Project Config ;
│   │                        # le contrat d'évaluation (§3.5 de cli.md) est
│   │                        # le même dans les cinq langages
│   ├── registry/                      # ── REGISTRES MACHINE ─────────────────
│   │   ├── patterns.registry.json     # tout pattern : id, famille, critères, coût, risques
│   │   ├── compatibility.matrix.json  # lang x framework x pattern x provider x store ; combos
│   │   ├── architecture-requirements.yml  # P7 : ce que chaque choix de stack impose
│   │   ├── adr-requirements.yml       # les décisions qui exigent un ADR accepté (part `adr` de G2)
│   │   └── ir.schema.json             # schéma de l'Agentic IR (cf. docs/AGENTIC-IR.md)
│   ├── templates/
│   │   ├── STACK.md.template
│   │   ├── mission.template.md
│   │   ├── capability.template.md
│   │   ├── topology.template.md
│   │   ├── agent-contract.template.md
│   │   ├── tool-contract.template.md
│   │   ├── retrieval-contract.template.md
│   │   ├── memory-contract.template.md
│   │   ├── eval-suite.template.md
│   │   ├── roster.template.md         # roster déclaré par l'architecte (P7) — Markdown, bloc yaml
│   │   ├── golden-set.schema.json
│   │   ├── tool-schema.schema.json
│   │   ├── project-config.schema.json # valide les VALEURS de STACK.md (x-stackSections, x-readBy)
│   │   ├── libs-catalog.schema.json   # schéma des .libs.json de stacks/
│   │   ├── prompt.template.md
│   │   ├── adr.template.md
│   │   ├── datasets/
│   │   │   └── adversarial-seed.jsonl # jeu d'amorce écrit à la main, que qa-evals copie et étend
│   │   └── runtime/python/            # squelette de l'application générée (gen-app-skeleton)
│   │       ├── app/                   #   entrée, config, bornes, traces, orchestration, serving…
│   │       │   └── guardrails/        #   injection, PII, schéma de sortie — EN CODE, selon
│   │       │                          #   ## Active Guardrails
│   │       └── data/ · tools/         #   runtime des sources déclarées
│   │       # (les combinaisons de stack vivent dans registry/compatibility.matrix.json)
│   ├── digests/                       # tranches de taxonomie par agent
│   ├── sdda.py                        # lanceur : `python .sdda/sdda.py {cmd}` —
│   │                                  #   marche depuis un clone nu, sans pip install
│   └── python/                        # outillage déterministe 0-token
│       ├── sdda_cli.py                # dispatcher des 80 sous-commandes ; registre
│       │                              #   DÉRIVÉ du disque, lu aussi par les scanners
│       ├── sdda_lib/                  # config, markdown_io, hashing, pricing, traces,
│       │                              #   graders (dont judge_clients : le juge LLM réel)
│       ├── sdda_scripts/              # validate_*, estimate_budget, eval_runner, audit_ownership, …
│       ├── sdda_admin/                # harness_build, framework_smoke, sync_*, planned_scripts,
│       │                              #   command_flags, hooks_selfcheck
│       ├── sdda_hooks/                # 15 hooks bloquants PreToolUse / SubagentStop
│       │                              #   chacun déclare son WIRING ; harness_build
│       │                              #   les câble TOUS, aucune table en dur
│       └── tests/
│
└── workspace/                         # ── LE PROJET — l'humain fournit, le framework produit (§2.ter)
    │
    │   ═══ CE QUE L'HUMAIN FOURNIT ════════════════════════════════════════
    ├── stack/
    │   ├── STACK.md                   # VERSIONNÉ — les choix techniques, des NOMS de variables
    │   │                              #   (${LLM_API_KEY}), jamais de valeur. Sources inline, API, MCP.
    │   └── mcp.json                   # optionnel — config MCP standard importée telle quelle
    ├── feats/                         # ses spécifications — du MARKDOWN, à plat
    │   ├── {n}-{Name}.md              #   le brief (--from-brief)
    │   └── {n}-roster.md              #   le ROSTER : combien d'agents, lesquels, qui porte quoi (P7)
    ├── assets/                        # les données (racine des stores `kind: local`)
    │   └── .env                       #   les VALEURS des secrets du runtime — gitignoré, lu par AUCUN agent
    ├── seed/                          # la vérité terrain : scénarios annotés, labels
    │
    │   ═══ CE QUE LE FRAMEWORK PRODUIT ════════════════════════════════════
    ├── pipeline/                      # tout ce que le pipeline génère avant et autour du code
    │   ├── missions/    {n}-{Name}.md          # po-elicitor
    │   ├── caps/        {n}-{m}-{Name}.md      # po-capabilities
    │   ├── topology/    {n}-topology.md        # architect-topology, graphe Mermaid inclus
    │   ├── contracts/   agents/ · tools/ · retrieval/ · memory/   # les architectes
    │   ├── decisions/   ADR-{ts}-{slug}.md     # UN seul endroit (cf. §2.ter)
    │   ├── datasets/    golden/ · holdout/ · calibration/ · adversarial/   ┐ ce qui JUGE :
    │   ├── suites/      les suites d'évaluation                            │ qa-evals et les
    │   ├── baselines/   la référence de non-régression                     │ scripts, JAMAIS
    │   ├── calibration/ κ de chaque juge LLM                               │ un `dev-*`
    │   └── fixtures/    tools/ · retrieval figé — les doubles d'isolement L4 ┘
    │
    ├── src/
    │   └── {AppName}/                       # l'application agentic générée — layout PLAT (SDD_Pro) :
    │       │                                #   ce répertoire EST le paquet, un seul niveau
    │       ├── pyproject.toml · README.md   # le projet (dev-backend)
    │       ├── CLAUDE.md                    # contexte projet (project-init, 0 token) — AGENTS.md / GEMINI.md
    │       │                                #   selon le harnais ; lu par les dev-* à la place de STACK.md
    │       ├── .env                         # copié depuis assets/.env à la création du projet, sans LLM
    │       ├── app/                         # composition, config, Domaine (dev-backend)
    │       ├── shared/                      # types partagés, posés par la pré-passe (dev-orchestration)
    │       ├── agents/{agent}/              # un agent du produit (dev-agent, une instance par agent)
    │       ├── prompts/{agent}.system.md    # l'exécutable hashé de chaque agent (dev-prompt)
    │       ├── skills/ · rules/             # ce que l'agent SAIT FAIRE / DOIT FAIRE, un fragment par slug (dev-prompt)
    │       ├── tools/ · data/ · retrieval/  # le socle (dev-tools, dev-data, dev-retrieval)
    │       ├── memory/                      # interface (pré-passe) puis implémentation (dev-orchestration)
    │       ├── orchestration/ · serving/    # graphe et surface (dev-orchestration, dev-api)
    │       ├── data/schemas/                # schémas figés des sources — actif d'EXÉCUTION
    │       └── **/tests/                    # L0→L2 (qa-tests), à côté de ce qu'ils testent
    │
    └── .sys/                          # ── ÉTAT INTERNE ET SORTIES DE RUN ─────
        ├── .ir/         {n}-system.ir.json  # Agentic IR compilé depuis les contrats
        ├── .context/ · .state/ · .validation/ · .audit/
        ├── reports/     {n}-{run-id}.json   # rapports d'eval ; runs/ : exécutions enregistrées
        ├── traces/runs/ {run-id}.jsonl
        └── workspace.json                   # workspaceVersion — écrit par bootstrap,
                                             #   monté par sdda_scripts/migrate_workspace.py
```
