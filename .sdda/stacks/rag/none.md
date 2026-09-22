# Stack: none (rag)

Stack ID: rag-none
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: *
Scope: **absence déclarée** de recherche documentaire. Aucun corpus, aucun index vectoriel, aucun embedding. Les connaissances de l'agent viennent de son prompt, de ses outils et de son accès aux données structurées. Aucune dépendance, aucun fichier généré.

---

## 1. Rôle et périmètre

`none` est le défaut raisonnable, pas un aveu de pauvreté. Le RAG répond à un
problème précis — *retrouver le passage pertinent dans un corpus de documents
en langue naturelle trop gros pour le contexte*. Quand ce problème n'existe pas,
en ajouter un est un coût pur : un index à maintenir, une qualité de
récupération à mesurer (G4), une surface d'injection indirecte de plus (P8), et
une latence supplémentaire à chaque tour.

Quand `none` est le bon choix, et c'est fréquent :

- les données sont **structurées** — commandes, stock, tracking, factures. On
  les interroge, on ne les « retrouve » pas par similarité. C'est du DATA ACCESS
  (`dataaccess/declared-sources.md`, `dataaccess/view-per-agent.md`).
- les connaissances sont **stables et petites** : quelques règles métier, une
  grille tarifaire. Elles vivent dans le prompt, versionnées et hashées.
- l'agent est **transactionnel** : il appelle des outils et compose un résultat.

Quand `none` devient faux : dès qu'une réponse doit citer un document — CGV,
procédure, fiche produit rédigée — que personne ne peut tenir intégralement dans
un contexte. Passer alors à `classic.md`, puis `hybrid.md` si les requêtes
portent des identifiants ou du jargon que le vecteur seul rate.

**Le piège inverse est le plus courant** : mettre du RAG sur des données
structurées. Chercher « les commandes de septembre » par similarité cosinus
donne des résultats plausibles et faux, là où un filtre donne la réponse.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `rag-none` |
| **Famille** | RAG · absence déclarée |
| **Paramètres STACK.md** | `## Active RAG Pattern` → cette fiche ; `## Active Retrieval Stack` entièrement ignorée (vectorstore, embedding, chunking, rerank) |
| **Dépendances** | aucune |
| **Fichiers générés** | aucun |

---

## 3. Effet sur le pipeline

| Élément | Effet |
|---|---|
| `retrievers[]` de l'IR | vide — l'absence est la représentation de `none` |
| **RETRIEVAL GATE (G4)** | sans objet : aucun index à évaluer, `run_retrieval_eval.py` n'a rien à mesurer |
| `RagQualityMode` | auto-skip, quelle que soit sa valeur dans `## Project Config` |
| `architect-rag` | non invoqué en PHASE 2 |
| Seuils `Retrieval*`, `GroundednessMin`, `CitationResolveRateMin` | inertes |
| Suite adversariale | **toujours requise** si un outil est `untrusted` — l'injection indirecte n'a pas besoin de RAG pour exister (un champ texte d'un JSON suffit) |

La dernière ligne est la seule qui surprend, et c'est la plus importante :
supprimer le RAG réduit la surface d'attaque, il ne l'annule pas.

---

## 4. Conventions imposées

1. **`## Active Retrieval Stack` reste en commentaire.** Une ligne
   ` - .sdda/stacks/vectorstore/*.md` active avec `rag/none` est
   `[RAG_STACK_INCONSISTENT]` : le smoke de bootstrap la signale.
2. **Aucun `EmbeddingModel` effectif.** La clé peut rester documentée ; une
   valeur active sans RAG ni mémoire vectorielle est ignorée et signalée.
3. **Les connaissances mises dans le prompt sont hashées.** Une grille tarifaire
   dans un système prompt est du contenu qui change sans que rien ne l'attrape :
   elle relève du pinning de prompt (P10) et d'une re-eval à chaque édition.
4. **Le choix se justifie dans la TOPOLOGY.** `none` est le défaut, donc il ne
   demande pas de défense ; mais la CAP doit dire d'où vient sa donnée. Une CAP
   dont la source est « le modèle sait » est un `[AC_NOT_EVALUABLE]` en puissance.

---

## 5. Commande de smoke

```bash
python .sdda/sdda.py compute-status --json
#   -> G4 absente du plan de gates ; aucun retriever dans l'IR. exit 0.
```

---

## 6. Pièges connus

1. **Le RAG par réflexe.** « C'est un agent, donc il faut du RAG. » Sur des
   données structurées, il dégrade la justesse et ajoute un gate à tenir.
2. **Le corpus qui arrive plus tard.** Un PDF de procédure ajouté en cours de
   route « juste collé dans le prompt » : coût par run multiplié, et aucune
   citation résolvable. Dès qu'il y a un document, il y a un pattern RAG à
   choisir explicitement.
3. **Croire la surface d'attaque nulle.** Sans corpus, il reste les sorties
   d'outils, les champs texte libres des données et le message utilisateur.
   `guardrails/injection-detection.md` et la suite adversariale restent requis
   dès qu'une source est `untrusted`.
4. **Les seuils oubliés.** `GroundednessMin: 0.85` reste écrit dans
   `STACK.md` et donne l'impression d'une garantie de non-hallucination. Avec
   `rag/none`, il ne mesure rien : la justesse se mesure alors sur les CAP ACs,
   contre les données des outils.
