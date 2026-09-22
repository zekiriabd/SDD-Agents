<!-- GÉNÉRÉ par sdda_admin/harness_build.py depuis .sdda/commands/sdda-status.md.
     NE PAS ÉDITER ICI : toute modification est écrasée au build suivant,
     et le test de parité la signale. Éditer la source. -->

# /sdda-status

# /sdda-status — État dérivé des gates

Affiche l'état des MISSIONs du projet : gates franchies, verdicts par CAP avec
variance, budget estimé vs mesuré, bypasses actifs, résultats périmés.
**Lecture seule**, aucune écriture, aucune invocation d'agent, ~0 token.

> **LIFECYCLE R1 — l'état est dérivé, pas déclaré.** Cette commande ne lit
> **jamais** la ligne `Status:` d'un fichier pour décider : elle appelle
> `compute_status.py`, qui calcule l'état depuis les rapports
> `workspace/.sys/.validation/{n}-G*.json` et les tuples d'épinglage. Un
> `Status:` non couvert par un rapport est signalé `[STATUS_UNBACKED]`. Un état
> auto-proclamé est le mécanisme par lequel un pipeline agentic se déclare vert.

**Usage :**
- `/sdda-status` — toutes les MISSIONs
- `/sdda-status {n}` — une MISSION, détail par CAP et par gate
- `/sdda-status {n} --gates` — détail contrôle par contrôle de chaque gate
- `/sdda-status --json` — sortie machine (CI, console)

---

## STEP 1 — Portée

`{n}` fourni → mode mono-MISSION ; sinon multi (toutes les
`workspace/missions/*.md`). Si aucune MISSION :

```
Aucune MISSION dans workspace/missions/. Lancer /sdda-mission {Name} pour démarrer.
```
STOP.

`STACK.md` absent → 1 ligne `[STACK_MISSING] — lancer /sdda-bootstrap` puis
continuer (l'état des fichiers reste lisible).

---

## STEP 2 — Scripts déterministes (source de vérité unique)

Pas de Glob/Read manuel : les scripts font déjà le calcul, sans réinterpréter.

```bash
# État dérivé : gates, états, régressions, périmés
python .sdda/python/sdda_scripts/compute_status.py [--mission {n}] --json

# Fraîcheur des tuples d'épinglage (prompt, model, index, tool_schema, dataset)
python .sdda/python/sdda_scripts/check_baseline_freshness.py [--mission {n}] --json

# Dernier run et bypasses
python .sdda/python/sdda_scripts/sdda_state.py status [--mission {n}] --json
```

Ce que `compute_status.py` applique :

| Règle | Effet visible |
|---|---|
| **R1** dérivé | état = plus haute gate dont le rapport est vert **et** frais |
| **R2** régression silencieuse | un hash épinglé a bougé → tout ce qui est au-dessus d'`Implemented` redescend ; les rapports G5→G8 concernés sont marqués `stale` |
| **R3** minimum des enfants | MISSION = min(CAPs) ; une CAP `Blocked` → MISSION `Blocked` |
| **R4** confiance plafonnée | une CAP `high` sous une MISSION `medium` → `[CONFIDENCE_ESCALATION]` affiché |
| **R5** bypass audité | chaque bypass du dernier run est listé, avec sa raison |

---

## STEP 3 — Rapport (tree ASCII compact)

### Mode multi

```
SDD_Agents — {M} MISSION(s) · STACK {lang} + {framework} · MaxParallel {mp}

MISSION 1-SupportAssistant                                        Evaluated  🟡
  gates       G0 ✅  G1 ✅  G2 ✅  G3 ✅  G4 ✅  G5 🟡  G6 ✅  G7 ✅  G8 —
  budget      $0.041/run (cible $0.05, cap $0.25)   p95 6.2 s (cible 8 s)   ✅
  holdout     objectif 0.90 → non mesuré (G8 non lancée)
  CAPs        4 · 2 Approved · 1 Evaluated 🟡 · 1 Blocked 🔴
  dernier run 2026-09-19T14:02Z /sdda-full · partial · bypasses : 1 (G5 jaune assumé)

MISSION 2-InvoiceExtractor                                        Architected  ✅
  gates       G0 ✅  G1 ✅  G2 ✅  G3 —  G4 n/a  G5 —  G6 —  G7 —  G8 —
  budget      estimé $0.012/run (cible $0.02)
  CAPs        3 · 3 Specified

MISSION 3-Onboarding                                              Draft
  gates       G0 🔴 [MISSION_INCOMPLETE] 2 <à préciser> résiduels

Total : {M} MISSION(s) · {C} CAPs · {A} Approved · {B} Blocked · {S} résultat(s) périmé(s)
```

### Mode mono (`/sdda-status {n}`)

```
MISSION 1-SupportAssistant                                        Evaluated  🟡
  confidence  high
  budget      $0.041/run (cible $0.05)   p95 6.2s (cible 8s)   60k tokens cap   ✅
  holdout     objectif 0.90 → mesuré 0.88                       🟡  G8 non franchie

  CAP 1-1 ClassifyIntent          Approved   0.97 ±0.01  (k=5)  ✅
  CAP 1-2 ExplainInvoiceLine      Evaluated  0.86 ±0.09  (k=3)  🟡  variance 10% > seuil ; juge groundedness advisory (κ 0.52)
  CAP 1-3 RouteByIntent           Approved   0.96 ±0.02  (k=5)  ✅
  CAP 1-4 IssueRefundTicket       Blocked    [INJECTION_SUCCEEDED]  🔴  adv-17 indirecte via corpus

  topologie   router · 2 agents · 4 outils · 1 retriever · alternative écartée : single-agent (isolation `create_ticket`)
  effets      read-only 3 · external-side-effect 1 (idempotence natural-key, dry-run, cap 1/run)
  retrieval   contracts-index  recall@8 0.84 · nDCG 0.76 · groundedness 0.88 · citations 0.99   ✅
  prompts     2 · hashés · lint ✅
  épinglage   prompt ✅  model ✅  index ✅  tool_schema ✅  dataset ✅   (frais)
  datasets    golden 120 · holdout 40 · calibration 50 (2 juges : 1 calibré, 1 advisory) · adversarial 41
  traces      workspace/traces/runs/ — 187 runs · dernier 2026-09-19T14:02Z
  bypasses    G5 jaune assumé (--force, 2026-09-19, jdoe, « variance connue, dataset v2 en cours »)
  rapports    .sys/.validation/1-G0..G7-*.json · evals/reports/1-{run}.md
```

Cas à flagger explicitement (`⚠️`) :

| Situation | Ligne |
|---|---|
| `Status:` dans un fichier ≠ état dérivé | `⚠️ [STATUS_UNBACKED] caps/1-2-*.md déclare Tested — aucun rapport G5 vert · écrasé` |
| tuple d'épinglage bougé | `⚠️ [EVAL_STALE] prompt billing-specialist modifié depuis G5 — MISSION redescendue à Implemented (R2)` |
| IR périmé | `⚠️ [IR_STALE] topology hash ≠ compiledFrom — /sdda-topology {n} --recompile-only` |
| bypass actif sur le dernier run | `⚠️ bypass G4 (SDDA_BYPASS_RETRIEVAL_GATE) — recall 0.61 sous seuil, résultat porté en G7` |
| juge advisory | `⚠️ juge {g} advisory (κ {x} < {min}) — la CAP ne peut pas être 🟢` |
| CAP `Blocked` | classe `[CLASS]` portée + item/rapport |
| confiance escaladée | `⚠️ [CONFIDENCE_ESCALATION] CAP 1-3 high sous MISSION medium` |

`--gates` ajoute, sous chaque gate, la liste des contrôles (✅/🔴, classe,
fichier:section).

---

## STEP 4 — Suggestion (1 ligne)

Déduite de l'état dérivé de la MISSION la plus avancée non `Approved` :

| État | Ligne |
|---|---|
| `Draft` (G0 🔴) | `→ /sdda-mission {n} pour compléter la MISSION` |
| `Specified` (G1 ✅) | `→ /sdda-topology {n}` |
| `Architected` | `→ /sdda-build {n} (ou /sdda-full {n} --from-phase build)` |
| `Implemented` / `Tested` | `→ /sdda-eval {n}` puis `/sdda-review {n}` |
| `Evaluated` 🟢 | `→ /sdda-eval {n} --acceptance` |
| `Evaluated` 🟡 | `→ lire les AC jaunes ; livrer maintenant est un pari sur le prochain tirage` |
| `Blocked` | `→ corriger [{CLASS}] ({rapport}) puis /sdda-full {n} --resume` |
| stale | `→ /sdda-eval {n} --run-only pour ré-exécuter les résultats périmés` |
| tout `Approved` | `Pipeline complet. Inspecter workspace/src/ et workspace/evals/reports/.` |

---

## Règles de cette commande

- **Lecture seule.** Aucun Write/Edit, aucun agent, aucune Q/R.
- **Délégation pure** vers `compute_status.py`, `check_baseline_freshness.py`,
  `sdda_state.py` (déterministes, 0 token).
- **Jamais la ligne `Status:`** comme source : toujours les rapports de gate.
- **Trois couleurs, jamais pass/fail** ; la variance est toujours affichée à
  côté du score (P3).
- **Format compact** : tree ASCII, pas de récap verbeux.

---

## Chat Output Protocol

Applique ``.sdda/rules/output-protocol.md` (Read ce fichier avant de poursuivre)`. Label `[ANALYSIS]` (diagnostic
read-only). Sortie 1 passe. Le tree ASCII est le rendu final, sans préfixe.
Erreurs : 1 ligne `🔴 [ANALYSIS/FAIL] {résumé}`. Bypass verbeux :
`SDDA_CHAT_VERBOSE=1`.
