# ADR-{YYYYMMDDTHHMM}-{slug}: {Titre court, à l'indicatif}

MISSION: {n}-{MissionName}                 # ou FRAMEWORK si la décision porte sur SDD_Agents lui-même
Status: Proposed                           # Proposed | Accepted | Superseded | Deprecated | Rejected
Date: {YYYY-MM-DD}
Deciders: <rôles, pas des noms — ex. Tech Lead, architect-topology (proposition), humain (arbitrage)>
Supersedes: <ADR-… | NONE>
Superseded by: <ADR-… | NONE>

> **Un arbitrage qui contredit un principe de PHILOSOPHY.md exige un ADR, pas
> un commit.** Un ADR n'est pas une justification après coup : il est écrit
> **avant** l'implémentation, et la TOPOLOGY GATE / la SAFETY GATE refusent
> l'artefact tant que l'ADR référencé n'existe pas ou n'est pas `Accepted`.
>
> Identifiant : `ADR-{timestamp}-{slug}` (DOMAIN-MODEL.md §5), stable, jamais
> renuméroté. Fichier : `workspace/feats/decisions/ADR-{timestamp}-{slug}.md`.

---

## 1. Contexte

<3-6 phrases. La situation qui force une décision : quel besoin, quelle
 contrainte mesurée, quel principe ou quel défaut du framework est en tension.
 Des **faits** (mesures, volumes, coûts constatés) séparés des **hypothèses** —
 même contrat que le reste du pipeline (ARCHITECTURE §5).>

- **Faits** : <ex. golden set de 120 questions ; recall@8 = 0.61 en `classic`
  ; corpus 14 000 documents ; latence p95 mesurée 11 s>
- **Hypothèses** : <ex. les questions thématiques représenteront ~20 % du trafic>
- **Principe(s) / invariant(s) en tension** : <ex. P7 (simplicité) ; `network`
  refusé par défaut ; `DbAgentRole: readonly`>

## 2. Décision

<Une phrase à l'indicatif : « Nous activons … », « Nous refusons … ».
 Puis le détail opérationnel : quelle clé de config, quel pattern, quelle borne,
 quel fichier porte la décision.>

| Artefact touché | Valeur avant | Valeur après |
|---|---|---|
| <ex. `STACK.md` › `DbAgentRole`> | `readonly` | `scoped-write` |
| <ex. `topology/{n}-topology.md` › Root Pattern> | `router` | `supervisor` |

## 3. Alternatives écartées

> Section obligatoire. Une décision sans alternative examinée est une
> préférence, pas une décision. L'alternative **plus simple** doit y figurer
> (P7) — et si c'est le défaut du framework qui est écarté, dire pourquoi
> précisément, avec la mesure quand elle existe.

| Alternative | Ce qu'elle apportait | Ce qui la disqualifie | Mesure / preuve |
|---|---|---|---|
| <défaut du framework — ex. rester en `readonly` + escalade humaine> | … | … | … |
| <alternative 2> | … | … | … |
| <ne rien faire> | … | … | … |

## 4. Conséquences

### 4.1 Positives
- <ce que la décision rend possible ou moins cher — chiffré si possible>

### 4.2 Négatives et risques acceptés
- <ce qu'on perd, ce qui devient plus cher, ce qui devient plus difficile à
  évaluer ou à déboguer>
- <risque de sécurité introduit, et pourquoi il est acceptable ICI>

### 4.3 Obligations créées
<Ce que la décision rend obligatoire — chacune devient un item vérifiable.>

- [ ] <ex. suite d'injection étendue à la nouvelle source non maîtrisée (P8)>
- [ ] <ex. eval de trajectoire sur la distribution des hops (supervisor)>
- [ ] <ex. `max_reflections` = 3 et `improvement-delta-stop` = 0.02 dans le contrat>
- [ ] <ex. test L8 « franchissement d'autorisation » sur la nouvelle vue>
- [ ] <ex. clé de config durcie dans la couche team>

## 5. Mesure de validation

> Comment saura-t-on que la décision était la bonne ? Une métrique, un seuil,
> un dataset, une échéance — même exigence que pour un AC de CAP (P2). Une
> décision qu'on ne peut pas mesurer est réversible par défaut : la date de
> revue l'impose.

| Métrique | Seuil attendu | Dataset | Échéance de revue |
|---|---|---|---|
| <ex. coût mesuré par run> | <≤ 0.05 USD> | `workspace/proof/datasets/holdout/…` | <date> |
| <ex. recall@8> | <≥ 0.80> | `workspace/proof/datasets/golden/retrieval-…` | <date> |

- **Condition de réversion** : <ce qui, constaté, déclenche l'abandon de la
  décision — ex. coût mesuré > 0.10 USD sur 2 runs d'eval consécutifs>

## 6. Références

- MISSION / CAPs : `workspace/feats/missions/{n}-…md`, `workspace/feats/caps/{n}-{m}-…md`
- TOPOLOGY : `workspace/feats/topology/{n}-topology.md` §9
- Contrats touchés : <…>
- Documents du framework : <ex. `ORCHESTRATION-PATTERNS.md#supervisor`,
  `DATA-ACCESS.md §3`, `PHILOSOPHY.md P7`>
- Rapports d'eval / mesures citées : `workspace/.sys/reports/…`

---

## Décisions qui EXIGENT un ADR (liste non exhaustive, tirée des documents du framework)

| Décision | Source |
|---|---|
| Activer le pattern `network` — l'ADR doit démontrer qu'aucun `graph` ne convient | ORCHESTRATION-PATTERNS.md |
| `DbAgentRole: scoped-write` ou `full` | DATA-ACCESS.md §3, INVARIANTS `db-safety-envelope-present` |
| `MemoryPIIPolicy: allow` | INVARIANTS `pii-not-in-vector-store`, STACK.md |
| `TracePIIPolicy: raw` | STACK.md ## Active Observability |
| Choix du pattern racine hors `single-agent` (quand la TOPOLOGY seule ne suffit pas) | topology.template.md §9 |
| Stratégie d'accès base en écriture | topology.template.md §9 |
| Contredire un principe de PHILOSOPHY.md | PHILOSOPHY.md, préambule |
