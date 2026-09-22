# Fixtures de test

`project_ok/` est un projet complet et VALIDE : G0, G1, G2 (topologie, IR,
budget) et les datasets passent au vert, sans un token.

Les autres dossiers `project_*` sont des **overlays négatifs** : ils ne
contiennent que les fichiers qui diffèrent de `project_ok/`. Le `conftest.py`
copie `project_ok/` dans un répertoire temporaire puis recouvre avec l'overlay
(`make_project(tmp_path, "project_unbounded")`). Chaque overlay casse une seule
chose, et le test vérifie la classe `[CLASS]` exacte que le validateur émet.

| Overlay | Ce qui est cassé | Classe attendue |
|---|---|---|
| `project_ac_unmeasurable` | un AC de CAP en prose, sans metric/threshold/dataset | `[AC_NOT_EVALUABLE]` |
| `project_unbounded` | le cycle classify <-> billing n'a que des arêtes « gratuites » (`-.->`) | `[UNBOUNDED_LOOP]` |
| `project_framework_leak` | `StateGraph` / LangGraph dans un contrat d'agent | `[FRAMEWORK_LEAK_IN_CONTRACT]` |
| `project_tool_scope_excess` | le classifieur porte `1-invoice-lookup`, exigé par aucune de ses CAPs | `[TOOL_SCOPE_EXCESS]` |
| `project_side_effect_undeclared` | l'outil `external-side-effect` sans stratégie de sûreté | `[SIDE_EFFECT_UNDECLARED]` |
| `project_budget_exceeded` | plafond `CostPerRunHardCapUsd` sous le pire cas | `[BUDGET_EXCEEDED_ESTIMATE]` |
| `project_holdout_overlap` | un item du holdout a le même `input` qu'un item golden | `[HOLDOUT_NOT_DISJOINT]` |
| `project_status_unbacked` | `Status: Tested` écrit à la main dans une CAP | `[STATUS_UNBACKED]` |
