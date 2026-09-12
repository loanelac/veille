#!/usr/bin/env python3
"""Transforme les items bruts en digest rédigé en français, via l'API Gemini.

Usage: python scripts/summarize.py <items.json> <daily|weekly>

Écrit data/latest.json (ou latest-weekly.json), data/archive/<date>-<kind>.json
et met à jour data/index.json.

Utilise l'API Interactions (POST /v1beta/interactions). L'ancienne forme
:generateContent renvoie désormais 404 sur les modèles récents.
Aucune dépendance : bibliothèque standard uniquement.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

# Garde-fou : nombre maximal d'éditions générées dans une même journée.
# Le quota Google est de 20 requêtes/jour sur gemini-3.8-flash au niveau sans frais
# (aistudio.google.com/rate-limit). On s'arrête volontairement à 12 : les tentatives
# qui échouent comptent aussi dans leur décompte, et surtout l'édition automatique du
# matin doit toujours trouver du quota disponible, même après une journée chargée en
# rafraîchissements manuels.
DAILY_RUN_LIMIT = int(os.environ.get("DAILY_RUN_LIMIT", "12"))

SECTION_LABELS = {"ia": "Intelligence artificielle",
                  "cyber": "Cybersécurité",
                  "aero": "Airbus & aéronautique"}

# Sous-ensemble de JSON Schema accepté par Gemini : pas d'additionalProperties.
SCHEMA = {
    "type": "object",
    "required": ["lede", "sections", "alerts"],
    "properties": {
        "lede": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["key", "items"],
                "properties": {
                    "key": {"type": "string", "enum": ["ia", "cyber", "aero"]},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["title", "summary", "sources"],
                            "properties": {
                                "title": {"type": "string"},
                                "summary": {"type": "string"},
                                "severity": {"type": "string", "enum": ["critical", "watch"]},
                                "sources": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "required": ["name"],
                                        "properties": {
                                            "name": {"type": "string"},
                                            "url": {"type": "string"},
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
        "alerts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["level", "title", "body", "refs"],
                "properties": {
                    "level": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "refs": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}

PROMPT = """Tu rédiges une édition du briefing de veille personnel de son lecteur, qui suit \
l'intelligence artificielle, la cybersécurité et l'actualité d'Airbus.

Voici les {count} items collectés sur les dernières {hours} heures, issus de {feeds} flux RSS. \
Travaille uniquement à partir de cette liste et n'invente aucun fait :

{payload}

Rédige le digest EN FRANÇAIS, quelle que soit la langue des sources. C'est le point central : \
les sources sont majoritairement anglophones, le livrable ne l'est jamais.

Règles :
- Trois sections exactement, de clés ia, cyber, aero.
- 3 à 5 items par section au maximum. Le tri EST le travail : cette liste doit ressortir en une \
douzaine d'items. Écarte le bruit — annonces produit mineures, webinaires, marronniers, offres \
d'emploi, cours de bourse.
- Fusionne les doublons : un même événement couvert par cinq sources fait UN seul item qui liste \
les cinq sources.
- Un item = un titre affirmatif disant ce qui s'est passé, puis 1 à 3 phrases. Jamais un titre \
thématique : « Check Point VPN : exploitation imminente », pas « Actualité Check Point ».
- severity vaut "critical" pour une faille activement exploitée ou à exploitation imminente, \
"watch" pour ce qui demande un suivi sans urgence. Omets le champ sinon. Si tout est critique, \
rien ne l'est.
- alerts ne contient que des failles réellement critiques et actionnables, avec leurs CVE. \
Tableau vide si rien ne le justifie ce jour-là.
- lede : 1 à 2 phrases sur ce qui domine réellement l'édition, pas un sommaire des sections.
- Ton factuel et direct. Aucun emoji.
{weekly_note}"""

WEEKLY_NOTE = ("- Édition HEBDOMADAIRE : privilégie les tendances de fond et les fils qui "
               "courent sur plusieurs jours plutôt que la reprise d'actualités isolées.\n")


def build_payload(items_doc):
    lines = []
    for source in items_doc["sources"]:
        lines.append(f"\n## {source['name']}")
        for item in source["items"]:
            stamp = item["published"][5:16].replace("T", " ")
            lines.append(f"- [{stamp}] {item['title']}")
            if item["summary"]:
                lines.append(f"    {item['summary'][:240]}")
            if item["link"]:
                lines.append(f"    {item['link']}")
    return "\n".join(lines)


def call_gemini(prompt, api_key, attempts=4):
    """Appelle l'API, en réessayant les erreurs transitoires.

    429 (débit dépassé) et 5xx (« high demand ») sont fréquents et passagers :
    un job planifié ne doit pas échouer pour ça. Les erreurs 4xx restantes
    (clé invalide, requête malformée) sont définitives, on abandonne aussitôt.
    """
    body = {
        "model": MODEL,
        "input": prompt,
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": SCHEMA,
        },
    }
    payload = json.dumps(body).encode("utf-8")
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(ENDPOINT, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            transient = exc.code == 429 or exc.code >= 500
            if transient and attempt < attempts:
                delay = 15 * attempt  # 15s, 30s, 45s
                print(f"HTTP {exc.code} (tentative {attempt}/{attempts}), "
                      f"nouvelle tentative dans {delay}s : {detail[:160]}", file=sys.stderr)
                time.sleep(delay)
                continue
            print(f"Erreur HTTP {exc.code} de l'API Gemini :\n{detail}", file=sys.stderr)
            raise SystemExit(1)
        except urllib.error.URLError as exc:
            if attempt < attempts:
                print(f"Réseau indisponible (tentative {attempt}/{attempts}) : {exc}",
                      file=sys.stderr)
                time.sleep(15 * attempt)
                continue
            print(f"Réseau indisponible : {exc}", file=sys.stderr)
            raise SystemExit(1)


def extract_text(response):
    """Le texte vit dans steps[] où type == 'model_output', sous content[].text."""
    chunks = []
    for step in response.get("steps", []):
        if step.get("type") != "model_output":
            continue
        for block in step.get("content", []):
            if block.get("text"):
                chunks.append(block["text"])
    if not chunks:
        print("Réponse sans bloc model_output exploitable :", file=sys.stderr)
        print(json.dumps(response, ensure_ascii=False)[:1200], file=sys.stderr)
        raise SystemExit(1)
    return "".join(chunks)


def parse_digest(text):
    text = text.strip()
    if text.startswith("```"):  # garde-fou si le modèle enrobe le JSON
        text = text.split("```")[1]
        text = text[4:] if text.startswith("json") else text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            print(f"Sortie non JSON :\n{text[:1000]}", file=sys.stderr)
            raise SystemExit(1)
        return json.loads(text[start:end + 1])


def load_usage(today):
    """Compteur journalier, remis à zéro au changement de date UTC."""
    path = DATA / "usage.json"
    usage = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if usage.get("date") != today:
        if usage.get("date"):
            history = usage.get("history", [])
            history.append({"date": usage["date"], "runs": usage.get("runs", 0),
                            "tokens": usage.get("tokens", 0)})
            usage["history"] = history[-30:]
        usage.update({"date": today, "runs": 0, "tokens": 0})
    usage.setdefault("history", [])
    return usage


def save_usage(usage):
    usage["limit"] = DAILY_RUN_LIMIT
    (DATA / "usage.json").write_text(
        json.dumps(usage, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    items_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "items.json"
    kind = sys.argv[2] if len(sys.argv) > 2 else "daily"

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY absente de l'environnement.", file=sys.stderr)
        raise SystemExit(1)

    items_doc = json.loads(items_path.read_text(encoding="utf-8"))
    stats = items_doc["stats"]
    if stats["items"] == 0:
        print("Aucun item à résumer, abandon.", file=sys.stderr)
        raise SystemExit(1)

    DATA.mkdir(exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    usage_doc = load_usage(today)
    if usage_doc["runs"] >= DAILY_RUN_LIMIT:
        print(f"Limite de {DAILY_RUN_LIMIT} éditions par jour déjà atteinte "
              f"({usage_doc['runs']} aujourd'hui). Rien n'est envoyé à l'API.",
              file=sys.stderr)
        raise SystemExit(1)

    prompt = PROMPT.format(
        count=stats["items"],
        hours=items_doc["window_hours"],
        feeds=stats["feeds"],
        payload=build_payload(items_doc),
        weekly_note=WEEKLY_NOTE if kind == "weekly" else "",
    )

    response = call_gemini(prompt, api_key)
    digest = parse_digest(extract_text(response))

    now = datetime.now(timezone.utc)
    date = now.strftime("%Y-%m-%d")
    document = {
        "date": date,
        "kind": kind,
        "generatedAt": now.strftime("%d/%m à %H:%M UTC"),
        "lede": digest.get("lede", ""),
        "stats": stats,
        "sections": [
            {"key": s["key"], "label": SECTION_LABELS[s["key"]], "items": s.get("items", [])}
            for s in digest.get("sections", []) if s.get("key") in SECTION_LABELS
        ],
        "alerts": digest.get("alerts", []),
        "feedErrors": [e["name"] for e in items_doc.get("errors", [])],
    }

    DATA.mkdir(exist_ok=True)
    (DATA / "archive").mkdir(exist_ok=True)
    edition_id = f"{date}-{kind}"
    serialized = json.dumps(document, ensure_ascii=False, indent=1)
    (DATA / "archive" / f"{edition_id}.json").write_text(serialized, encoding="utf-8")
    (DATA / ("latest.json" if kind == "daily" else "latest-weekly.json")).write_text(
        serialized, encoding="utf-8")

    index_path = DATA / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {"editions": []}
    index["editions"] = [e for e in index["editions"] if e["id"] != edition_id]
    index["editions"].append({"id": edition_id, "date": date, "kind": kind,
                              "lede": document["lede"][:160]})
    index["editions"].sort(key=lambda e: (e["date"], e["kind"]), reverse=True)
    index["editions"] = index["editions"][:120]
    index["updated"] = now.isoformat()
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    usage = response.get("usage", {})
    usage_doc["runs"] += 1
    usage_doc["tokens"] += usage.get("total_tokens", 0)
    usage_doc["lastRun"] = now.strftime("%d/%m à %H:%M UTC")
    save_usage(usage_doc)

    retained = sum(len(s["items"]) for s in document["sections"])
    print(f"Publié {edition_id} — {retained} items retenus sur {stats['items']}, "
          f"{len(document['alerts'])} alerte(s)")
    print(f"Tokens : {usage.get('total_input_tokens', '?')} entrée / "
          f"{usage.get('total_output_tokens', '?')} sortie / "
          f"{usage.get('total_thought_tokens', '?')} raisonnement")
    print(f"Éditions aujourd'hui : {usage_doc['runs']}/{DAILY_RUN_LIMIT}")


if __name__ == "__main__":
    main()
