<!-- GÉNÉRÉ par sdda_admin/sync_digests.py — ne pas éditer à la main.
     Source : la fiche .sdda/agents/qa-tests.md + rules/error-classification.md
     Régénérer : python .sdda/sdda.py sync-digests -->

# Digest — classes d'erreur de `qa-tests`

Tranche de `@.sdda/rules/error-classification.md` réduite à ce que cet agent
peut émettre. **Aucun bloc ERROR sans préfixe `[CLASS]`.**

Format obligatoire (cf. `rules/output-protocol.md` §3) :

```
ERROR: <qui> — <quoi>
CAUSE: [CLASS] <le fait constaté, avec ses valeurs>
FIX: <l'action précise qui débloque>
```

## Classes propres à cet agent

- `[BOUND_NOT_MATERIALIZED]`
- `[DATA_ACCESS_ENVELOPE_MISSING]`
- `[IR_NOT_FOUND]`
- `[PROJECT_NOT_INIT]`
- `[TOOL_CONTRACT_FAILED]`

## Classes universelles

- `[INVALID_ARG]`
- `[PACK_UNUSABLE]`
- `[STACK_MISSING]`

---

Une classe absente de cette liste ne doit pas être inventée : elle ne serait
parsée par personne, donc ne déclencherait aucune reprise et n'apparaîtrait dans
aucun tableau de bord. Émettre avec la classe la plus proche et signaler le trou.
