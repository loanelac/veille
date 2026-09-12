# Briefing IA · Cyber · Airbus

Une page web qui affiche un digest quotidien en français sur l'intelligence artificielle, la
cybersécurité et l'actualité d'Airbus, rédigé par Claude à partir de 37 flux RSS.

GitHub Actions fait le travail chaque matin ; GitHub Pages sert la page. Rien ne tourne sur ta
machine, et le digest est lisible depuis le téléphone.

## Comment ça marche

```
                 ┌─────────────────── GitHub Actions (6h00 UTC) ───────────────────┐
   37 flux RSS ──┤  fetch_feeds.py  →  items.json  →  summarize.py  →  data/*.json  ├──┐
                 └────────────────────────────────────────────────────────────────┘  │
                                                                    commit + push ────┘
                                                                             │
                                                          GitHub Pages  ─────┴──→  index.html
```

- `scripts/fetch_feeds.py` télécharge les flux en parallèle et ne garde que les items récents.
- `scripts/summarize.py` envoie ces items à Claude, qui trie, fusionne les doublons et rédige
  en français, quelle que soit la langue des sources.
- `index.html` lit les JSON produits. Aucune clé ne circule côté navigateur.

La clé API reste dans les secrets GitHub et n'est lue que par le workflow.

## Installation

### 1. Créer le dépôt

```bash
cd veille-app
git init && git add . && git commit -m "Briefing de veille"
gh repo create veille-ia-cyber-airbus --private --source=. --push
```

Un dépôt **privé** convient : GitHub Pages sert les pages privées sur les plans payants, sinon
mets-le en public — la page ne contient aucune donnée sensible.

### 2. Ajouter la clé API

Sur `github.com/<toi>/veille-ia-cyber-airbus` → **Settings → Secrets and variables → Actions →
New repository secret** :

- Nom : `ANTHROPIC_API_KEY`
- Valeur : ta clé depuis console.anthropic.com

### 3. Activer Pages

**Settings → Pages → Source : Deploy from a branch**, branche `main`, dossier `/ (root)`.
L'URL est affichée après une minute.

### 4. Renseigner le dépôt dans la page

Dans `index.html`, ajuste la constante en tête de script si ton dépôt porte un autre nom :

```js
var REPO = "loanelac/veille-ia-cyber-airbus";
```

Elle ne sert qu'au bouton « Mettre à jour » du pied de page.

### 5. Sur l'iPhone

Ouvre l'URL Pages dans Safari → Partager → **Sur l'écran d'accueil**. La page s'ouvre alors
comme une application.

## Utilisation

**Automatique** — tous les jours à 06:00 UTC, soit 8h à l'heure d'été et 7h à l'heure d'hiver.
Le dimanche, la synthèse hebdomadaire (fenêtre de 7 jours) s'ajoute à l'édition du matin.

**À la demande** — onglet **Actions** → *Digest de veille* → **Run workflow**. Cette page
fonctionne depuis Safari mobile : c'est le bouton de mise à jour, accessible aussi depuis le
pied de la page elle-même.

## Coût

Le digest quotidien envoie environ 4 000 tokens et en produit 3 000 à 6 000.

| Modèle | Par exécution | Par mois (quotidien) |
|---|---|---|
| `claude-opus-5` (par défaut) | ~0,10-0,17 $ | **~3-5 $** |
| `claude-sonnet-5` | ~0,07 $ | ~2 $ |
| `claude-haiku-4-5` | ~0,035 $ | ~1 $ |

Pour changer, édite `MODEL` en tête de `scripts/summarize.py`. Baisser `effort` de `high` à
`medium` dans le même fichier réduit encore la part de tokens de raisonnement.

GitHub Actions est gratuit pour les dépôts publics ; en privé, le quota mensuel gratuit couvre
très largement une exécution quotidienne de deux minutes.

## Modifier les sources

`feeds.json` liste les flux, chacun avec `group`, `name` et `url`. Ajoute ou retire une ligne,
committe : la prochaine exécution en tient compte. Vérifie qu'une URL répond avant de l'ajouter —
certains sites bloquent les clients non navigateurs.

Le fichier `veille-ia-cyber-airbus.opml` (à la racine du dossier parent) contient la même liste
au format OPML, importable dans un lecteur RSS classique.

## Ajuster la rédaction

Les règles éditoriales sont dans la constante `PROMPT` de `scripts/summarize.py` : nombre d'items
par section, critères de tri, ton, définition des niveaux de sévérité. C'est le fichier à ouvrir
si le digest est trop large, trop bavard ou passe à côté de ce qui t'intéresse.

## Dépannage

**La page affiche « Aucun digest publié »** — le workflow n'a pas encore tourné. Lance-le
manuellement depuis Actions.

**Le workflow échoue à l'étape « Rédiger le digest »** — clé API absente, invalide, ou crédits
épuisés sur le compte Anthropic.

**Le workflow échoue à « Récupérer les flux »** — le script sort en erreur quand aucun item n'est
récupéré, pour éviter de publier un digest vide. Les erreurs par flux sont listées dans le log et
ne bloquent pas tant qu'il reste des sources actives.

**Une source apparaît toujours en erreur** — certains sites renvoient 403 aux robots. CISA est
dans ce cas ; ses alertes sont couvertes par les autres sources.
