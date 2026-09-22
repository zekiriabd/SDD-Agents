# Règle — Protocole de sortie

> Règle **inconditionnelle** : chargée par tous les agents et toutes les
> commandes. Elle porte aussi le noyau universel du format d'erreur, pour que la
> taxonomie complète reste en chargement paresseux.

---

## 1. Sortie chat : une ligne

Un agent rend **une ligne** en fin d'exécution. Le détail va sur disque, pas dans
la conversation.

```
[AGENT] <objet> — <résultat chiffré> <emoji de verdict>
```

Exemples :

```
[CAPS]      MISSION 1-SupportAssistant → 4 CAPs, 11 AC mesurables, couverture 8/8 ✅
[TOPOLOGY]  router, 2 agents, 5 outils — pire cas $0.148/14.2s (plafond $0.25/8s) 🟡
[TOOL-GATE] 5 outils — 5 contrats verts, 1 connectivité KO (zendesk) ❌
[EVAL]      CAP 1-2 groundedness 0.847 ±0.071 (seuil 0.85, k=3, pass 2/3) 🟡
```

**Pourquoi c'est une règle et pas un style.** Un pipeline qui fait tourner 26
agents produit des centaines de sorties. Si chacune raconte son travail, la
seule information qui compte — quelle gate a rendu rouge et pourquoi — devient
introuvable. La verbosité d'un agent est prise sur l'attention d'un humain.

**Interdits** : préambule (« Je vais maintenant… »), récapitulatif de ce qui
vient d'être fait, reformulation de la consigne, remerciement, liste de fichiers
écrits quand ils sont déductibles.

---

## 2. Toujours des chiffres, jamais des adjectifs

| ❌ | ✅ |
|---|---|
| « la couverture est bonne » | `couverture 8/8 BR · 3/3 AC` |
| « les evals passent » | `4/4 suites vertes, variance max 4%` |
| « le budget est respecté » | `pire cas $0.148 / plafond $0.25` |
| « quelques outils échouent » | `1/5 outils KO : zendesk (AUTH_FAILED)` |

Un adjectif ne peut être ni vérifié ni contredit. Dans un framework dont la
raison d'être est de refuser le faux vert, c'est disqualifiant.

---

## 3. Le bloc ERROR — trois lignes, toujours

```
ERROR: <qui> — <quoi, en une clause>
CAUSE: [CLASS] <le fait constaté, avec ses valeurs>
FIX: <l'action précise qui débloque>
```

Règles :

- **`CAUSE:` commence toujours par une classe `[CLASS]`** entre crochets, en
  majuscules anglaises. Sans exception. C'est ce qui permet aux hooks, aux
  boucles de reprise et aux tableaux de bord de classer sans interpréter du
  texte libre.
- `FIX:` donne une action, pas un conseil. « vérifier la configuration » n'est
  pas un fix ; « compléter `## Ground Truth` puis relancer `/sdda-caps 1` » en
  est un.
- Le bloc complet va sur disque (`workspace/.sys/.validation/`). Le chat ne
  reçoit que la ligne de résumé et le chemin du rapport.

Même forme pour `WARN:`, non bloquant.

---

## 4. La règle mentale de la classe

Avant d'écrire un `ERROR` ou un `WARN`, demande-toi à quelle **famille** il
appartient :

`MISSION_` · `CAP_` · `TOPOLOGY_` · `AGENT_` · `TOOL_` · `RETRIEVAL_` ·
`MEMORY_` · `PROMPT_` · `EVAL_` · `JUDGE_` · `BUDGET_` · `SAFETY_` · `TRACE_` ·
`CONFIG_`

Si aucune ne convient, la taxonomie a un trou : émets quand même avec la famille
la plus proche et signale le trou. N'invente pas une famille — une classe
inconnue est une classe que personne ne parsera.

Taxonomie complète : `@.sdda/rules/error-classification.md` (chargement
paresseux). Chaque agent lit sa tranche :
`.sdda/digests/error-classification.{agent}.md`.

---

## 5. Les trois couleurs

Tout verdict de gate ou d'eval est 🟢 / 🟡 / 🔴, jamais pass/fail.

- 🟢 au-dessus du seuil, `pass_rate` à 1.0, variance sous le plancher
- 🟡 au-dessus du seuil mais variance élevée ou un run qui échoue
- 🔴 sous le seuil, classe critique en échec, ou budget dépassé

**Le jaune se dit, il ne s'arrondit pas.** Un agent qui rapporte vert un résultat
jaune commet la faute la plus grave du framework : il rend un système non
déterministe indiscernable d'un système fiable.

---

## 6. Ne jamais déclarer un état qu'on n'a pas mesuré

Interdits absolus :

- écrire `Status: Tested` sans rapport de gate sur disque → `[STATUS_UNBACKED]`,
  le script écrase (`LIFECYCLE.md` R1) ;
- rapporter une eval depuis un seul run ;
- dire « les tests passent » sans avoir exécuté la commande ;
- résumer un résultat d'un autre agent sans avoir lu son rapport.

> Un état auto-proclamé est le mécanisme exact par lequel un pipeline agentic se
> déclare vert. Tout le reste du framework existe pour l'empêcher ; ce serait
> absurde de le réintroduire par la sortie chat.

---

## 7. Quand on ne sait pas

Le dire, avec la raison :

```
WARN: agent review-rag — verdict indisponible
CAUSE: [EVAL_BASELINE_STALE] l'index a changé (indexHash différent) ; la
       baseline du 2026-09-14 ne mesure plus le même système
FIX: relancer /sdda-eval 1 --retrieval-only pour régénérer la baseline
```

C'est plus utile qu'un verdict fabriqué, et infiniment moins cher qu'un verdict
fabriqué qu'on découvre faux trois semaines plus tard.
