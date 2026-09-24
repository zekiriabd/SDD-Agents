# 📚 Documentation SDD_Agents

> **Spec Driven Development pour applications agentic, multi-harnais** (Claude
> Code, Codex, Gemini CLI) — un framework qui transforme une MISSION en système
> d'agents LLM testé, par **<!--sdda:count agents-->23<!--/sdda:count--> Developer Agents**, une orchestration Python
> déterministe (**0 token**) et **9 gates**.

Ceci est le hub de la documentation. Chaque document a un objet et un public.
Version anglaise : [README.md](README.md).

## Convention de langue

**L'anglais par défaut, le français en jumeau.**

| Fichier | Langue | Rôle |
|---|---|---|
| `NAME.md` | anglais | la page de référence, celle que visent tous les liens |
| `NAME.fr.md` | français | traduction de la même page, même contenu technique |

Le contenu technique — identifiants, codes `[CLASS]`, chemins, options, numéros
de section, marqueurs de compteurs, blocs de code — est identique dans les deux
langues, et les titres sont les mêmes, traduits, dans le même ordre. Une page
anglaise renvoie aux pages anglaises ; une page française renvoie aux jumeaux
français. Un fichier n'est jamais renommé `NAME.en.md` : la page anglaise garde
le nom que tous les liens emploient déjà.

Deux zones sont **volontairement en français seul** et ne suivent aucune règle
de jumeau : les prompts consommés par les LLM (`.sdda/agents/`,
`.sdda/commands/`, `.sdda/rules/`, `.sdda/stacks/`, `.sdda/templates/`,
`.sdda/digests/`) et les façades de harnais générées (`.claude/`, `.codex/`,
`.gemini/`). C'est du contenu opérationnel, pas de la documentation, et SDD_Pro
applique la même règle.

**L'architecture est la seule exception qui a les deux.** `harness_build.py`
compile `.claude/CLAUDE.md` (et les fichiers mémoire de Codex et Gemini) depuis
`ARCHITECTURE.fr.md`, parce que les agents lisent le français comme le reste de
leurs prompts. `ARCHITECTURE.md` est la référence anglaise, pour les lecteurs
humains. Les deux sont tenues au pas par le même contrôle de parité que tous les
autres jumeaux (voir plus bas).

---

## 🚀 Je veux comprendre le framework

| Étape | Objet | Document | Langues |
|---|---|---|---|
| **1** | Les 12 principes fondateurs | [../PHILOSOPHY.fr.md](../PHILOSOPHY.fr.md) | 🇬🇧 EN · 🇫🇷 FR |
| **2** | Arborescence, pipeline, 9 gates, abstraction harness/provider | [../ARCHITECTURE.fr.md](../ARCHITECTURE.fr.md) | 🇬🇧 EN · 🇫🇷 FR |
| **3** | Le vocabulaire clos : MISSION, CAP, AGENT, TOOL, RETRIEVER… | [DOMAIN-MODEL.fr.md](DOMAIN-MODEL.fr.md) | 🇬🇧 EN · 🇫🇷 FR |
| **4** | Machine à états Draft → Approved, dérivée des gates | [LIFECYCLE.fr.md](LIFECYCLE.fr.md) | 🇬🇧 EN · 🇫🇷 FR |
| **5** | Ce qui est hérité de SDD_Pro, refusé ou ajouté | [SDD-PRO-INHERITANCE.fr.md](SDD-PRO-INHERITANCE.fr.md) | 🇬🇧 EN · 🇫🇷 FR |

---

## 🏗 Je conçois un système (architectes)

| Sujet | Consommé par | Document | Langues |
|---|---|---|---|
| Patterns d'orchestration — catalogue et matrice de sélection | `architect-topology` | [ORCHESTRATION-PATTERNS.fr.md](ORCHESTRATION-PATTERNS.fr.md) | 🇬🇧 EN · 🇫🇷 FR |
| Patterns RAG, recherche et retrieval — catalogue et métriques de gate | `architect-rag` | [RAG-PATTERNS.fr.md](RAG-PATTERNS.fr.md) | 🇬🇧 EN · 🇫🇷 FR |
| Patterns de mémoire — portées, coûts, la mémoire comme surface d'attaque persistante | `architect-memory` | [MEMORY-PATTERNS.fr.md](MEMORY-PATTERNS.fr.md) | 🇬🇧 EN · 🇫🇷 FR |
| Accès base de données depuis un agent — une décision de sécurité | `architect-data` | [DATA-ACCESS.fr.md](DATA-ACCESS.fr.md) | 🇬🇧 EN · 🇫🇷 FR |
| Sources de données hors base — registre, connecteurs, secrets | `architect-data` | [DATA-SOURCES.fr.md](DATA-SOURCES.fr.md) | 🇬🇧 EN · 🇫🇷 FR |

SSoT machine derrière ces catalogues : [../registry/patterns.registry.json](../registry/patterns.registry.json)
et [../registry/compatibility.matrix.json](../registry/compatibility.matrix.json).

---

## ⚙️ Je veux savoir comment c'est construit

| Sujet | Document | Langues |
|---|---|---|
| L'Agentic IR — la représentation intermédiaire qui rend le multi-framework déterministe | [AGENTIC-IR.fr.md](AGENTIC-IR.fr.md) | 🇬🇧 EN · 🇫🇷 FR |
| Les <!--sdda:count agents-->23<!--/sdda:count--> Developer Agents et leur orchestration interne | [AGENT-ROSTER.fr.md](AGENT-ROSTER.fr.md) | 🇬🇧 EN · 🇫🇷 FR |
| Compilation vers Claude Code / Codex / Gemini CLI | [MULTI-HARNESS.fr.md](MULTI-HARNESS.fr.md) | 🇬🇧 EN · 🇫🇷 FR |
| Tests et évaluation — la pyramide L0→L9, *un test affirme, une eval note* | [TESTING-AND-EVAL.fr.md](TESTING-AND-EVAL.fr.md) | 🇬🇧 EN · 🇫🇷 FR |
| Les <!--sdda:count invariants-->21<!--/sdda:count--> invariants porteurs et leurs enforcers | [../INVARIANTS.yml](../INVARIANTS.yml) | fichier machine |
| La couche Python déterministe | [../python/README.fr.md](../python/README.fr.md) | 🇬🇧 EN · 🇫🇷 FR |
| Fixtures de test | [../python/tests/fixtures/README.fr.md](../python/tests/fixtures/README.fr.md) | 🇬🇧 EN · 🇫🇷 FR |

---

## 🗺 Où va le projet

| Sujet | Document | Langues |
|---|---|---|
| Ordre de construction et MVP — une combinaison validée de bout en bout avant d'en annoncer douze | [ROADMAP.fr.md](ROADMAP.fr.md) | 🇬🇧 EN · 🇫🇷 FR |
| Scripts déterministes planifiés — **générés** dans les deux langues, ne pas éditer à la main | [PLANNED-SCRIPTS.fr.md](PLANNED-SCRIPTS.fr.md) | 🇬🇧 EN · 🇫🇷 FR |

---

## Vérifier que ce hub dit vrai

`framework_smoke.py` parcourt `.sdda/*.md` et `.sdda/docs/*.md`, ce fichier
compris : toute classe d'erreur citée dans la prose normative doit avoir un
émetteur réel, et toute référence interne doit se résoudre. Un jumeau `.fr.md`
est parcouru sous la même règle.

Son contrôle `docs.parity` tient les jumeaux ensemble : pour chaque paire
`NAME.md` / `NAME.fr.md`, les deux pages doivent citer les mêmes codes
`[CLASS]`, porter le même nombre de titres dans le même ordre, les mêmes
marqueurs `sdda:count` et les mêmes blocs de code. Une traduction qui perd une
section, une classe ou une commande fait échouer le smoke au lieu de dériver en
silence. À lancer depuis la racine du dépôt :

```bash
python .sdda/sdda.py framework-smoke
```
