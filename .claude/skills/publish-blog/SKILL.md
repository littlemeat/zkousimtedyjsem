---
name: publish-blog
description: Publikace článku z Notionu na blog zkousimtedyjsem.cz. Použij, když Marie řekne „publishni článek X", „publikuj z Notionu", „vydej článek", „co mám k publikaci", nebo řeší publikační workflow blogu.
---

# Publikace článku z Notionu na blog

Marie píše články v Notionu (databáze „Blog — články"), na web se dostávají skriptem
[scripts/notion-blog.py](../../scripts/notion-blog.py). Web je Hugo + GitHub Pages,
**push do main = nasazení na produkci**.

## Postup publikace

1. Token je v `.notion-token` v rootu repa (gitignored). Nikdy ho nevypisuj, neloguj
   a neposílej. Pokud chybí, viz Setup níž — vytvořit ho musí Marie.
2. `python3 scripts/notion-blog.py list` — přehled článků a stavů. Signál k publikaci
   je Stav **K publikaci** (nebo Mariin přímý pokyn se slugem/názvem).
3. `python3 scripts/notion-blog.py publish <slug>` — stáhne stránku, převede na
   markdown, obrázky stáhne a zmenší (sips, max 2000 px) do `content/posts/<slug>/`.
   Zapisuje `draft: false`; na zkušební převod použij `--draft`.
4. Projdi vygenerovaný `index.md` a vypsaná varování (nepřenesené bloky) — varování
   vždy řekni Marii.
5. Ukaž náhled: `hugo server -D` a odkaz na `http://localhost:1313/posts/<slug>/`.
6. **Commit + push do main až na výslovný pokyn Marie** — push nasazuje web.
   Commituj i `.notion-blog.json` (obsahuje jen ID databáze, není tajné).
7. Po úspěšném pushi spusť `publish <slug> --mark-published` — v Notionu přepne
   Stav na Publikováno a doplní URL článku.

## Pravidla

- Stav „K publikaci" v Notionu je signál k **převodu**, ne k pushi na web.
- Do textů obsahově nezasahovat (viz memory `blog-styl-psani` a `blog-koncept-a-odlozene-veci`).
- Nepřenáší se: callouty, toggles, sloupce, videa/embedy, rovnice, podtržení, barvy.
  Vzorový článek „vzorovy-clanek" v Notion databázi je živý katalog podporovaného formátování.

## Setup (jednorázový, dělá Marie)

1. Na notion.so/my-integrations vytvořit interní integraci (capabilities: Read +
   Insert + Update content) a token uložit: v rootu repa `echo "ntn_…" > .notion-token`.
   Token nikdy neposílat do chatu.
2. V Notionu založit stránku „Blog" a přes ⋯ → Connections k ní připojit integraci.
3. `python3 scripts/notion-blog.py init --parent <URL stránky Blog>` — založí databázi
   s vlastnostmi (Název, Slug, Kategorie, Tagy, Stav, Datum, Popis, Summary, URL),
   vytvoří vzorový článek s katalogem formátování a zapíše `.notion-blog.json`.
   Příkaz `sample` vytvoří vzorový článek znovu, bez zakládání databáze.

Hotovo 16. 9. 2026: databáze i vzorový článek („vzorovy-clanek") v Notionu existují.
