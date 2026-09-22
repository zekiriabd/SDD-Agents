# Stack: none (dataaccess)

Stack ID: dataaccess-none
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: *
Scope: **absence déclarée** de stratégie d'accès aux données structurées. Les agents ne touchent ni base, ni fichiers de données : leurs entrées viennent des outils (`stacks/tools/*`), du corpus documentaire (`stacks/rag/*`) ou du message de l'utilisateur. Aucune dépendance, aucun fichier généré.

---

## 1. Rôle et périmètre

`none` est un **choix**, pas un trou. Le déclarer dit : « aucun agent de ce
système ne lit de données structurées », et cette phrase est vérifiable. Sans
elle, l'absence de section serait ambiguë — oubli ou décision ? — et la
TOOL GATE ne saurait pas si une enveloppe manquante est une violation ou le
comportement attendu.

Quand `none` est le bon choix :

- l'agent raisonne sur ce que l'utilisateur fournit (résumé, rédaction,
  classification d'un texte transmis) ;
- toutes les données arrivent par des outils tiers contractualisés (API, MCP) —
  ce sont alors des `TOOL`, avec leurs propres contrats, pas du DATA ACCESS ;
- le système est documentaire : le corpus est couvert par `rag/*.md`.

Quand `none` est **faux** et le dira vite : dès qu'un agent doit répondre « quel
est le statut de la commande X », il lui faut une source structurée
(`view-per-agent.md` pour une base, `declared-sources.md` pour des fichiers,
des API ou des outils MCP). Contourner en collant les données dans le prompt fait exploser
`TokenCeilingPerRun` et rend toute mesure de fraîcheur impossible.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `dataaccess-none` |
| **Famille** | DATA ACCESS · absence déclarée |
| **Paramètres STACK.md** | `DatabaseType: none` ; aucun bloc `## Active Data Sources` ; les clés `Db*` sont ignorées |
| **Dépendances** | aucune |
| **Fichiers générés** | aucun |

---

## 3. Effet sur l'IR

`dataaccess/none` ne produit **aucune entrée** dans `dataAccess[]` de l'Agentic
IR : l'absence *est* la représentation de `none`, et un tableau vide est valide.
Il n'existe donc pas de valeur `none` dans l'énumération `strategy` du schéma.

Conséquence sur la TOOL GATE (G3) : l'invariant `db-safety-envelope-present`
n'a rien à vérifier — il ne peut pas échouer, et il ne peut pas non plus être
contourné, puisqu'une entrée `dataAccess[]` apparue sans changement de STACK.md
serait une incohérence rapportée par `validate_data_access.py`.

---

## 4. Conventions imposées

1. **`DatabaseType: none`** dans `## Active Data Access`. Un `DatabaseType`
   renseigné avec `dataaccess/none` actif est `[DATA_ACCESS_INCONSISTENT]` :
   l'un des deux ment.
2. **Aucune clé `Db*` ni `Json*` effective.** Elles peuvent rester en
   commentaire dans `STACK.md` ; toute valeur active est ignorée et signalée.
3. **Un outil qui lit une base ou un fichier de données contredit cette
   fiche.** S'il en existe un, la stack active est fausse : la corriger, ne pas
   la contourner par un outil « technique ».

---

## 5. Commande de smoke

```bash
python .sdda/sdda.py validate-data-access --json
#   -> confirme : aucune stratégie active, aucune entrée dataAccess[] dans l'IR,
#      aucune clé Db*/Json* effective. exit 0.
```

---

## 6. Pièges connus

1. **`none` par oubli.** La section n'a jamais été relue et personne n'a décidé.
   Le symptôme arrive tard : un agent à qui il manque une source invente une
   réponse plausible. La TOPOLOGY GATE demande que chaque CAP nomme d'où vient
   sa donnée — c'est là que l'oubli se voit, pas ici.
2. **Le contournement par le prompt.** Coller le catalogue produit dans le
   système pour « éviter une base » : coût par run multiplié, données figées au
   jour de la rédaction, et aucune mesure de fraîcheur possible. Si la donnée
   change, elle n'a rien à faire dans un prompt.
3. **L'outil tiers qui est en fait un accès base.** Un outil MCP `sql_query`
   pointant sur la base de production n'est pas du DATA ACCESS « externe » :
   c'est `text-to-sql.md` sans enveloppe. Déclarer la bonne stack.
