# TOOL CONTRACT: {n}-{tool-slug}

MISSION: {n}-{MissionName}
Status: Draft
Side Effect Class: <read-only | write-scoped | write-destructive | external-side-effect>
Trust: <trusted | untrusted>          # la SORTIE de cet outil est-elle du texte hostile ?

---

## 1. Nom et description

- **name** : `{tool_name}`  *(tel que le modèle le verra)*
- **description** :

```
<La description exposée au modèle.>
```

> ⚠️ **Cette description est du prompt engineering, pas de la documentation.**
> C'est sur elle — et sur elle seule — que le modèle décide d'appeler ou non cet
> outil. Une description vague est la cause première des « l'agent n'appelle pas
> le bon outil ». Elle est revue comme du prompt et hashée comme du prompt.
>
> Elle dit : **quand** utiliser l'outil, **quand ne pas** l'utiliser, et ce
> qu'il retourne. Pas comment il est implémenté.

## 2. Schémas

- **Entrée** :
```json
{ "type": "object", "properties": {}, "required": [] }
```
- **Sortie** :
```json
{ "type": "object", "properties": {}, "required": [] }
```

## 3. Stratégie de sûreté

> Obligatoire dès que `side_effect_class != read-only`. Invariant
> `tool-side-effect-declared`.

| Aspect | Valeur |
|---|---|
| Idempotence | <`header` \| `natural-key:{champ}` \| `none`> |
| Dry-run supporté | <oui / non> |
| Confirmation | <`never` \| `always` \| `required-above:{seuil}`> |
| Plafond par run | <ex. 1 ticket max> |
| Allowlist | <ex. transitions de statut autorisées> |
| Journalisation | <ex. tout appel tracé avec args redigés> |

> **Le retry est le piège principal.** Un agent qui réessaie un outil
> d'écriture non idempotent crée trois tickets, envoie trois e-mails, émet trois
> remboursements. Un outil non idempotent DOIT avoir `retry_policy: no-retry`,
> ou devenir idempotent.

## 4. Erreurs déclarées

| Code | Signification | Comportement attendu de l'agent |
|---|---|---|
| `NOT_FOUND` | … | <ex. informer l'utilisateur, ne pas réessayer> |
| `RATE_LIMITED` | … | backoff-then-escalate |
| `AUTH_FAILED` | … | échec explicite, jamais de contournement |
| `TIMEOUT` | … | <…> |

> Chaque erreur listée a un test de contrat en L2. Une erreur non déclarée qui
> survient en production est un trou de spécification, pas un imprévu.

## 5. Bornes techniques

| | |
|---|---|
| `timeout_s` | 10 |
| `rate_limit_rpm` | 60 |
| `retry_policy` | <`none` \| `exponential:3` — `none` si non idempotent> |
| `max_response_bytes` | <protège le budget de tokens> |

## 6. Authentification

- **Variable d'environnement** : `{ENV_VAR}`
- **Jamais** la valeur ici. `STACK.md` porte les secrets, gitignored, et ils ne
  doivent apparaître dans aucun prompt, aucun trace span, aucun dataset.

## 7. Exposé à

| Agent | CAP qui l'exige |
|---|---|
| `{n}-…` | `{n}-{m}-…` |

> Si aucune CAP ne l'exige, l'outil ne doit être câblé à personne
> (`[TOOL_SCOPE_EXCESS]`).

## 8. Tests de contrat (L2)

Fichier : `workspace/evals/suites/tool-{n}-{tool-slug}.yaml`

- [ ] happy path
- [ ] chaque erreur du §4
- [ ] timeout
- [ ] échec d'authentification
- [ ] idempotence — deux appels, même clé, un seul effet
- [ ] respect du rate limit
- [ ] **connectivité live** *(marqué `network`, seconde moitié de la TOOL GATE)*

## 9. Si `trust: untrusted`

<La sortie de cet outil est traitée comme du texte hostile (P8). Décrire
 l'encadrement : balisage, troncature, validation de schéma, et le renvoi vers
 la suite d'injection de chaque agent qui le consomme.>
