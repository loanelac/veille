#!/usr/bin/env python3
"""Transforme les items bruts en digest rédigé en français, via l'API Claude.

Usage: python scripts/summarize.py <items.json> <daily|weekly>
Écrit data/latest.json (ou latest-weekly.json), data/archive/<date>-<kind>.json
et met à jour data/index.json.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Literal, Optional

import anthropic
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MODEL = "claude-opus-5"

SECTION_LABELS = {"ia": "Intelligence artificielle",
                  "cyber": "Cybersécurité",
                  "aero": "Airbus & aéronautique"}


class Source(BaseModel):
    name: str = Field(description="Nom court de la source, ex. 'The Record'")
    url: Optional[str] = Field(default=None, description="Lien vers l'article, si disponible")


class Item(BaseModel):
    title: str = Field(description="Titre affirmatif qui dit ce qui s'est passe")
    summary: str = Field(description="1 a 3 phrases en francais")
    severity: Optional[Literal["critical", "watch"]] = Field(
        default=None,
        description="'critical' si faille activement exploitee ou exploitation imminente, "
                    "'watch' si suivi sans urgence, null sinon")
    sources: List[Source]


class Section(BaseModel):
    key: Literal["ia", "cyber", "aero"]
    items: List[Item]


class Alert(BaseModel):
    level: str = Field(description="Ex. 'Exploitation imminente' ou 'Activement exploitee'")
    title: str
    body: str
    refs: List[str] = Field(description="Identifiants CVE le cas echeant, sinon liste vide")


class Digest(BaseModel):
    lede: str = Field(description="1 a 2 phrases sur ce qui domine reellement l'edition")
    sections: List[Section]
    alerts: List[Alert] = Field(description="Failles critiques a afficher en banniere, sinon vide")


PROMPT = """Tu rédiges une édition du briefing de veille personnel de son lecteur, qui suit \
l'intelligence artificielle, la cybersécurité et l'actualité d'Airbus.

Voici les {count} items collectés sur les dernières {hours} heures, issus de {feeds} flux RSS :

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
- severity vaut 'critical' pour une faille activement exploitée ou à exploitation imminente, \
'watch' pour ce qui demande un suivi sans urgence, et reste nul sinon. Si tout est critique, \
rien ne l'est.
- alerts ne contient que des failles réellement critiques et actionnables, avec leurs CVE. \
Liste vide si rien ne le justifie ce jour-là.
- lede : 1 à 2 phrases sur ce qui domine réellement l'édition, pas un sommaire des sections.
- Ton factuel et direct. Aucun emoji.
{weekly_note}"""

WEEKLY_NOTE = ("\n- Édition HEBDOMADAIRE : privilégie les tendances de fond et les fils qui "
               "courent sur plusieurs jours plutôt que la reprise d'actualités isolées.")


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


def main():
    items_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "items.json"
    kind = sys.argv[2] if len(sys.argv) > 2 else "daily"

    items_doc = json.loads(items_path.read_text(encoding="utf-8"))
    stats = items_doc["stats"]

    if stats["items"] == 0:
        print("Aucun item à résumer, abandon.", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        messages=[{
            "role": "user",
            "content": PROMPT.format(
                count=stats["items"],
                hours=items_doc["window_hours"],
                feeds=stats["feeds"],
                payload=build_payload(items_doc),
                weekly_note=WEEKLY_NOTE if kind == "weekly" else "",
            ),
        }],
        output_format=Digest,
    )

    digest = response.parsed_output
    now = datetime.now(timezone.utc)
    date = now.strftime("%Y-%m-%d")

    document = {
        "date": date,
        "kind": kind,
        "generatedAt": now.strftime("%d/%m à %H:%M UTC"),
        "lede": digest.lede,
        "stats": stats,
        "sections": [
            {"key": s.key,
             "label": SECTION_LABELS[s.key],
             "items": [i.model_dump(exclude_none=True) for i in s.items]}
            for s in digest.sections
        ],
        "alerts": [a.model_dump() for a in digest.alerts],
        "feedErrors": [e["name"] for e in items_doc.get("errors", [])],
    }

    DATA.mkdir(exist_ok=True)
    (DATA / "archive").mkdir(exist_ok=True)

    edition_id = f"{date}-{kind}"
    (DATA / "archive" / f"{edition_id}.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")

    latest_name = "latest.json" if kind == "daily" else "latest-weekly.json"
    (DATA / latest_name).write_text(
        json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")

    index_path = DATA / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {"editions": []}
    index["editions"] = [e for e in index["editions"] if e["id"] != edition_id]
    index["editions"].append({"id": edition_id, "date": date, "kind": kind,
                              "lede": digest.lede[:160]})
    index["editions"].sort(key=lambda e: (e["date"], e["kind"]), reverse=True)
    index["editions"] = index["editions"][:120]
    index["updated"] = now.isoformat()
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    usage = response.usage
    total_items = sum(len(s["items"]) for s in document["sections"])
    print(f"Publié {edition_id} — {total_items} items retenus sur {stats['items']}, "
          f"{len(document['alerts'])} alerte(s)")
    print(f"Tokens : {usage.input_tokens} entrée / {usage.output_tokens} sortie")


if __name__ == "__main__":
    main()
