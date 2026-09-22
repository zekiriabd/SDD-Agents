# Stack: none (rerank)

Stack ID: rerank-none
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: *
Scope: **absence déclarée** de réordonnancement. Les `topK` résultats du retriever sont servis au modèle dans l'ordre rendu par la fusion. Aucune dépendance, aucun appel réseau supplémentaire, aucun fichier généré.

---

## 1. Rôle et périmètre

`none` est le défaut, et c'est le bon défaut. Un reranker corrige un problème
précis — *le bon document est retrouvé mais mal classé* — et ce problème doit
être **mesuré** avant d'être traité. Ajouter un reranker sans l'avoir constaté
coûte un appel réseau par requête (~100-300 ms) et une dépendance de plus, pour
un gain qu'on ne sait pas nommer.

Le diagnostic tient en deux métriques que `run_retrieval_eval.py` produit déjà :

| `recall@25` | `nDCG@5` | Lecture | Action |
|---|---|---|---|
| bon | bon | le retrieval fait son travail | **rester en `none`** |
| bon | médiocre | le bon document est trouvé, mal classé | **c'est le cas du reranker** |
| mauvais | — | le bon document n'est pas trouvé | réparer le retrieval — un reranker n'y peut rien |

La troisième ligne est celle qu'on oublie. Reclasser de mauvais résultats produit
de meilleurs mauvais résultats : le `nDCG@5` monte, la réponse reste fausse.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `rerank-none` |
| **Famille** | Retrieval · absence déclarée |
| **Paramètres STACK.md** | `## Active Reranker` → cette fiche ; `RerankEnabled: false`, `RerankTopN` inerte |
| **Dépendances** | aucune |
| **Fichiers générés** | aucun |

---

## 3. Effet sur le pipeline

| Élément | Effet |
|---|---|
| `retrievers[].binding.rerank` de l'IR | absent — l'absence est la représentation de `none` |
| `RetrievalTopK` | seule taille qui compte : c'est ce qui est servi au modèle |
| `RerankTopN` | ignoré ; une valeur active sans reranker est `[RETRIEVAL_CONFIG_DRIFT]` |
| **RETRIEVAL GATE (G4)** | inchangée — `nDCG@k` se mesure avec ou sans reranker |
| `LatencyP95TargetMs` | ne porte aucun budget de rerank |

---

## 4. Conventions imposées

1. **`RerankEnabled: false` et `## Active Reranker` → `none.md` vont ensemble.**
   Un `RerankEnabled: true` avec cette fiche active est une configuration qui
   ment : la clé est lue, rien ne l'implémente. Vérifié par
   `preflight_stack_combo` → `[RETRIEVAL_CONFIG_DRIFT]`.
2. **Le passage à un reranker se justifie par une mesure, pas par une intuition.**
   Le rapport de `run_retrieval_eval.py` qui montre l'écart `recall@25` /
   `nDCG@5` est la pièce à verser à l'ADR.
3. **`RetrievalTopK` reste modeste.** Sans reranker, récupérer large ne sert à
   rien : on sert tout au modèle, donc on paie le contexte et on dilue
   l'attention. 5 à 8 est l'ordre de grandeur utile.

---

## 5. Commande de smoke

```bash
python .sdda/python/sdda_scripts/run_retrieval_eval.py --index {index_slug} --json
#   -> compare recall@k et nDCG@k ; si recall@25 est bon et nDCG@5 médiocre,
#      le rapport recommande explicitement d'évaluer un reranker. exit 0.
```

---

## 6. Pièges connus

1. **Le reranker par réflexe.** « C'est du RAG sérieux, donc il faut un
   reranker. » Sur un corpus homogène de quelques milliers de chunks avec une
   fusion hybride correcte, le gain est souvent dans le bruit de mesure — et la
   latence, elle, est certaine.
2. **Augmenter `RetrievalTopK` en restant sans reranker.** Passer de 8 à 25
   n'améliore rien et multiplie le contexte servi : `RetrievalTopK: 25` n'a de
   sens que devant un reranker qui le ramène à `RerankTopN`.
3. **Mesurer le `nDCG` sur le golden set qui a servi à régler le découpage.**
   Le verdict est alors optimiste par construction. Le holdout existe pour ça.
