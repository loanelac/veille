#!/usr/bin/env python3
"""Transforme les items bruts en digest rédigé en français, via l'API Perplexity.

Usage: python scripts/summarize.py <items.json> <daily|weekly>

Écrit data/latest.json (ou latest-weekly.json), data/archive/<date>-<kind>.json
et met à jour data/index.json.

Utilise l'API Agent (https://api.perplexity.ai/v1/agent). L'ancienne API Sonar
(/chat/completions) est dépréciée depuis septembre 2026.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

ENDPOINT = "https://api.perplexity.ai/v1/agent"
# Modèle sans recherche web : on résume les items fournis, on n'en cherche pas d'autres.
MODEL = os.environ.get("PERPLEXITY_MODEL", "perplexity/glm-5.3")
MAX_OUTPUT_TOKENS = 8000

SECTION_LABELS = {"ia": "Intelligence artificielle",
                  "cyber": "Cybersécurité",
                  "aero": "Airbus & aéronautique"}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lede", "sections", "alerts"],
    "properties": {
        "lede": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "items"],
                "properties": {
                    "key": {"type": "string", "enum": ["ia", "cyber", "aero"]},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["title", "summary", "sources"],
                            "properties": {
                                "title": {"type": "string"},
                                "summary": {"type": "string"},
                                "severity": {"type": "string", "enum": ["critical", "watch"]},
                                "sources": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
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
                "additionalProperties": False,
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
Travaille uniquement à partir de cette liste, ne cherche rien d'autre et n'invente aucun fait :

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
{weekly_note}
Réponds uniquement par le JSON demandé."""

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


def call_perplexity(prompt, api_key):
    body = {
        "model": MODEL,
        "input": prompt,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "max_steps": 1,  # pas de recherche web : on résume ce qu'on fournit
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "digest", "schema": SCHEMA},
        },
    }
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:800]
        print(f"Erreur HTTP {exc.code} de l'API Perplexity :\n{detail}", file=sys.stderr)
        raise SystemExit(1)


def extract_text(response):
    """Récupère le texte de la réponse, quelle que soit la position du bloc message."""
    chunks = []
    for entry in response.get("output", []):
        if entry.get("type") != "message":
            continue
        for block in entry.get("content", []):
            text = block.get("text")
            if text:
                chunks.append(text)
    if not chunks:
        print("Réponse sans bloc message exploitable :", file=sys.stderr)
        print(json.dumps(response, ensure_ascii=False)[:1500], file=sys.stderr)
        raise SystemExit(1)
    return "\n".join(chunks)


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


def main():
    items_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "items.json"
    kind = sys.argv[2] if len(sys.argv) > 2 else "daily"

    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        print("PERPLEXITY_API_KEY absente de l'environnement.", file=sys.stderr)
        raise SystemExit(1)

    items_doc = json.loads(items_path.read_text(encoding="utf-8"))
    stats = items_doc["stats"]
    if stats["items"] == 0:
        print("Aucun item à résumer, abandon.", file=sys.stderr)
        raise SystemExit(1)

    prompt = PROMPT.format(
        count=stats["items"],
        hours=items_doc["window_hours"],
        feeds=stats["feeds"],
        payload=build_payload(items_doc),
        weekly_note=WEEKLY_NOTE if kind == "weekly" else "",
    )

    response = call_perplexity(prompt, api_key)
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
            {"key": s["key"], "label": SECTION_LABELS.get(s["key"], s["key"]), "items": s["items"]}
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

    retained = sum(len(s["items"]) for s in document["sections"])
    usage = response.get("usage", {})
    print(f"Publié {edition_id} — {retained} items retenus sur {stats['items']}, "
          f"{len(document['alerts'])} alerte(s)")
    if usage:
        print(f"Tokens : {usage}")


if __name__ == "__main__":
    main()
