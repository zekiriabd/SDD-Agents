---
trigger: model_decision
description: "Architecture SDD_Agents, partie 4/4 (9. Taxonomie d'erreurs `[CLASS]`) — lire avant toute action du pipeline SDD_Agents"
---
<!-- GÉNÉRÉ par sdda_admin/harness_build.py depuis .sdda/ARCHITECTURE.fr.md.
     NE PAS ÉDITER ICI : toute modification est écrasée au build suivant,
     et le test de parité la signale. Éditer la source. -->

## 9. Taxonomie d'erreurs `[CLASS]`

Hérité de SDD_Pro (193 classes) : tout bloc ERROR porte un code `[CLASS]` dans son
`CAUSE:`, pour que hooks, boucles de reprise et tableaux de bord classent sans
interpréter du texte. SDD_Agents en porte **444**, liste close régénérée depuis
les émetteurs réels par `sdda_admin/sync_error_registry.py` — écrire la liste à la
main la ferait dériver dans les deux sens (`rules/error-classification.md §6`).
Le chiffre ci-dessus est lui-même régénéré (`sync-counters`), pas recopié.
Familles propres à SDD_Agents :

`[MISSION_*]` · `[CAP_*]` · `[TOPOLOGY_*]` · `[AGENT_*]` · `[TOOL_*]` ·
`[RETRIEVAL_*]` · `[MEMORY_*]` · `[PROMPT_*]` · `[EVAL_*]` · `[JUDGE_*]` ·
`[BUDGET_*]` · `[SAFETY_*]` · `[TRACE_*]` · `[API_*]`

**Une classe citée ici doit avoir un émetteur.** `sync_error_registry.py`
régénère le registre depuis les émetteurs **réels** — donc une classe qui ne
vit que dans ce document n'y entre jamais, et le registre se déclare « à jour »
sans elle. C'est ainsi que `[API_CONTRACT_DRIFT]` et `[API_ROUTE_UNBACKED]` ont
pu être annoncés bloquants au §4 pendant tout un lot sans qu'aucun script ne
les émette. Le contrôle `errors.documented` de `framework_smoke.py` ferme cette
porte : toute classe citée dans la prose normative (`.sdda/*.md`,
`.sdda/docs/*.md`, jumeaux `.fr.md` compris) et émise par rien est un
**échec**, pas un avertissement. `docs.parity` ajoute la règle des jumeaux : une
page et son `.fr.md` citent les mêmes classes, portent le même nombre de titres
par niveau, les mêmes marqueurs de compteur et les mêmes commandes.

---

## 10. Ce que SDD_Agents ne fera pas

Déclaré d'entrée, pour que la promesse reste tenable :

- **Pas d'entraînement ni de fine-tuning.** Le framework compose des modèles
  existants ; il ne produit pas de poids.
- **Pas de garantie de correction du produit généré.** Il garantit que le produit
  a été *mesuré* contre des seuils déclarés, sur des jeux déclarés. Un seuil trop
  bas reste un seuil trop bas.
- **Pas d'hébergement ni d'exploitation.** Il produit du code, des evals et de la
  CI ; il ne fait pas tourner la production.
- **Pas de choix de modèle à votre place sur des critères qu'il ne mesure pas.**
  Les tiers sont déclarés, la résolution appartient au provider.
