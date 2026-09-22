<!-- GÉNÉRÉ par sdda_admin/sync_digests.py — ne pas éditer à la main.
     Source : la fiche .sdda/agents/review-rag.md + rules/error-classification.md
     Régénérer : python .sdda/sdda.py sync-digests -->

# Digest — classes d'erreur de `review-rag`

Tranche de `@.sdda/rules/error-classification.md` réduite à ce que cet agent
peut émettre. **Aucun bloc ERROR sans préfixe `[CLASS]`.**

Format obligatoire (cf. `rules/output-protocol.md` §3) :

```
ERROR: <qui> — <quoi>
CAUSE: [CLASS] <le fait constaté, avec ses valeurs>
FIX: <l'action précise qui débloque>
```

## Classes propres à cet agent

- `[ABSTENTION_MISSING]`
- `[CITATION_UNRESOLVED]`
- `[EVAL_PIN_STALE]`
- `[JUDGE_UNCALIBRATED]`
- `[MEASUREMENT_MISSING]`
- `[RAG_AGENT_COMPENSATING]`
- `[RAG_GENERATION_ISSUE]`
- `[RAG_RETRIEVAL_ISSUE]`
- `[RETRIEVAL_BELOW_THRESHOLD]`
- `[RETRIEVAL_CONFIG_DRIFT]`

## Classes universelles

- `[INVALID_ARG]`
- `[PACK_UNUSABLE]`
- `[STACK_MISSING]`

---

Une classe absente de cette liste ne doit pas être inventée : elle ne serait
parsée par personne, donc ne déclencherait aucune reprise et n'apparaîtrait dans
aucun tableau de bord. Émettre avec la classe la plus proche et signaler le trou.
