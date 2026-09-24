# Guardrail: schema-validation

Stack ID: guardrails-schema-validation
Status: Stable
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: *

> **Le guardrail le moins cher et le plus efficace du catalogue.** Actif par
> défaut ; le désactiver demande une raison.

> **Code (Python)** : `.sdda/templates/runtime/python/app/guardrails/schema.py`,
> émis par `gen-app-skeleton`, actif si cette fiche est listée sous
> `## Active Guardrails`. Les schémas viennent de l'IR (`agents[].outputSchema`,
> et celui du nœud terminal pour la sortie finale), recopiés dans
> `app_config.json` — jamais écrits à la main. `RunService` valide la sortie
> finale : non conforme -> `AGENT_OUTPUT_INVALID` (code de sortie 9). Un
> mot-clé de schéma hors du sous-ensemble supporté est une VIOLATION, pas un
> silence : un validateur qui saute une contrainte la déclare satisfaite.

---

## 1. Rôle

Toute sortie d'agent destinée à être consommée par du code est contrainte par un
**JSON Schema** et validée avant d'être transmise.

Trois bénéfices, dont deux sont rarement mentionnés :

1. **Fiabilité** — pas de parsing défensif, pas de « le modèle a mis un
   commentaire avant le JSON ».
2. **Sécurité** — une sortie structurée limite drastiquement ce qu'une injection
   réussie peut faire sortir. Un attaquant qui contrôle le modèle ne contrôle
   pas le schéma.
3. **Évaluabilité** — un grader `schema` est déterministe et gratuit. Il répond
   à « la forme est-elle correcte » sans juge LLM, ce qui laisse le budget de
   jugement pour le fond.

---

## 2. Où l'appliquer

| Point | Obligatoire |
|---|:---:|
| Sortie d'un agent consommée par du code | **oui** |
| Sortie d'un classifieur / routeur | **oui** — `{intent: enum, confidence: number}` |
| Sortie d'une étape de pipeline | **oui** — l'erreur compose, la valider tôt la borne |
| Entrée d'un outil | **oui** — côté outil, pas côté agent |
| Réponse finale en langage naturel à un humain | non — mais l'**enveloppe** (citations, confiance, métadonnées) l'est |

---

## 3. Écrire un schéma qu'un modèle sait remplir

Un schéma trop permissif ne protège de rien ; un schéma trop rigide produit des
échecs de génération en cascade. Règles qui marchent :

- **`enum` partout où l'espace est fini.** C'est la contrainte la plus efficace :
  elle élimine la créativité là où elle nuit.
- **Interdire `additionalProperties`.** Un champ inventé est un champ que le code
  ignorera silencieusement.
- **Éviter l'imbrication profonde** (> 3 niveaux) : le taux d'échec de
  génération monte vite.
- **Nommer les champs comme le métier**, pas comme la base. `invoice_number` et
  non `inv_num_fk`. Le modèle raisonne sur les noms.
- **Prévoir l'ignorance.** Un champ obligatoire que le modèle ne peut pas
  connaître le force à inventer. Ajouter `"unknown"` à l'enum, ou rendre le champ
  nullable avec un `reason`. **C'est le point le plus souvent manqué, et c'est
  une cause directe d'hallucination.**

```jsonc
{
  "type": "object",
  "additionalProperties": false,
  "required": ["answer", "confidence", "citations"],
  "properties": {
    "answer":     { "type": "string", "maxLength": 2000 },
    "confidence": { "enum": ["high", "medium", "low", "insufficient_context"] },
    "citations":  { "type": "array", "items": { "type": "string" } },
    "unresolved": {
      "type": ["string", "null"],
      "description": "Ce que la réponse ne couvre pas, et pourquoi. null si complète."
    }
  }
}
```

`"insufficient_context"` dans l'enum de confiance donne au modèle une issue
honnête. Sans elle, il choisira `low` et répondra quand même.

---

## 4. Comportement à l'échec de validation

```yaml
OutputGuardrails: [schema-validation]
OnGuardrailTrip: block-and-log
```

| Stratégie | Quand |
|---|---|
| **Retry avec l'erreur** — renvoyer le message de validation au modèle, 1 fois | défaut. Taux de réussite élevé au second essai |
| **Bloquer** | après le retry. `[SCHEMA_VALIDATION_FAILED]` |
| **Réparer** | déconseillé : une réparation heuristique masque un schéma inadapté et rend le taux d'échec invisible |

Le retry compte dans `max_iterations` et dans le budget. Un schéma dont le taux
de retry dépasse ~10 % est un schéma à retravailler, pas un modèle à blâmer —
et la métrique doit être suivie.

---

## 5. Contrainte native vs validation a posteriori

| Approche | Fiabilité | Disponibilité |
|---|---|---|
| **Sortie structurée native** (contrainte au décodage) | la plus haute — le schéma ne *peut pas* être violé | dépend du provider |
| **Tool-use détourné** en générateur de structure | haute | large |
| **Prompt + validation a posteriori** | moyenne | universelle |

Déclarer `structured_output_fidelity` dans le provider
(`.sdda/providers/*.yaml`) et **préférer la contrainte native quand elle
existe**. La validation a posteriori reste en place de toute façon : une
contrainte native mal supportée échoue silencieusement, et c'est le validateur
qui s'en aperçoit.

---

## 6. Piège principal

**Un schéma valide ne dit rien du fond.** Une réponse parfaitement structurée
peut être entièrement fausse : `confidence: "high"` sur une information inventée
passe la validation.

Ce guardrail borne la **forme**. Le fond relève de `groundedness`, des citations
résolvables et des evals. Confondre les deux — « le schéma passe, donc c'est
bon » — est une façon courante de produire un faux vert.
