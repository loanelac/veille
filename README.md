# Briefing de veille

Une page web qui affiche un digest quotidien en français, rédigé par Gemini à partir de
37 flux RSS.

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
- `scripts/summarize.py` envoie ces items à l'API Gemini, qui trie, fusionne les doublons et
  rédige en français, quelle que soit la langue des sources. Aucune dépendance Python à
  installer : le script n'utilise que la bibliothèque standard.
- `index.html` lit les JSON produits. Aucune clé ne circule côté navigateur.

La clé API reste dans les secrets GitHub et n'est lue que par le workflow.

## Installation

### 1. Créer le dépôt

```bash
cd veille-app
git init && git add . && git commit -m "Briefing de veille"
gh repo create veille --public --source=. --push
```

Le dépôt doit être **public** : GitHub Pages ne sert les dépôts privés que sur les plans
payants. Aucun secret n'y figure — la clé vit dans les secrets chiffrés du dépôt.

### 2. Ajouter la clé API

Récupère une clé gratuite sur **aistudio.google.com/apikey** — pas de carte bancaire à
renseigner.

Sur `github.com/<toi>/veille` → **Settings → Secrets and variables → Actions →
New repository secret** :

- Nom : `GEMINI_API_KEY`
- Valeur : la clé

### 3. Activer Pages

**Settings → Pages → Source : Deploy from a branch**, branche `main`, dossier `/ (root)`.
L'URL est affichée après une minute.

### 4. Renseigner le dépôt dans la page

Dans `index.html`, ajuste la constante en tête de script si ton dépôt porte un autre nom :

```js
var REPO = "loanelac/veille";
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

**Zéro.** Le niveau gratuit de l'API Gemini autorise plusieurs centaines de requêtes par jour ;
ce digest en consomme **une**. Une édition pèse environ 10 000 tokens en entrée et 6 000 en
sortie, raisonnement compris.

Deux réserves à connaître : Google a déjà réduit ces quotas sans préavis, et sur le niveau
gratuit les données envoyées peuvent servir à améliorer leurs modèles — ici, des titres
d'actualité publics.

Le modèle se change via la variable d'environnement `GEMINI_MODEL` ou la constante `MODEL` en
tête de `scripts/summarize.py`. Par défaut : `gemini-3.8-flash`.

GitHub Actions est gratuit pour les dépôts publics.

## Modifier les sources

`feeds.json` liste les flux, chacun avec `group`, `name` et `url`. Ajoute ou retire une ligne,
committe : la prochaine exécution en tient compte. Vérifie qu'une URL répond avant de l'ajouter —
certains sites bloquent les clients non navigateurs.

Le fichier `veille.opml` (à la racine du dossier parent) contient la même liste
au format OPML, importable dans un lecteur RSS classique.

## Ajuster la rédaction

Les règles éditoriales sont dans la constante `PROMPT` de `scripts/summarize.py` : nombre d'items
par section, critères de tri, ton, définition des niveaux de sévérité. C'est le fichier à ouvrir
si le digest est trop large, trop bavard ou passe à côté de ce qui t'intéresse.

## Dépannage

**La page affiche « Aucun digest publié »** — le workflow n'a pas encore tourné. Lance-le
manuellement depuis Actions.

**Le workflow échoue à l'étape « Rédiger le digest »** — clé absente, invalide, ou quota
journalier atteint. Le script affiche le corps de la réponse HTTP en erreur, qui précise lequel.

**Le workflow échoue à « Récupérer les flux »** — le script sort en erreur quand aucun item n'est
récupéré, pour éviter de publier un digest vide. Les erreurs par flux sont listées dans le log et
ne bloquent pas tant qu'il reste des sources actives.

**Une source apparaît toujours en erreur** — certains sites renvoient 403 aux robots. CISA est
dans ce cas ; ses alertes sont couvertes par les autres sources.
