#!/usr/bin/env python3
"""Notion → Hugo publikační pipeline pro zkousimtedyjsem.cz.

Příkazy:
  init --parent <notion_page_url>   Založí databázi „Blog — články" + vzorový článek
  list                              Vypíše články a jejich stav
  publish <slug|page_url> [--draft] [--mark-published]
                                    Převede článek z Notionu na Hugo bundle
  selftest                          Otestuje konvertor na vestavěné fixtuře (bez API)

Token: env NOTION_TOKEN, nebo soubor .notion-token v rootu repa (gitignored).
Vyžaduje jen Python 3 stdlib + macOS `sips` na zmenšování obrázků.
"""

import json
import re
import subprocess
import sys
import time
import unicodedata
import urllib.request
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIG_FILE = REPO / ".notion-blog.json"
TOKEN_FILE = REPO / ".notion-token"
API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
MAX_IMG_PX = 2000
SITE_URL = "https://zkousimtedyjsem.cz"

CATEGORIES = ["Pracuju", "Testuju", "Cestuju"]
STATES = ["Píšu", "K publikaci", "Publikováno"]


# ---------- API plumbing ----------

def token():
    import os
    t = os.environ.get("NOTION_TOKEN") or (
        TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else None
    )
    if not t:
        sys.exit("Chybí token: nastav NOTION_TOKEN nebo vytvoř soubor .notion-token")
    return t


def api(method, path, body=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {token()}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(float(e.headers.get("Retry-After", "1")))
                continue
            detail = e.read().decode(errors="replace")[:400]
            sys.exit(f"Notion API {e.code} na {path}: {detail}")
    return None


def page_id_from(ref):
    """Přijme URL nebo ID, vrátí UUID s pomlčkami."""
    m = re.search(r"([0-9a-f]{32})(?:[?#]|$)", ref.replace("-", ""))
    if not m:
        sys.exit(f"Nepoznávám Notion ID v: {ref}")
    h = m.group(1)
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


# ---------- rich text ----------

def rt(text, **ann):
    """Zkratka pro rich_text objekt při zápisu."""
    obj = {"type": "text", "text": {"content": text}}
    link = ann.pop("link", None)
    if link:
        obj["text"]["link"] = {"url": link}
    if ann:
        obj["annotations"] = ann
    return obj


def rich_to_md(rich):
    out = []
    for r in rich or []:
        t = r.get("plain_text", "")
        if not t:
            continue
        a = r.get("annotations", {})
        # pořadí: code nejdřív (uvnitř code se další styly nerenderují)
        if a.get("code"):
            t = f"`{t}`"
        else:
            if a.get("bold"):
                t = f"**{t}**"
            if a.get("italic"):
                t = f"*{t}*"
            if a.get("strikethrough"):
                t = f"~~{t}~~"
        if r.get("href"):
            t = f"[{t}]({r['href']})"
        out.append(t)
    return "".join(out)


# ---------- čtení bloků ----------

def fetch_children(block_id):
    results, cursor = [], None
    while True:
        q = f"/blocks/{block_id}/children?page_size=100"
        if cursor:
            q += f"&start_cursor={cursor}"
        data = api("GET", q)
        results += data["results"]
        if not data.get("has_more"):
            return results
        cursor = data["next_cursor"]


def fetch_tree(block_id):
    blocks = fetch_children(block_id)
    for b in blocks:
        if b.get("has_children"):
            b["_children"] = fetch_tree(b["id"])
        time.sleep(0.05)
    return blocks


# ---------- konverze bloků → markdown ----------

class Converter:
    def __init__(self, out_dir=None, download=True):
        self.out_dir = out_dir
        self.download = download
        self.img_n = 0
        self.warnings = []

    LIST_TYPES = ("bulleted_list_item", "numbered_list_item", "to_do")

    def convert(self, blocks, indent=0):
        lines = []
        n = 0
        prev = None
        for b in blocks:
            t = b["type"]
            # číslování běží jen v nepřerušené řadě numbered_list_item
            n = n + 1 if t == "numbered_list_item" else 0
            # seznam musí od okolního obsahu (i od jiného typu seznamu) dělit prázdný řádek
            if prev in self.LIST_TYPES and (t != prev or t == "numbered_list_item" and n == 1):
                if lines and lines[-1] != "":
                    lines.append("")
            lines += self.block(b, indent, n)
            prev = t
        if prev in self.LIST_TYPES and lines and lines[-1] != "":
            lines.append("")
        return lines

    def block(self, b, indent, n):
        t = b["type"]
        pad = "    " * indent
        d = b.get(t, {})
        md = lambda: rich_to_md(d.get("rich_text"))
        kids = b.get("_children", [])
        out = []

        if t == "paragraph":
            out = [pad + md(), ""] if md() else [""]
        elif t == "heading_1":
            out = [f"## {md()}", ""]
        elif t == "heading_2":
            out = [f"### {md()}", ""]
        elif t == "heading_3":
            out = [f"#### {md()}", ""]
        elif t == "bulleted_list_item":
            out = [f"{pad}- {md()}"] + self.convert(kids, indent + 1)
        elif t == "numbered_list_item":
            out = [f"{pad}{n}. {md()}"] + self.convert(kids, indent + 1)
        elif t == "to_do":
            box = "x" if d.get("checked") else " "
            out = [f"{pad}- [{box}] {md()}"]
        elif t == "quote":
            out = [f"> {md()}"] + [f"> {l}" for l in self.convert(kids) if l] + [""]
        elif t == "divider":
            out = ["---", ""]
        elif t == "code":
            lang = d.get("language", "")
            lang = "" if lang == "plain text" else lang
            out = [f"```{lang}", rich_to_md(d.get("rich_text")), "```", ""]
        elif t == "image":
            out = self.image(d)
        elif t == "table":
            out = self.table(kids)
        elif t == "callout":
            self.warnings.append("callout → převedeno na obyčejnou citaci")
            out = [f"> {md()}", ""]
        elif t == "toggle":
            self.warnings.append("toggle → rozbaleno do textu")
            out = [f"**{md()}**", ""] + self.convert(kids)
        elif t in ("column_list", "column"):
            self.warnings.append("sloupce → složeny pod sebe")
            out = self.convert(kids)
        elif t == "bookmark":
            url = d.get("url", "")
            out = [f"[{url}]({url})", ""]
        elif t in ("child_page", "child_database", "synced_block", "template",
                   "link_to_page", "embed", "video", "audio", "pdf", "file",
                   "equation", "table_of_contents", "breadcrumb"):
            self.warnings.append(f"blok „{t}“ se nepřenáší — přeskočeno")
        else:
            self.warnings.append(f"neznámý blok „{t}“ — přeskočeno")
        return out

    def table(self, rows):
        if not rows:
            return []
        grid = [
            [rich_to_md(c) for c in r.get("table_row", {}).get("cells", [])]
            for r in rows
        ]
        w = max(len(r) for r in grid)
        grid = [r + [""] * (w - len(r)) for r in grid]
        out = ["| " + " | ".join(grid[0]) + " |",
               "|" + "---|" * w]
        out += ["| " + " | ".join(r) + " |" for r in grid[1:]]
        return out + [""]

    def image(self, d):
        src = (d.get("file") or d.get("external") or {}).get("url", "")
        caption = rich_to_md(d.get("caption"))
        if not src:
            return []
        if not (self.download and self.out_dir):
            line = f"![{caption or 'obrázek'}]({src})"
            return [line, f"*{caption}*", ""] if caption else [line, ""]
        self.img_n += 1
        name = self.download_image(src, f"foto-{self.img_n:02d}")
        line = f"![{caption or 'foto'}]({name})"
        return [line, f"*{caption}*", ""] if caption else [line, ""]

    def download_image(self, url, stem):
        ext = re.sub(r"[?#].*", "", url).rsplit(".", 1)
        ext = ("." + ext[1].lower()) if len(ext) == 2 and len(ext[1]) <= 5 else ".jpg"
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            ext = ".jpg"
        path = self.out_dir / (stem + ext)
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req) as r, open(path, "wb") as f:
            f.write(r.read())
        if ext != ".gif":
            self.shrink(path)
        kb = path.stat().st_size // 1024
        print(f"  obrázek {path.name} ({kb} kB)")
        return path.name

    @staticmethod
    def shrink(path):
        """Zmenší fotku na MAX_IMG_PX. Nikdy nezvětšuje — sips -Z umí i upscale."""
        probe = subprocess.run(
            ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
            capture_output=True, text=True,
        )
        dims = [int(m) for m in re.findall(r"pixel(?:Width|Height):\s*(\d+)", probe.stdout)]
        if not dims:
            return
        cmd = ["sips"]
        if max(dims) > MAX_IMG_PX:
            cmd += ["-Z", str(MAX_IMG_PX)]
        cmd += ["-s", "formatOptions", "85", str(path)]
        subprocess.run(cmd, capture_output=True)


# ---------- front matter ----------

def slugify(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "clanek"


def yaml_str(s):
    return '"' + (s or "").replace('\\', '\\\\').replace('"', '\\"') + '"'


def prop_text(props, name):
    p = props.get(name, {})
    return rich_to_md(p.get("rich_text")) if p.get("type") == "rich_text" else ""


def front_matter(page, slug, draft):
    props = page["properties"]
    title = rich_to_md(next(
        p["title"] for p in props.values() if p["type"] == "title"
    ))
    cat = (props.get("Kategorie", {}).get("select") or {}).get("name")
    tags = [t["name"] for t in props.get("Tagy", {}).get("multi_select", [])]
    dt = (props.get("Datum", {}).get("date") or {}).get("start") or str(date.today())
    fm = ["---",
          f"title: {yaml_str(title)}",
          f"date: {dt[:10]}",
          f"draft: {'true' if draft else 'false'}",
          f"description: {yaml_str(prop_text(props, 'Popis'))}",
          f"summary: {yaml_str(prop_text(props, 'Summary'))}",
          f"categories: {json.dumps([cat] if cat else [], ensure_ascii=False)}",
          f"tags: {json.dumps(tags, ensure_ascii=False)}",
          "ShowToc: false"]
    return fm, title


# ---------- příkazy ----------

def cmd_init(parent_ref):
    parent = page_id_from(parent_ref)
    db = api("POST", "/databases", {
        "parent": {"type": "page_id", "page_id": parent},
        "title": [rt("Blog — články")],
        "properties": {
            "Název": {"title": {}},
            "Slug": {"rich_text": {}},
            "Kategorie": {"select": {"options": [
                {"name": c, "color": col} for c, col in
                zip(CATEGORIES, ["blue", "green", "orange"])]}},
            "Tagy": {"multi_select": {}},
            "Stav": {"select": {"options": [
                {"name": s, "color": col} for s, col in
                zip(STATES, ["gray", "yellow", "green"])]}},
            "Datum": {"date": {}},
            "Popis": {"rich_text": {}},
            "Summary": {"rich_text": {}},
            "URL": {"url": {}},
        },
    })
    CONFIG_FILE.write_text(json.dumps({"database_id": db["id"]}, indent=2))
    print(f"Databáze založena: {db.get('url')}")
    sample = api("POST", "/pages", sample_page(db["id"]))
    print(f"Vzorový článek: {sample.get('url')}")


def sample_page(db_id):
    img = "https://picsum.photos/id/1015/1200/700.jpg"
    return {
        "parent": {"database_id": db_id},
        "properties": {
            "Název": {"title": [rt("Vzorový článek — co všechno umí formátování")]},
            "Slug": {"rich_text": [rt("vzorovy-clanek")]},
            "Kategorie": {"select": {"name": "Pracuju"}},
            "Tagy": {"multi_select": [{"name": "návod"}, {"name": "blog"}]},
            "Stav": {"select": {"name": "Píšu"}},
            "Datum": {"date": {"start": str(date.today())}},
            "Popis": {"rich_text": [rt("Katalog formátování, které pipeline Notion → blog umí přenést.")]},
            "Summary": {"rich_text": [rt("Vzorový článek: všechno, co se z Notionu přenese na blog — a co ne.")]},
        },
        "children": [
            {"type": "paragraph", "paragraph": {"rich_text": [
                rt("Tenhle článek je katalog. Každý blok níž pipeline umí převést na blog. Piš normálně jako v Notionu, jen se drž těchhle bloků — a "),
                rt("na konci stránky", bold=True),
                rt(" najdeš seznam věcí, kterým se vyhnout.")]}},
            {"type": "heading_1", "heading_1": {"rich_text": [rt("Velký nadpis (H1)")]}},
            {"type": "paragraph", "paragraph": {"rich_text": [
                rt("Nadpisy: H1 je hlavní sekce článku, H2 podsekce, H3 detail. Titulek článku se bere z názvu stránky, ne z textu.")]}},
            {"type": "heading_2", "heading_2": {"rich_text": [rt("Menší nadpis (H2)")]}},
            {"type": "heading_3", "heading_3": {"rich_text": [rt("Nejmenší nadpis (H3)")]}},
            {"type": "heading_1", "heading_1": {"rich_text": [rt("Styly textu")]}},
            {"type": "paragraph", "paragraph": {"rich_text": [
                rt("Umíme "), rt("tučné", bold=True), rt(", "),
                rt("kurzívu", italic=True), rt(", "),
                rt("přeškrtnuté", strikethrough=True), rt(", "),
                rt("inline kód", code=True), rt(" a "),
                rt("odkaz", link="https://zkousimtedyjsem.cz"), rt(". "),
                rt("Podtržení", underline=True),
                rt(" se NEpřenese (markdown ho nemá) — nepoužívej ho. Barvy textu se nepřenáší taky.")]}},
            {"type": "heading_1", "heading_1": {"rich_text": [rt("Seznamy")]}},
            {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [rt("odrážka")]}},
            {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [rt("odrážka s vnořením")]},
             "children": [{"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [rt("vnořená odrážka")]}}]},
            {"type": "numbered_list_item", "numbered_list_item": {"rich_text": [rt("číslovaný seznam")]}},
            {"type": "numbered_list_item", "numbered_list_item": {"rich_text": [rt("druhá položka")]}},
            {"type": "to_do", "to_do": {"rich_text": [rt("checkbox (hodí se na packing listy)")], "checked": False}},
            {"type": "to_do", "to_do": {"rich_text": [rt("odškrtnutý checkbox")], "checked": True}},
            {"type": "heading_1", "heading_1": {"rich_text": [rt("Citace, oddělovač, kód")]}},
            {"type": "quote", "quote": {"rich_text": [rt("Citace vypadá na blogu takhle. Hodí se i na zvýraznění jedné myšlenky.")]}},
            {"type": "divider", "divider": {}},
            {"type": "code", "code": {"language": "python",
                "rich_text": [rt('print("blok kódu i se zvýrazněním syntaxe")')]}},
            {"type": "heading_1", "heading_1": {"rich_text": [rt("Obrázky")]}},
            {"type": "paragraph", "paragraph": {"rich_text": [
                rt("Fotku prostě přetáhni do stránky. Při publikaci se automaticky stáhne, "),
                rt("zmenší na rozumnou velikost", bold=True),
                rt(" a uloží k článku — nemusíš nic řešit. Popisek fotky (caption) se přenese jako popisek pod fotkou.")]}},
            {"type": "image", "image": {"type": "external", "external": {"url": img},
                "caption": [rt("Tohle je popisek fotky — napiš ho v Notionu jako caption")]}},
            {"type": "heading_1", "heading_1": {"rich_text": [rt("Tabulka")]}},
            {"type": "paragraph", "paragraph": {"rich_text": [
                rt("Jednoduchá tabulka projde (první řádek se bere jako hlavička):")]}},
            {"type": "table", "table": {"table_width": 3, "has_column_header": True,
                "has_row_header": False},
             "children": [
                {"type": "table_row", "table_row": {"cells": [[rt("Co")], [rt("Funguje?")], [rt("Poznámka")]]}},
                {"type": "table_row", "table_row": {"cells": [[rt("Tabulky")], [rt("ano")], [rt("jen jednoduché")]]}},
                {"type": "table_row", "table_row": {"cells": [[rt("Emoji 🎉")], [rt("ano")], [rt("v pohodě")]]}},
             ]},
            {"type": "heading_1", "heading_1": {"rich_text": [rt("Čemu se vyhnout")]}},
            {"type": "paragraph", "paragraph": {"rich_text": [
                rt("Tyhle bloky se na blog nepřevedou (pipeline tě na ně při publikaci upozorní): "),
                rt("callouty, toggles (rozbalovací bloky), sloupce, vložená videa a embedy, rovnice, propojené databáze", bold=True),
                rt(". A nepoužívej podtržení ani barvy textu — tiše zmizí.")]}},
            {"type": "paragraph", "paragraph": {"rich_text": [
                rt("Až budeš chtít článek vydat: přepni Stav na "),
                rt("K publikaci", bold=True),
                rt(" a napiš Claudovi „publishni vzorový článek“. Zbytek se stane sám.")]}},
        ],
    }


def db_id():
    if not CONFIG_FILE.exists():
        sys.exit("Chybí .notion-blog.json — spusť nejdřív: notion-blog.py init --parent <url>")
    return json.loads(CONFIG_FILE.read_text())["database_id"]


def find_page(ref):
    if "notion." in ref or re.fullmatch(r"[0-9a-f]{32}", ref.replace("-", "")):
        return api("GET", f"/pages/{page_id_from(ref)}")
    res = api("POST", f"/databases/{db_id()}/query", {
        "filter": {"property": "Slug", "rich_text": {"equals": ref}}})
    if not res["results"]:
        sys.exit(f"Článek se slugem „{ref}“ v databázi není.")
    return res["results"][0]


def cmd_list():
    res = api("POST", f"/databases/{db_id()}/query", {"page_size": 100})
    for p in res["results"]:
        props = p["properties"]
        title = rich_to_md(next(v["title"] for v in props.values() if v["type"] == "title"))
        stav = (props.get("Stav", {}).get("select") or {}).get("name", "?")
        slug = prop_text(props, "Slug")
        print(f"[{stav:12}] {title}  (slug: {slug or '—'})")


def cmd_publish(ref, draft=False, mark=False):
    page = find_page(ref)
    props = page["properties"]
    slug = prop_text(props, "Slug") or slugify(
        rich_to_md(next(v["title"] for v in props.values() if v["type"] == "title")))
    out_dir = REPO / "content" / "posts" / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    conv = Converter(out_dir=out_dir)
    fm, title = front_matter(page, slug, draft)

    cover_lines = []
    cover = page.get("cover")
    if cover:
        url = (cover.get("file") or cover.get("external") or {}).get("url")
        if url:
            name = conv.download_image(url, "cover")
            cover_lines = ["cover:", f"  image: {yaml_str(name)}", f"  alt: {yaml_str(title)}"]

    print(f"Stahuju bloky článku „{title}“…")
    body = conv.convert(fetch_tree(page["id"]))

    lines = fm + cover_lines + ["---", ""] + body
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).rstrip() + "\n"
    (out_dir / "index.md").write_text(text)
    print(f"Zapsáno: {out_dir / 'index.md'}")

    for w in sorted(set(conv.warnings)):
        print(f"  ⚠ {w}")
    if mark:
        api("PATCH", f"/pages/{page['id']}", {"properties": {
            "Stav": {"select": {"name": "Publikováno"}},
            "URL": {"url": f"{SITE_URL}/posts/{slug}/"}}})
        print("Stav v Notionu → Publikováno")


# ---------- selftest ----------

FIXTURE = [
    {"type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "Nadpis", "annotations": {}}]}},
    {"type": "paragraph", "paragraph": {"rich_text": [
        {"plain_text": "tučně", "annotations": {"bold": True}},
        {"plain_text": " a ", "annotations": {}},
        {"plain_text": "odkaz", "annotations": {}, "href": "https://example.com"}]}},
    {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "odrážka", "annotations": {}}]},
     "_children": [{"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "vnořená", "annotations": {}}]}}]},
    {"type": "numbered_list_item", "numbered_list_item": {"rich_text": [{"plain_text": "jedna", "annotations": {}}]}},
    {"type": "numbered_list_item", "numbered_list_item": {"rich_text": [{"plain_text": "dva", "annotations": {}}]}},
    {"type": "quote", "quote": {"rich_text": [{"plain_text": "citace", "annotations": {}}]}},
    {"type": "code", "code": {"language": "python", "rich_text": [{"plain_text": "x = 1", "annotations": {}}]}},
    {"type": "table", "table": {}, "_children": [
        {"type": "table_row", "table_row": {"cells": [[{"plain_text": "A", "annotations": {}}], [{"plain_text": "B", "annotations": {}}]]}},
        {"type": "table_row", "table_row": {"cells": [[{"plain_text": "1", "annotations": {}}], [{"plain_text": "2", "annotations": {}}]]}}]},
    {"type": "image", "image": {"external": {"url": "https://example.com/f.jpg"},
        "caption": [{"plain_text": "popisek", "annotations": {}}]}},
    {"type": "callout", "callout": {"rich_text": [{"plain_text": "pozor", "annotations": {}}]}},
]


def cmd_selftest():
    conv = Converter(download=False)
    md = "\n".join(conv.convert(FIXTURE))
    checks = ["## Nadpis", "**tučně**", "[odkaz](https://example.com)",
              "- odrážka", "    - vnořená", "1. jedna", "2. dva", "> citace",
              "```python", "| A | B |", "![popisek](https://example.com/f.jpg)",
              "> pozor"]
    missing = [c for c in checks if c not in md]
    print(md)
    print("-" * 40)
    if missing:
        sys.exit(f"SELFTEST FAILED, chybí: {missing}")
    print(f"SELFTEST OK ({len(checks)} kontrol, varování: {sorted(set(conv.warnings))})")


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    cmd = args[0]
    if cmd == "selftest":
        cmd_selftest()
    elif cmd == "init":
        cmd_init(args[args.index("--parent") + 1])
    elif cmd == "list":
        cmd_list()
    elif cmd == "publish":
        cmd_publish(args[1], draft="--draft" in args, mark="--mark-published" in args)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
