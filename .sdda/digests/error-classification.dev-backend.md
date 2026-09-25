<!-- GÉNÉRÉ par sdda_admin/sync_digests.py — ne pas éditer à la main.
     Source : la fiche .sdda/agents/dev-backend.md + rules/error-classification.md
     Régénérer : python .sdda/sdda.py sync-digests -->

# Digest — classes d'erreur de `dev-backend`

Tranche de `@.sdda/rules/error-classification.md` réduite à ce que cet agent
peut émettre. **Aucun bloc ERROR sans préfixe `[CLASS]`.**

Format obligatoire (cf. `rules/output-protocol.md` §3) :

```
ERROR: <qui> — <quoi>
CAUSE: [CLASS] <le fait constaté, avec ses valeurs>
FIX: <l'action précise qui débloque>
```

## Classes propres à cet agent

- `[BUILD_CORRECTIBLE]`
- `[DATASET_OWNERSHIP_VIOLATION]`
- `[IR_NOT_FOUND]`
- `[OWNERSHIP_VIOLATION]`
- `[PACKAGING_ARCHI_UNDECLARED]`
- `[PACKAGING_BACKEND_SHEET_MISSING]`
- `[PROJECT_NOT_INIT]`
- `[PROMPT_OWNERSHIP_VIOLATION]`
- `[SECRET_FILE_MISSING]`
- `[SECRET_LEAK]`
- `[SECRET_READ_FORBIDDEN]`
- `[SECRET_VAR_UNDECLARED]`
- `[SEC_ENV_VAR_FORBIDDEN]`
- `[STACK_COMBO_UNLOADABLE]`
- `[STACK_LANGUAGE_MISMATCH]`
- `[STACK_LIBRARY_MISSING]`

## Classes universelles

- `[INVALID_ARG]`
- `[PACK_UNUSABLE]`
- `[STACK_MISSING]`

---

Une classe absente de cette liste ne doit pas être inventée : elle ne serait
parsée par personne, donc ne déclencherait aucune reprise et n'apparaîtrait dans
aucun tableau de bord. Émettre avec la classe la plus proche et signaler le trou.
