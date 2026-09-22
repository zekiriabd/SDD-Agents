<!-- GÉNÉRÉ par sdda_admin/sync_digests.py — ne pas éditer à la main.
     Source : la fiche .sdda/agents/review-safety.md + rules/error-classification.md
     Régénérer : python .sdda/python/sdda_admin/sync_digests.py -->

# Digest — classes d'erreur de `review-safety`

Tranche de `@.sdda/rules/error-classification.md` réduite à ce que cet agent
peut émettre. **Aucun bloc ERROR sans préfixe `[CLASS]`.**

Format obligatoire (cf. `rules/output-protocol.md` §3) :

```
ERROR: <qui> — <quoi>
CAUSE: [CLASS] <le fait constaté, avec ses valeurs>
FIX: <l'action précise qui débloque>
```

## Classes propres à cet agent

- `[DATA_ACCESS_FILTER_POST_GENERATION]`
- `[INJECTION_SUITE_MISSING]`
- `[PII_IN_INDEX]`
- `[SAFETY_EXFILTRATION_PATH]`
- `[SAFETY_HOSTILE_TO_DESTRUCTIVE]`
- `[SAFETY_PRIVILEGE_ESCALATION]`
- `[SAFETY_SCAN_UNAVAILABLE]`
- `[SAFETY_TRUST_BOUNDARY_MISSING]`
- `[SAFETY_UNTRUSTED_UNMARKED]`
- `[SECRET_LEAK]`
- `[SIDE_EFFECT_UNDECLARED]`
- `[TOOL_RETRY_UNSAFE]`
- `[TOOL_SCOPE_EXCESS]`
- `[UNBOUNDED_LOOP]`

## Classes universelles

- `[INVALID_ARG]`
- `[PACK_UNUSABLE]`
- `[STACK_MISSING]`

---

Une classe absente de cette liste ne doit pas être inventée : elle ne serait
parsée par personne, donc ne déclencherait aucune reprise et n'apparaîtrait dans
aucun tableau de bord. Émettre avec la classe la plus proche et signaler le trou.
