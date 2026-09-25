<!-- GÉNÉRÉ par sdda_admin/sync_digests.py — ne pas éditer à la main.
     Source : la fiche .sdda/agents/architect-topology.md + rules/error-classification.md
     Régénérer : python .sdda/sdda.py sync-digests -->

# Digest — classes d'erreur de `architect-topology`

Tranche de `@.sdda/rules/error-classification.md` réduite à ce que cet agent
peut émettre. **Aucun bloc ERROR sans préfixe `[CLASS]`.**

Format obligatoire (cf. `rules/output-protocol.md` §3) :

```
ERROR: <qui> — <quoi>
CAUSE: [CLASS] <le fait constaté, avec ses valeurs>
FIX: <l'action précise qui débloque>
```

## Classes propres à cet agent

- `[AGENT_CONTRACT_MISSING]`
- `[ARCH_ROSTER_AGENT_IDLE]`
- `[ARCH_ROSTER_DUPLICATE_SOURCE]`
- `[ARCH_ROSTER_MUTATED]`
- `[BUDGET_EXCEEDED_ESTIMATE]`
- `[CAP_GAP]`
- `[CAP_GATE_NOT_PASSED]`
- `[HANDOFF_UNCONTRACTED]`
- `[MISSION_AMBIGUOUS]`
- `[MISSION_NOT_FOUND]`
- `[ROUTER_NO_FALLBACK]`
- `[TOOL_SCOPE_EXCESS]`
- `[TOPOLOGY_PATTERN_MISMATCH]`
- `[TOPOLOGY_SIMPLICITY_ADVISORY]`

## Classes universelles

- `[INVALID_ARG]`
- `[PACK_UNUSABLE]`
- `[STACK_MISSING]`

---

Une classe absente de cette liste ne doit pas être inventée : elle ne serait
parsée par personne, donc ne déclencherait aucune reprise et n'apparaîtrait dans
aucun tableau de bord. Émettre avec la classe la plus proche et signaler le trou.
