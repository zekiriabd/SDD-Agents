# Stack: ddd (archi)

Stack ID: archi-ddd
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: *
Scope: pattern d'architecture de la **coquille applicative** en couches Domaine → Application → Infrastructure → Présentation, pour une MISSION dont les **règles métier calculables sont nombreuses et coûteuses à se tromper**. Hérité de SDD_Pro `archi/ddd.md`, transposé : le domaine n'est pas persistant, il est **la partie de la MISSION qui n'est pas du jugement de modèle**. Ne s'applique pas au découpage interne du moteur agentic (imposé par l'ownership). Alternative à `archi/mvc.md` (le défaut), jamais en plus.

---

## 1. Rôle et périmètre

Une MISSION de support après-vente porte une dizaine de règles : « un retard
se compte depuis la date promise, pas depuis l'expédition », « le remboursement
est éligible sous 14 jours après livraison ou au-delà de 7 jours de retard »,
« une réclamation ouverte interdit d'en créer une seconde ». Rien de cela n'est
du langage naturel. Tout cela est une fonction, avec des entrées typées et un
résultat qu'un test L1 vérifie en une milliseconde — et **c'est exactement ce
qu'un prompt fait mal** : il l'approxime, il l'oublie sous adversité, et le
jour où la règle change, il faut la retrouver dans trois prompts.

`archi/ddd.md` existe pour ce cas : quand les règles métier de la MISSION
forment un **modèle**, avec des objets-valeurs (`OrderId`, `Money`,
`DeliveryWindow`), des invariants (« un montant remboursé ne dépasse jamais le
montant payé ») et des décisions nommées (`RefundEligibility`), on leur donne
une couche à elles, sans dépendance, que les agents **appellent** au lieu de
la réinventer.

Ce que la fiche impose, en une phrase : **le modèle décide ce qu'il faut
comprendre, le domaine calcule ce qui se calcule, et la frontière entre les
deux est un appel d'outil déterministe** — pas une consigne dans un prompt.

Hors périmètre : la persistance (il n'y en a pas par défaut ; les sources sont
en lecture seule, cf. `dataaccess/`), les événements de domaine (aucune
écriture, donc rien à publier — le jour où une MISSION écrit, c'est
`repository-tools.md` et un ADR).

---

## 2. Couches et sens des dépendances

```
Présentation  ──►  Application  ──►  Domaine  ◄──  Infrastructure
(serving/)         (app/usecases,     (app/domain/)   (tools/, data/,
                    RunService)                        retrieval/, provider)
                         │
                         └──►  Moteur (agents/, orchestration/) ──► Domaine (via outils)
```

| Couche | Contient | Dépend de | Owner |
|---|---|---|---|
| **Domaine** | objets-valeurs, invariants, services de domaine purs, **ports** (interfaces vers les données) | **rien** — stdlib du langage seulement | `dev-backend` (`app/domain/`) |
| **Application** | cas d'usage : `RunService`, construction des sorties structurées (`claim_request`), orchestration des appels au Domaine ; frontière transactionnelle si un jour il y a écriture | Domaine | `dev-backend` (`app/`) |
| **Moteur** | agents, graphe, prompts — le jugement | Domaine **par les outils** : une règle est exposée comme outil déterministe (`assess_refund_eligibility`), l'agent l'appelle, il ne la calcule pas | les `dev-*` du moteur |
| **Infrastructure** | adaptateurs : sources déclarées, vues, index, MCP, fournisseur de modèles — ils **implémentent** les ports du Domaine | Domaine, Application | `dev-data`, `dev-retrieval`, `dev-tools` |
| **Présentation** | surfaces : CLI, HTTP, MCP, batch — transport et identité | Application | `dev-api` |

Règles de dépendance, vérifiables par un analyseur d'imports en L0 :

- **Le Domaine n'importe rien** : ni le framework d'agents, ni un client HTTP,
  ni un logger, ni la config. Un import de `langchain`, `httpx`, `fastapi` dans
  `app/domain/` est une violation, pas une commodité.
- **L'Application n'a pas de règle métier** : elle séquence, elle ne décide
  pas. « Si retard > 7 jours alors éligible » dans un cas d'usage est une règle
  qui a fui.
- **La Présentation ne connaît que l'Application.**
- **L'Infrastructure implémente, elle n'invente pas** : une interface du
  Domaine (`OrderRepository.find(order_id, customer_id)`), une implémentation
  par adaptateur, l'enveloppe de sûreté dedans.

---

## 3. Le modèle de domaine, en agentic

| Composant | Rôle ici | Exemple (support après-vente) |
|---|---|---|
| **Objet-valeur** | immuable, égalité par valeur, validé à la construction | `OrderId`, `CustomerId`, `Money(eur)`, `Delay(days)` |
| **Décision** | résultat nommé d'une règle, avec **la règle citée** | `RefundEligibility(eligible, rule="BR-8", reason)` |
| **Service de domaine** | fonction pure quand aucun objet ne la porte naturellement | `assess_eligibility(order, shipment, as_of)` |
| **Port** | interface de lecture définie **dans le Domaine**, implémentée en Infrastructure | `OrdersPort`, `ShipmentsPort` |
| **Date de référence** | paramètre explicite de tout calcul temporel — jamais l'horloge | `as_of: date` (cf. `--as-of` de la CLI) |

Ce qui n'existe pas par défaut, et pourquoi : **agrégat** au sens transactionnel
(pas d'écriture), **événement de domaine** (rien à publier), **outbox** (idem).
Les introduire exige une MISSION qui écrit, donc un ADR et
`dataaccess/repository-tools.md`.

---

## 4. La frontière modèle ↔ domaine : un outil, pas une consigne

C'est le point qui distingue cette fiche de son original SDD_Pro. Une règle du
Domaine est exposée au moteur **comme un outil déterministe** :

```
tool: assess_refund_eligibility
input : { order_id, as_of }            # identité de l'appelant injectée par le runtime, jamais par le modèle
output: { eligible: bool, rule: "BR-8", reason: str, amount: Money | null }
side_effect: read-only · trust: trusted
```

Conséquences, toutes vérifiables :

- le prompt de l'agent dit **quand** appeler l'outil, jamais **comment** décider
  (`rules/prompt-authoring.md`) ;
- la règle se teste en L1 (`app/domain/tests/`), l'outil en L2 (contrat), la
  décision de l'agent d'appeler l'outil en L5 — trois niveaux, trois causes
  d'erreur distinctes ;
- le contrat de l'outil vit dans `pipeline/contracts/tools/{n}-*.tool.md` comme
  tout outil : `architect-tools` le déclare, `dev-tools` câble l'appel vers le
  Domaine, `dev-backend` écrit la fonction.

Une règle qui vit **à la fois** dans un prompt et dans le Domaine est signalée
par `review-spec` : deux vérités, et c'est celle du prompt — non testable —
qui gouverne sous adversité.

---

## 5. Mapping couche → répertoire

| Couche | Emplacement canonique |
|---|---|
| Domaine | `…/app/domain/{contexte}/` — `values.*`, `rules.*`, `ports.*` ; tests en `…/app/domain/tests/` |
| Application | `…/app/run_service.*` · `…/app/usecases/{cas}.*` |
| Infrastructure | `…/data/` · `…/retrieval/` · `…/tools/` (adaptateurs qui implémentent les ports) |
| Présentation | `…/serving/{cli,http,mcp,batch}/` |
| Composition | `…/app/composition.*` — câble les implémentations sur les ports |
| Modèle d'échange | `…/app/models.*` (généré depuis l'IR) |

Les fiches `backend/*.md` surchargent les noms selon l'écosystème (interfaces
`I*` en .NET, `Protocol` en Python, `interface` en Kotlin et TypeScript).

---

## 6. Interdits

- import d'infrastructure, de framework d'agents ou de transport dans
  `app/domain/` ;
- règle métier dans un cas d'usage, une surface, ou un prompt qui ne délègue
  pas à un outil ;
- objet-valeur mutable ;
- calcul temporel sur l'horloge système au lieu de `as_of` ;
- port défini en Infrastructure (le Domaine doit pouvoir se tester sans elle) ;
- « repository générique CRUD » : les ports disent ce dont la MISSION a besoin
  (`find_order_for_customer`), pas ce que le stockage sait faire.

---

## 7. Quand choisir `ddd`, et quand non

| Choisir `ddd` | Rester en `mvc` |
|---|---|
| ≥ 5 règles métier calculables, interdépendantes | quelques règles isolées : `app/domain/` de `mvc` suffit |
| une erreur de règle coûte de l'argent ou une décision irréversible | le coût d'erreur est une reformulation |
| les règles évoluent indépendamment des prompts | règles et prompts changent ensemble |
| plusieurs agents consomment les mêmes règles | un seul agent, une seule fois |

`ddd` coûte une couche de plus et des tests L1 en plus. Il rend en échange
**chaque règle métier testable sans LLM**, ce qui est la seule façon de savoir,
quand une eval L5 échoue, si c'est le modèle qui a mal jugé ou la règle qui est
fausse.

---

## 8. Pour les agents

- **`dev-backend`** matérialise le Domaine et l'Application depuis les règles
  métier de la MISSION (`## Business Rules`) et les contrats d'outils qui les
  exposent. Chaque règle porte son identifiant `BR-x` en commentaire et dans la
  décision qu'elle rend.
- **`architect-tools`** déclare un outil par règle exposée au moteur, avec la
  classe d'effet de bord `read-only`.
- **`dev-tools`** câble l'outil vers la fonction du Domaine — il n'écrit pas la
  règle.
- **`qa-tests`** écrit les tests L1 du Domaine : un cas par règle, un cas par
  bord (jour exact, montant nul, `as_of` antérieur à la commande).

Précédence : les idiomes de `backend/*.md` et `lang/*.md` priment sur les noms ;
le sens des dépendances du §2 prime sur tout.
