#!/usr/bin/env python3
"""Notion → Hugo publikační pipeline pro zkousimtedyjsem.cz.

Příkazy:
  init --parent <notion_page_url>   Založí databázi „Blog — články" + vzorový článek
  sample                            Vytvoří vzorový článek v existující databázi
  list                              Vypíše články a jejich stav
  publish <slug|page_url> [--draft] [--mark-published]
                                    Převede článek z Notionu na Hugo bundle
  publish-ready --output <soubor>   Převede články „K publikaci“ a stáhne články „Ke stažení“
  mark-deployed <soubor>             Po nasazení aktualizuje stav článků v Notionu
  selftest                          Otestuje konvertor na vestavěné fixtuře (bez API)

Token: env NOTION_TOKEN, nebo soubor .notion-token v rootu repa (gitignored).
Vyžaduje Python 3. Pro zmenšování obrázků používá Pillow; na macOS funguje
i záložní varianta přes `sips`.
"""

import json
import re
import shutil
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
NOTION_VERSION = "2026-03-11"
MAX_IMG_PX = 2000
TITLE_LIMIT = 60   # nad 60 znaků se titulek na webu láme na tři řádky
NOTE_EMOJI = "🚧"  # callout s touto ikonou = soukromá poznámka, nepublikuje se
SITE_URL = "https://zkousimtedyjsem.cz"

CATEGORIES = ["Pracuju", "Testuju", "Cestuju"]
STATES = ["Píšu", "K publikaci", "Publikováno", "Ke stažení", "Staženo", "Chyba publikace"]


class PublicationError(Exception):
    """Chyba, kterou lze bezpečně zapsat k článku v Notionu."""


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
        # prázdný řádek za seznamem jen na nejvyšší úrovni — uvnitř by seznam rozpůlil
        if indent == 0 and prev in self.LIST_TYPES and lines and lines[-1] != "":
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
            if (d.get("icon") or {}).get("emoji") == NOTE_EMOJI:
                # soukromá poznámka — na web jde jako HTML komentář, tedy neviditelně
                out = [f"<!-- {md()} -->", ""]
            else:
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
        """Zmenší fotku bez zvětšování; funguje lokálně i na Linux runneru."""
        try:
            from PIL import Image
        except ImportError:
            Image = None

        if Image:
            with Image.open(path) as image:
                if max(image.size) <= MAX_IMG_PX:
                    return
                image.thumbnail((MAX_IMG_PX, MAX_IMG_PX), Image.Resampling.LANCZOS)
                save_args = {"optimize": True}
                if image.format in ("JPEG", "WEBP"):
                    save_args["quality"] = 85
                image.save(path, format=image.format, **save_args)
            return

        # Lokální záloha pro Mac, kde Pillow ještě není nainstalované.
        sips = shutil.which("sips")
        if not sips:
            raise PublicationError(
                "Chybí Pillow pro zmenšení obrázku. Nainstaluj Pillow nebo spusť publikaci v GitHub Actions."
            )
        probe = subprocess.run(
            [sips, "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
            capture_output=True, text=True, check=False,
        )
        dims = [int(m) for m in re.findall(r"pixel(?:Width|Height):\s*(\d+)", probe.stdout)]
        if dims and max(dims) > MAX_IMG_PX:
            subprocess.run([sips, "-Z", str(MAX_IMG_PX), "-s", "formatOptions", "85", str(path)],
                           capture_output=True, check=True)


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


def prop_url(props, name):
    p = props.get(name, {})
    return p.get("url") if p.get("type") == "url" else None


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
        "initial_data_source": {"properties": {
            "Název": {"title": {}},
            "Slug": {"rich_text": {}},
            "Kategorie": {"select": {"options": [
                {"name": c, "color": col} for c, col in
                zip(CATEGORIES, ["blue", "green", "orange"])]}},
            "Tagy": {"multi_select": {}},
            "Stav": {"select": {"options": [
                {"name": s, "color": col} for s, col in
                zip(STATES, ["gray", "yellow", "green", "orange", "brown", "red"])]}},
            "Datum": {"date": {}},
            "Popis": {"rich_text": {}},
            "Summary": {"rich_text": {}},
            "URL": {"url": {}},
        }},
    })
    sources = db.get("data_sources", [])
    if len(sources) != 1:
        raise PublicationError("Nově vytvořená Notion databáze nemá právě jeden zdroj dat.")
    CONFIG_FILE.write_text(json.dumps({"database_id": db["id"],
                                       "data_source_id": sources[0]["id"]}, indent=2))
    print(f"Databáze založena: {db.get('url')}")
    cmd_sample()


def cmd_sample():
    """Vytvoří vzorový článek v existující databázi."""
    sample = api("POST", "/pages", sample_page(data_source_id()))
    print(f"Vzorový článek: {sample.get('url')}")


def sample_page(source_id):
    img = "https://picsum.photos/id/1015/1200/700.jpg"
    return {
        "parent": {"type": "data_source_id", "data_source_id": source_id},
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
            {"type": "bulleted_list_item", "bulleted_list_item": {
                "rich_text": [rt("odrážka s vnořením")],
                "children": [{"type": "bulleted_list_item", "bulleted_list_item": {
                    "rich_text": [rt("vnořená odrážka")]}}]}},
            {"type": "numbered_list_item", "numbered_list_item": {"rich_text": [rt("číslovaný seznam")]}},
            {"type": "numbered_list_item", "numbered_list_item": {"rich_text": [rt("druhá položka")]}},
            {"type": "to_do", "to_do": {"rich_text": [rt("checkbox (hodí se na packing listy)")], "checked": False}},
            {"type": "to_do", "to_do": {"rich_text": [rt("odškrtnutý checkbox")], "checked": True}},
            {"type": "heading_1", "heading_1": {"rich_text": [rt("Citace, oddělovač, kód")]}},
            {"type": "quote", "quote": {"rich_text": [rt("Citace vypadá na blogu takhle. Hodí se i na zvýraznění jedné myšlenky.")]}},
            {"type": "divider", "divider": {}},
            {"type": "code", "code": {"language": "python",
                "rich_text": [rt('doplnky = ["hořčík", "vitamín D", "omega-3"]\n'
                                 'for d in doplnky:\n'
                                 '    print(f"ráno: {d}")')]}},
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
            {"type": "table", "table": {
                "table_width": 3, "has_column_header": True, "has_row_header": False,
                "children": [
                    {"type": "table_row", "table_row": {"cells": [[rt("Co")], [rt("Funguje?")], [rt("Poznámka")]]}},
                    {"type": "table_row", "table_row": {"cells": [[rt("Tabulky")], [rt("ano")], [rt("jen jednoduché")]]}},
                    {"type": "table_row", "table_row": {"cells": [[rt("Emoji 🎉")], [rt("ano")], [rt("v pohodě")]]}},
                ]}},
            {"type": "heading_1", "heading_1": {"rich_text": [rt("Soukromé poznámky")]}},
            {"type": "paragraph", "paragraph": {"rich_text": [
                rt("Potřebuješ si v článku nechat vzkaz pro sebe („sem doplnit číslo“)? Udělej "),
                rt("callout s ikonou 🚧", bold=True),
                rt(" (napiš /callout a klikni na ikonu). Takový blok se na web nepřenese — zůstane jen ve zdrojovém kódu stránky jako neviditelný komentář. Callout s jakoukoli jinou ikonou se publikuje jako citace.")]}},
            {"type": "callout", "callout": {
                "icon": {"type": "emoji", "emoji": NOTE_EMOJI},
                "rich_text": [rt("Takhle vypadá poznámka, kterou čtenář nikdy neuvidí.")]}},
            {"type": "heading_1", "heading_1": {"rich_text": [rt("Čemu se vyhnout")]}},
            {"type": "paragraph", "paragraph": {"rich_text": [
                rt("Tyhle bloky se na blog nepřevedou (pipeline tě na ně při publikaci upozorní): "),
                rt("callouty, toggles (rozbalovací bloky), sloupce, vložená videa a embedy, rovnice, propojené databáze", bold=True),
                rt(". A nepoužívej podtržení ani barvy textu — tiše zmizí.")]}},
            {"type": "paragraph", "paragraph": {"rich_text": [
                rt("Až budeš chtít článek vydat: přepni Stav na "),
                rt("K publikaci", bold=True),
                rt(" a v GitHub Actions spusť workflow „Publikovat blog z Notionu“. Zbytek se stane sám.")]}},
        ],
    }


def db_config():
    if not CONFIG_FILE.exists():
        sys.exit("Chybí .notion-blog.json — spusť nejdřív: notion-blog.py init --parent <url>")
    return json.loads(CONFIG_FILE.read_text())


def db_id():
    return db_config()["database_id"]


def data_source_id():
    """Vrátí datový zdroj; starší konfiguraci doplní za běhu bez zápisu do repa."""
    config = db_config()
    if config.get("data_source_id"):
        return config["data_source_id"]
    database = api("GET", f"/databases/{config['database_id']}")
    sources = database.get("data_sources", [])
    if len(sources) != 1:
        raise PublicationError(
            "Databáze musí mít právě jeden data source; jeho ID ulož do .notion-blog.json."
        )
    return sources[0]["id"]


def query_pages(body):
    """Načte všechny stránky z jediného zdroje dat včetně dalších stránek výsledků."""
    results, cursor = [], None
    while True:
        request = {**body, "page_size": 100}
        if cursor:
            request["start_cursor"] = cursor
        data = api("POST", f"/data_sources/{data_source_id()}/query", request)
        results += data["results"]
        if not data.get("has_more"):
            return results
        cursor = data["next_cursor"]


def find_page(ref):
    if "notion." in ref or re.fullmatch(r"[0-9a-f]{32}", ref.replace("-", "")):
        return api("GET", f"/pages/{page_id_from(ref)}")
    results = query_pages({"filter": {"property": "Slug", "rich_text": {"equals": ref}}})
    if not results:
        sys.exit(f"Článek se slugem „{ref}“ v databázi není.")
    return results[0]


def cmd_list():
    for p in query_pages({}):
        props = p["properties"]
        title = rich_to_md(next(v["title"] for v in props.values() if v["type"] == "title"))
        stav = (props.get("Stav", {}).get("select") or {}).get("name", "?")
        slug = prop_text(props, "Slug")
        print(f"[{stav:12}] {title}  (slug: {slug or '—'})")


def page_title(page):
    return rich_to_md(next(p["title"] for p in page["properties"].values()
                           if p["type"] == "title"))


def validation_errors(page):
    """Kontroly, které nesmějí pustit neúplný článek do produkce."""
    props = page["properties"]
    errors = []
    title = page_title(page).strip()
    slug = prop_text(props, "Slug").strip()
    category = (props.get("Kategorie", {}).get("select") or {}).get("name")
    date_value = (props.get("Datum", {}).get("date") or {}).get("start")
    if not title:
        errors.append("chybí Název")
    if not slug:
        errors.append("chybí Slug")
    elif not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        errors.append("Slug smí obsahovat jen malá písmena, čísla a pomlčky")
    if category not in CATEGORIES:
        errors.append("Kategorie musí být Pracuju, Testuju nebo Cestuju")
    if not date_value:
        errors.append("chybí Datum")
    if not prop_text(props, "Popis").strip():
        errors.append("chybí Popis")
    if not prop_text(props, "Summary").strip():
        errors.append("chybí Summary")
    return errors


def ensure_valid_page(page):
    errors = validation_errors(page)
    slug = prop_text(page["properties"], "Slug").strip()
    if slug:
        same_slug = query_pages({"filter": {"property": "Slug", "rich_text": {"equals": slug}}})
        if len(same_slug) != 1 or same_slug[0]["id"] != page["id"]:
            errors.append(f"Slug „{slug}“ není unikátní")
    if errors:
        raise PublicationError("; ".join(errors))


def publish_page(page, draft=False):
    ensure_valid_page(page)
    props = page["properties"]
    slug = prop_text(props, "Slug")
    out_dir = REPO / "content" / "posts" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    # smazat fotky z minulé publikace, ať v článku nezůstanou ty, které už v Notionu nejsou
    for old in list(out_dir.glob("foto-*.*")) + list(out_dir.glob("cover.*")):
        old.unlink()

    conv = Converter(out_dir=out_dir)
    fm, title = front_matter(page, slug, draft)

    cover_lines = []
    cover = page.get("cover")
    if cover:
        url = (cover.get("file") or cover.get("external") or {}).get("url")
        if url:
            name = conv.download_image(url, "cover")
            cover_lines = ["cover:", f"  image: {yaml_str(name)}", f"  alt: {yaml_str(title)}"]

    if len(title) > TITLE_LIMIT:
        print(f"  ⚠ titulek má {len(title)} znaků (doporučeno do {TITLE_LIMIT}) — "
              f"na webu se zalomí na tři řádky")
    print(f"Stahuju bloky článku „{title}“…")
    body = conv.convert(fetch_tree(page["id"]))

    lines = fm + cover_lines + ["---", ""] + body
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).rstrip() + "\n"
    (out_dir / "index.md").write_text(text)
    print(f"Zapsáno: {out_dir / 'index.md'}")

    for w in sorted(set(conv.warnings)):
        print(f"  ⚠ {w}")
    return {"id": page["id"], "slug": slug, "title": title, "action": "published"}


def withdrawal_slugs(page):
    """Vrátí složky, které má bezpečně odstranit požadavek „Ke stažení“.

    URL uchovává poslední nasazený slug. Díky tomu článek skutečně zmizí i tehdy,
    když byl slug v Notionu omylem změněn před jeho stažením.
    """
    props = page["properties"]
    slugs = {prop_text(props, "Slug").strip()}
    url = prop_url(props, "URL") or ""
    match = re.fullmatch(re.escape(SITE_URL) + r"/posts/([a-z0-9]+(?:-[a-z0-9]+)*)/?", url)
    if match:
        slugs.add(match.group(1))
    invalid = [slug for slug in slugs if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug)]
    if invalid:
        raise PublicationError("pro stažení je potřeba platný Slug")
    return sorted(slugs)


def withdraw_page(page):
    removed = []
    for slug in withdrawal_slugs(page):
        out_dir = REPO / "content" / "posts" / slug
        if out_dir.is_dir():
            shutil.rmtree(out_dir)
            removed.append(slug)
    title = page_title(page)
    if removed:
        print(f"Staženo z webu: {title} ({', '.join(removed)})")
    else:
        print(f"Článek už na webu není: {title}")
    return {"id": page["id"], "slug": prop_text(page["properties"], "Slug").strip(),
            "title": title, "action": "withdrawn"}


def mark_deployed(items):
    for item in items:
        if item["action"] == "published":
            props = {
                "Stav": {"select": {"name": "Publikováno"}},
                "URL": {"url": f"{SITE_URL}/posts/{item['slug']}/"},
            }
            status = "Publikováno"
        elif item["action"] == "withdrawn":
            props = {
                "Stav": {"select": {"name": "Staženo"}},
                "URL": {"url": None},
            }
            status = "Staženo"
        else:
            raise PublicationError(f"Neznámá změna článku v manifestu: {item['action']}")
        api("PATCH", f"/pages/{item['id']}", {"properties": props})
        print(f"Stav v Notionu → {status}: {item['title']}")


def mark_failed(page, error):
    """Zapíše stav, který Marie přidala do databáze; obsah chyby je v logu Actions."""
    api("PATCH", f"/pages/{page['id']}", {"properties": {
        "Stav": {"select": {"name": "Chyba publikace"}}}})
    print(f"Stav v Notionu → Chyba publikace: {page_title(page)} ({error})", file=sys.stderr)


def cmd_publish(ref, draft=False, mark=False):
    item = publish_page(find_page(ref), draft=draft)
    if mark:
        mark_deployed([item])


def cmd_publish_ready(output_path):
    ready = query_pages({"filter": {"property": "Stav", "select": {"equals": "K publikaci"}}})
    withdrawn = query_pages({"filter": {"property": "Stav", "select": {"equals": "Ke stažení"}}})
    if not ready and not withdrawn:
        print("Žádné články k publikaci ani stažení.")
        Path(output_path).write_text("[]\n")
        return

    changes, failures = [], []
    for page in ready:
        try:
            changes.append(publish_page(page))
        except Exception as error:
            failures.append(f"{page_title(page)}: {error}")
            try:
                mark_failed(page, str(error))
            except Exception as mark_error:
                failures[-1] += f" (nepodařilo se zapsat stav chyby: {mark_error})"
    for page in withdrawn:
        try:
            changes.append(withdraw_page(page))
        except Exception as error:
            failures.append(f"{page_title(page)}: {error}")
    Path(output_path).write_text(json.dumps(changes, ensure_ascii=False, indent=2) + "\n")
    if failures:
        raise PublicationError("Publikace nebo stažení selhalo:\n- " + "\n- ".join(failures))
    print(f"Připraveno k nasazení: {len(changes)} změn článků.")


def cmd_mark_deployed(manifest_path):
    items = json.loads(Path(manifest_path).read_text())
    if not items:
        print("Není co označit jako nasazené.")
        return
    mark_deployed(items)


# ---------- import: markdown → Notion ----------

INLINE_RE = re.compile(
    r"(?P<code>`[^`]+`)"
    r"|(?P<link>\[[^\]]+\]\([^)]+\))"
    r"|(?P<bold>\*\*[^*]+\*\*)"
    r"|(?P<strike>~~[^~]+~~)"
    r"|(?P<italic>\*[^*\n]+\*)"
)


def md_inline(text):
    """Markdown řádek → Notion rich_text."""
    out, pos = [], 0
    for m in INLINE_RE.finditer(text):
        if m.start() > pos:
            out += chunked(text[pos:m.start()])
        kind = m.lastgroup
        raw = m.group()
        if kind == "code":
            out.append(rt(raw[1:-1], code=True))
        elif kind == "bold":
            out.append(rt(raw[2:-2], bold=True))
        elif kind == "strike":
            out.append(rt(raw[2:-2], strikethrough=True))
        elif kind == "italic":
            out.append(rt(raw[1:-1], italic=True))
        elif kind == "link":
            label, url = re.match(r"\[([^\]]+)\]\(([^)]+)\)", raw).groups()
            out.append(rt(label, link=url))
        pos = m.end()
    if pos < len(text):
        out += chunked(text[pos:])
    return out


def chunked(text, limit=2000):
    """Notion má limit 2000 znaků na jeden rich_text prvek."""
    return [rt(text[i:i + limit]) for i in range(0, len(text), limit)] or []


def blk(kind, rich, **extra):
    return {"type": kind, kind: {"rich_text": rich, **extra}}


def md_to_blocks(md, warnings):
    """Tělo markdownu → seznam Notion bloků (vnořené uvnitř payloadu)."""
    lines = md.split("\n")
    blocks, i = [], 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()

        if not line:
            i += 1
            continue

        # HTML komentář (poznámky a TODO) — zachovat jako odstavec, ať se nic neztratí
        if line.startswith("<!--"):
            buf = []
            while i < len(lines):
                buf.append(lines[i])
                if "-->" in lines[i]:
                    break
                i += 1
            i += 1
            txt = re.sub(r"^<!--|-->$", "", "\n".join(buf).strip()).strip()
            if txt:
                blocks.append(blk("callout", chunked(txt),
                                  icon={"type": "emoji", "emoji": NOTE_EMOJI}))
            continue

        # blok kódu
        if line.startswith("```"):
            lang = line[3:].strip() or "plain text"
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            blocks.append(blk("code", chunked("\n".join(buf)), language=lang))
            continue

        # tabulka
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                if not re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i]):
                    cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                    rows.append([md_inline(c) for c in cells])
                i += 1
            width = max(len(r) for r in rows)
            rows = [r + [[]] * (width - len(r)) for r in rows]
            blocks.append({"type": "table", "table": {
                "table_width": width, "has_column_header": True, "has_row_header": False,
                "children": [{"type": "table_row", "table_row": {"cells": r}} for r in rows]}})
            continue

        if re.fullmatch(r"-{3,}", line):
            blocks.append({"type": "divider", "divider": {}})
            i += 1
            continue

        m = re.match(r"^(#{2,4})\s+(.*)", line)
        if m:
            level = {2: "heading_1", 3: "heading_2", 4: "heading_3"}[len(m.group(1))]
            blocks.append(blk(level, md_inline(m.group(2))))
            i += 1
            continue

        if line.startswith("> "):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            blocks.append(blk("quote", md_inline(" ".join(b for b in buf if b))))
            continue

        m = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)", line)
        if m:
            alt, src = m.groups()
            if src.startswith("http"):
                blocks.append({"type": "image", "image": {
                    "type": "external", "external": {"url": src},
                    "caption": md_inline(alt) if alt else []}})
            else:
                warnings.append(f"lokální obrázek {src} — nahraj ho do Notionu ručně")
                blocks.append(blk("paragraph", chunked(f"[obrázek k doplnění: {src} — {alt}]")))
            i += 1
            # popisek pod fotkou už je v caption — nedělat z něj ještě odstavec
            j = i
            while j < len(lines) and not lines[j].strip():
                j += 1
            if alt and j < len(lines) and lines[j].strip() == f"*{alt}*":
                i = j + 1
            continue

        indent = (len(raw) - len(raw.lstrip(" "))) // 4
        m = re.match(r"^- \[([ xX])\]\s+(.*)", line)
        if m:
            blocks.append(blk("to_do", md_inline(m.group(2)),
                              checked=m.group(1).lower() == "x"))
            i += 1
            continue
        m = re.match(r"^[-*]\s+(.*)", line)
        if m:
            add_list_item(blocks, indent, blk("bulleted_list_item", md_inline(m.group(1))))
            i += 1
            continue
        m = re.match(r"^\d+\.\s+(.*)", line)
        if m:
            add_list_item(blocks, indent, blk("numbered_list_item", md_inline(m.group(1))))
            i += 1
            continue

        # odstavec: spojit navazující řádky
        buf = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
                r"^\s*(#{2,4}\s|[-*]\s|\d+\.\s|>|```|\||!\[|<!--|-{3,}$)", lines[i]):
            buf.append(lines[i].strip())
            i += 1
        blocks.append(blk("paragraph", md_inline(" ".join(buf))))
    return blocks


def add_list_item(blocks, indent, item):
    """Odsazenou položku pověsí pod poslední položku o úroveň výš."""
    if indent and blocks:
        parent = blocks[-1]
        for _ in range(indent - 1):
            kids = parent[parent["type"]].get("children")
            if not kids:
                break
            parent = kids[-1]
        payload = parent[parent["type"]]
        payload.setdefault("children", []).append(item)
    else:
        blocks.append(item)


def parse_front_matter(text):
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    fm, body = {}, m.group(2)
    for line in m.group(1).split("\n"):
        km = re.match(r"^(\w+):\s*(.*)$", line)
        if not km:
            continue
        key, val = km.group(1), km.group(2).strip()
        if val.startswith("[") and val.endswith("]"):
            fm[key] = [v.strip().strip('"\'') for v in val[1:-1].split(",") if v.strip()]
        else:
            fm[key] = val.strip('"\'')
    return fm, body


def cmd_import(path_str, category=None):
    path = Path(path_str)
    if not path.is_absolute():
        path = REPO / path
    fm, body = parse_front_matter(path.read_text())
    slug = path.parent.name if path.name == "index.md" else path.stem
    cat = category or (fm.get("categories") or [None])[0]
    if cat not in CATEGORIES:
        if cat:
            print(f"  ⚠ kategorie „{cat}“ není Pracuju/Testuju/Cestuju — nechávám prázdnou")
        cat = None

    warnings = []
    blocks = md_to_blocks(body, warnings)

    props = {
        "Název": {"title": [rt(fm.get("title", slug))]},
        "Slug": {"rich_text": [rt(slug)]},
        "Tagy": {"multi_select": [{"name": t} for t in fm.get("tags", [])]},
        "Stav": {"select": {"name": "Píšu"}},
        "Popis": {"rich_text": chunked(fm.get("description", ""))},
        "Summary": {"rich_text": chunked(fm.get("summary", ""))},
    }
    if cat:
        props["Kategorie"] = {"select": {"name": cat}}
    if fm.get("date"):
        props["Datum"] = {"date": {"start": fm["date"][:10]}}

    page = api("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": data_source_id()},
                                  "properties": props, "children": blocks[:100]})
    for start in range(100, len(blocks), 100):
        api("PATCH", f"/blocks/{page['id']}/children",
            {"children": blocks[start:start + 100]})
    print(f"Importováno „{fm.get('title', slug)}“ ({len(blocks)} bloků): {page.get('url')}")
    for w in sorted(set(warnings)):
        print(f"  ⚠ {w}")
    return page


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


ROUNDTRIP_MD = """## Nadpis

Odstavec s **tučným**, *kurzívou*, ~~přeškrtnutým~~, `kódem` a [odkazem](https://example.com).

### Podnadpis

- odrážka
    - vnořená odrážka
- druhá odrážka

1. jedna
2. dva

- [ ] nehotovo
- [x] hotovo

> citace

---

```python
x = 1
```

| A | B |
|---|---|
| 1 | 2 |

![popisek](https://example.com/f.jpg)
*popisek*

<!-- soukromá poznámka, na web se nedostane -->
"""


def write_to_read(block):
    """Zápisový tvar bloku → čtecí tvar (vnořené bloky vedle payloadu)."""
    t = block["type"]
    payload = dict(block.get(t, {}))
    for key in ("rich_text", "caption"):
        if key in payload:
            payload[key] = [{"plain_text": r["text"]["content"],
                             "annotations": r.get("annotations", {}),
                             **({"href": r["text"]["link"]["url"]}
                                if r["text"].get("link") else {})}
                            for r in payload[key]]
    if t == "table_row":
        payload["cells"] = [[{"plain_text": c["text"]["content"],
                              "annotations": c.get("annotations", {})} for c in cell]
                            for cell in payload["cells"]]
    kids = payload.pop("children", None)
    out = {"type": t, t: payload}
    if kids:
        out["_children"] = [write_to_read(k) for k in kids]
    return out


def cmd_selftest():
    conv = Converter(download=False)
    md = "\n".join(conv.convert(FIXTURE))
    checks = ["## Nadpis", "**tučně**", "[odkaz](https://example.com)",
              "- odrážka", "    - vnořená", "1. jedna", "2. dva", "> citace",
              "```python", "| A | B |", "![popisek](https://example.com/f.jpg)",
              "> pozor"]
    missing = [c for c in checks if c not in md]
    if missing:
        sys.exit(f"SELFTEST FAILED (Notion→md), chybí: {missing}")
    print(f"Notion→markdown OK ({len(checks)} kontrol)")

    # round trip: markdown → Notion bloky → markdown
    warnings = []
    blocks = [write_to_read(b) for b in md_to_blocks(ROUNDTRIP_MD, warnings)]
    back = re.sub(r"\n{3,}", "\n\n", "\n".join(Converter(download=False).convert(blocks))).strip()
    want = ROUNDTRIP_MD.strip()
    if back != want:
        import difflib
        print("\n".join(difflib.unified_diff(want.split("\n"), back.split("\n"),
                                             "originál", "po round tripu", lineterm="")))
        sys.exit("SELFTEST FAILED (round trip)")
    print(f"markdown→Notion→markdown OK ({len(blocks)} bloků, beze změny)")

    complete = {"properties": {
        "Název": {"type": "title", "title": [{"plain_text": "Platný článek", "annotations": {}}]},
        "Slug": {"type": "rich_text", "rich_text": [{"plain_text": "platny-clanek", "annotations": {}}]},
        "Kategorie": {"select": {"name": "Testuju"}},
        "Datum": {"date": {"start": "2026-10-21"}},
        "Popis": {"type": "rich_text", "rich_text": [{"plain_text": "Popis", "annotations": {}}]},
        "Summary": {"type": "rich_text", "rich_text": [{"plain_text": "Summary", "annotations": {}}]},
    }}
    if validation_errors(complete):
        sys.exit("SELFTEST FAILED (validace platného článku)")
    incomplete = {"properties": {**complete["properties"],
                                   "Slug": {"type": "rich_text", "rich_text": []}}}
    if "chybí Slug" not in validation_errors(incomplete):
        sys.exit("SELFTEST FAILED (validace chybějícího slugu)")
    withdrawn = {"properties": {**complete["properties"],
        "URL": {"type": "url", "url": f"{SITE_URL}/posts/puvodni-slug/"}}}
    if withdrawal_slugs(withdrawn) != ["platny-clanek", "puvodni-slug"]:
        sys.exit("SELFTEST FAILED (odvození slugu pro stažení)")
    print("validace metadat a stažení OK (3 kontroly)")


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    cmd = args[0]
    if cmd == "selftest":
        cmd_selftest()
    elif cmd == "init":
        cmd_init(args[args.index("--parent") + 1])
    elif cmd == "sample":
        cmd_sample()
    elif cmd == "list":
        cmd_list()
    elif cmd == "publish":
        cmd_publish(args[1], draft="--draft" in args, mark="--mark-published" in args)
    elif cmd == "publish-ready":
        if "--output" not in args:
            sys.exit("Použití: notion-blog.py publish-ready --output <soubor>")
        cmd_publish_ready(args[args.index("--output") + 1])
    elif cmd in ("mark-deployed", "mark-published"):
        cmd_mark_deployed(args[1])
    elif cmd == "import":
        cat = args[args.index("--kategorie") + 1] if "--kategorie" in args else None
        cmd_import(args[1], category=cat)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
