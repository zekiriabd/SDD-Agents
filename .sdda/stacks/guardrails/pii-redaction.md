# Guardrail: pii-redaction

Stack ID: guardrails-pii-redaction
Status: Stable
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: *

> **Hors Python** : le code de référence n'existe qu'en Python (`.sdda/templates/runtime/python/app/guardrails/`). En C#, TypeScript, Kotlin et Java, `dev-backend` l'écrit d'après cette fiche, et c'est le COMPORTEMENT décrit ici qui fait foi — mêmes règles, mêmes seuils, même événement `guardrail` (`serving/cli.md` §3.2). Rien ne le vérifie avant la revue (`review-safety`) et le jeu adversarial joué en live (G7) : c'est dit, pas supposé.

> **Code (Python)** : `.sdda/templates/runtime/python/app/guardrails/pii.py`, émis
> par `gen-app-skeleton` et **actif seulement si cette fiche est listée** sous
> `## Active Guardrails`. Couvre la ligne « haute fiabilité » du §4, validée :
> e-mail, téléphone FR et international, IBAN (mod 97), carte (Luhn), NIR (clé),
> IP v4/v6. Jetons typés et stables (`[IBAN_1]`), table de correspondance dans
> l'objet, ré-hydratation par le code (`restore`). Appliquée par `RunService` à
> l'entrée utilisateur et par `BoundedLoop` aux sorties d'outils `untrusted`,
> AVANT le modèle — donc avant la trace et le fournisseur. Les noms propres et
> adresses (NER) n'y sont pas, et ne sont pas prétendus y être.

---

## 1. Rôle

Détecter et neutraliser les données personnelles avant qu'elles n'atteignent un
endroit d'où on ne pourra plus les retirer.

**Le point qui fait tout** : la question n'est pas « faut-il rédiger les PII »
mais **« à quel moment »**. Rédiger trop tard est équivalent à ne pas rédiger.

---

## 2. Les quatre points d'application, par irréversibilité

| Point | Moment | Difficulté de retrait a posteriori |
|---|---|---|
| **Index vectoriel** | à l'ingestion | **très élevée** — un chunk supprimé laisse un embedding, et réindexer un corpus entier pour une personne est rarement fait |
| **Mémoire long terme** | à l'écriture | élevée |
| **Traces** | à l'émission du span | moyenne (rotation) mais elles partent souvent chez un tiers |
| **Prompt** | à la construction | faible — éphémère, sauf qu'il finit dans la trace et chez le fournisseur |

> Un système qui ne rédige qu'à l'affichage protège l'écran et rien d'autre.
> La donnée est déjà dans l'index, dans la trace, et chez le fournisseur de
> modèle.

---

## 3. Configuration

```yaml
MemoryPIIPolicy: redact-before-write   # forbid | redact-before-write | allow
TracePIIPolicy: redact                 # redact | hash | raw
```

| Politique | Effet | Quand |
|---|---|---|
| `forbid` | l'écriture est refusée si une PII est détectée | corpus qui ne doit contenir aucune donnée personnelle |
| `redact-before-write` | **défaut** — remplacement par un jeton avant écriture | cas général |
| `allow` | passage tel quel | **exige un ADR** nommant la base légale et la durée de conservation |

`hash` pour les traces conserve la **corrélation** (« le même utilisateur revient »)
sans conserver l'identité. C'est souvent le bon compromis pour le débogage, et
il est sous-utilisé.

---

## 4. Ce qu'on détecte, et ce qu'on rate

| Catégorie | Détection | Fiabilité |
|---|---|---|
| E-mail, téléphone, IBAN, carte bancaire, IP | motifs + somme de contrôle | **haute** — ces formats se valident |
| Numéro de sécurité sociale, identifiants nationaux | motifs par pays | haute si le pays est connu |
| Noms de personnes | NER | **moyenne** — et c'est le problème |
| Adresses postales | NER + motifs | moyenne |
| Données de santé, opinions, appartenances | contextuel | **faible** |

**Le nom propre est le cas difficile, et il est central.** Un NER produit des
faux positifs sur le vocabulaire métier (un produit qui s'appelle « Martin ») et
des faux négatifs sur les noms rares ou mal orthographiés. Un système qui
prétend rédiger tous les noms ment ; il faut le dire au client, et choisir en
conséquence entre la rédaction automatique et le cloisonnement à la source.

**La ré-identification par recoupement** échappe entièrement à ce guardrail :
« le directeur financier de l'agence de Lyon » n'est ni un nom ni un
identifiant, et désigne une personne.

---

## 5. Jetons de remplacement

Préférer des jetons **stables et typés** à une suppression :

```
Mme [PERSON_a3f] joignable au [PHONE_1] concernant [IBAN_1]
```

- **Typé** : le modèle comprend la structure de la phrase. Supprimer purement
  casse la syntaxe et dégrade la compréhension.
- **Stable dans un document** : deux occurrences de la même personne portent le
  même jeton, ce qui préserve les coréférences (« elle », « son contrat »).
- **Non réversible côté modèle** : la table de correspondance reste dans le
  système, jamais dans le prompt.

Si la réponse doit contenir la valeur réelle (envoyer un e-mail au client),
c'est le **code** qui ré-hydrate le jeton après génération — jamais le modèle.

---

## 6. Évaluation

| Métrique | Seuil | Remarque |
|---|---|---|
| `pii_leak_rate` dans l'index | **0** sur les catégories à format vérifiable | mesuré par scan déterministe (G7) |
| `pii_leak_rate` dans les traces | **0** idem | |
| `false_redaction_rate` | mesuré | un guardrail qui rédige le vocabulaire métier rend le produit inutilisable |
| couverture NER (noms) | mesurée, **jamais annoncée à 100 %** | |

Le scan de G7 (`[PII_IN_INDEX]`, `[PII_IN_DATASET]`) est déterministe et ne
couvre que les formats vérifiables. C'est un plancher, pas une garantie — et
c'est ce qu'il faut dire.

---

## 7. Pièges

- **Rédiger après l'ingestion.** L'erreur irrattrapable.
- **Oublier les datasets.** Un golden set construit depuis des conversations
  réelles contient des PII, et il est versionné dans le dépôt — parfois public.
  `[PII_IN_DATASET]` existe pour ça.
- **Oublier les messages d'erreur.** Un outil qui échoue et renvoie
  `user not found: jean.dupont@acme.fr` place la PII dans le contexte, donc dans
  la trace.
- **Croire que la rédaction remplace le cloisonnement.** Si un agent ne doit pas
  voir les données d'un autre client, le filtrage se fait **à la source** (vue
  SQL, filtre d'index), pas par rédaction. La rédaction protège contre la fuite
  accidentelle, pas contre l'accès non autorisé.
