---
name: publish-blog
description: Publikace článku z Notionu na blog zkousimtedyjsem.cz. Použij, když Marie řekne „publishni článek X", „publikuj z Notionu", „vydej článek", „co mám k publikaci", nebo řeší publikační workflow blogu.
---

# Publikace článku z Notionu na blog

Marie píše články v Notionu (databáze „Blog — články"), na web se dostávají skriptem
[scripts/notion-blog.py](../../scripts/notion-blog.py). Web je Hugo + GitHub Pages,
ale běžné vydání provádí ruční GitHub Actions workflow **„Publikovat blog z Notionu"**.

## Postup publikace

1. Marie nastaví v Notionu u hotového článku **Stav: K publikaci**.
2. Otevře GitHub Actions workflow „Publikovat blog z Notionu" a klikne na **Run workflow**.
3. Workflow ověří metadata, převede všechny připravené články, sestaví web a nasadí ho.
   Teprve po úspěšném nasazení nastaví Stav **Publikováno** a doplní URL. Pokud převod
   nebo validace konkrétního článku selže, nastaví **Chyba publikace**.

Pro stažení již vydaného článku Marie nastaví Stav **Ke stažení** a spustí stejný
workflow. Workflow odstraní jeho Hugo bundle, nasadí změnu a pak nastaví konečný Stav
**Staženo** a vymaže URL ze záznamu v Notionu.

Pro technické ladění je možné použít lokální token z `.notion-token` a příkazy
`list`, `publish <slug>` nebo `publish-ready --output <soubor>`. Token nikdy
nevypisuj, neloguj ani neposílej. GitHub workflow používá výhradně GitHub Secret
`NOTION_TOKEN`.

## Pravidla

- Stav „K publikaci" zveřejní článek; stav „Ke stažení" jej stáhne z webu. Po úspěšném
  nasazení dostane konečný stav „Staženo“. Stav „Publikováno" ponechává aktuální veřejnou
  verzi beze změny.
- Do textů obsahově nezasahovat (viz memory `blog-styl-psani` a `blog-koncept-a-odlozene-veci`).
- Nepřenáší se: toggles, sloupce, videa/embedy, rovnice, podtržení, barvy.
  Vzorový článek „vzorovy-clanek" v Notion databázi je živý katalog podporovaného formátování.
- **Callout s ikonou 🚧 = soukromá poznámka.** Jde na web jako HTML komentář, takže
  je neviditelný. Callout s jinou ikonou se publikuje jako citace.
- Obrázky: skript uloží do repa jeden zmenšený obrázek (max 2000 px), WebP a responzivní
  velikosti generuje Hugo při buildu přes `layouts/_default/_markup/render-image.html`.
- Články žijí v Notionu, `content/posts/` je jen výstup publikace. Ruční editace
  markdownu v repu se při dalším publishi přepíše — opravy patří do Notionu.

## Import staršího markdownu do Notionu

`python3 scripts/notion-blog.py import <cesta/index.md> [--kategorie Testuju]`
Převede front matter na vlastnosti a tělo na bloky. HTML komentáře se stanou
poznámkami 🚧. Lokální obrázky se nenahrávají — skript na ně upozorní.

## Setup (jednorázový, dělá Marie)

1. Na notion.so/my-integrations vytvořit interní integraci (capabilities: Read +
   Insert + Update content). Token uložit jako GitHub Actions Secret `NOTION_TOKEN`.
   Pro lokální ladění je možné ho uložit do `.notion-token`; token nikdy neposílat do chatu.
2. V Notionu založit stránku „Blog" a přes ⋯ → Connections k ní připojit integraci.
3. `python3 scripts/notion-blog.py init --parent <URL stránky Blog>` — založí databázi
   s vlastnostmi (Název, Slug, Kategorie, Tagy, Stav včetně „Ke stažení", „Staženo" a „Chyba publikace", Datum, Popis, Summary, URL),
   vytvoří vzorový článek s katalogem formátování a zapíše `.notion-blog.json`.
   Příkaz `sample` vytvoří vzorový článek znovu, bez zakládání databáze.

Hotovo 16. 9. 2026: databáze i vzorový článek („vzorovy-clanek") v Notionu existují.
