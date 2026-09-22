<!-- GÉNÉRÉ par sdda_admin/sync_digests.py — ne pas éditer à la main.
     Source : la fiche .sdda/agents/review-orchestration.md + rules/error-classification.md
     Régénérer : python .sdda/python/sdda_admin/sync_digests.py -->

# Digest — classes d'erreur de `review-orchestration`

Tranche de `@.sdda/rules/error-classification.md` réduite à ce que cet agent
peut émettre. **Aucun bloc ERROR sans préfixe `[CLASS]`.**

Format obligatoire (cf. `rules/output-protocol.md` §3) :

```
ERROR: <qui> — <quoi>
CAUSE: [CLASS] <le fait constaté, avec ses valeurs>
FIX: <l'action précise qui débloque>
```

## Classes propres à cet agent

- `[BOUND_BEHAVIOR_MISMATCH]`
- `[FUSION_DEGENERATE]`
- `[HANDOFF_UNCONTRACTED]`
- `[MEASUREMENT_MISSING]`
- `[MISROUTE_TO_DESTRUCTIVE]`
- `[NODE_UNREACHED]`
- `[ORCH_OVERHEAD_HIGH]`
- `[ORCH_PING_PONG]`
- `[REFLECTION_NO_GAIN]`
- `[ROUTER_FALLBACK_UNTESTED]`
- `[TOPOLOGY_JUSTIFICATION_UNMET]`
- `[TOPOLOGY_REDUNDANT_HOP]`
- `[TRAJECTORY_DEAD_END]`
- `[TRAJECTORY_VIOLATION]`
- `[UNBOUNDED_LOOP]`

## Classes universelles

- `[INVALID_ARG]`
- `[PACK_UNUSABLE]`
- `[STACK_MISSING]`

---

Une classe absente de cette liste ne doit pas être inventée : elle ne serait
parsée par personne, donc ne déclencherait aucune reprise et n'apparaîtrait dans
aucun tableau de bord. Émettre avec la classe la plus proche et signaler le trou.
