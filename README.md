# Zkouším, tedy jsem

Osobní blog na [zkousimtedyjsem.cz](https://zkousimtedyjsem.cz) — Hugo + téma PaperMod, hostované na GitHub Pages.

## Články se píšou v Notionu

Notion databáze **Blog — články** je zdrojem článků. Složka `content/posts/` v tomto
repozitáři je jen jejich vygenerovaný výstup — ruční úpravy se při příštím převodu
z Notionu přepíšou.

U článku v Notionu vyplň:

- **Název**, **Slug**, **Datum**, **Kategorie**, **Tagy**, **Popis** a **Summary**;
- kategorii vyber z **Pracuju / Testuju / Cestuju**;
- obrázky jednoduše vlož do stránky; při převodu se stáhnou k článku a zmenší na
  maximálně 2 000 px delší strany.

Podporované jsou nadpisy, běžné formátování textu, odkazy, seznamy, checkboxy,
citace, oddělovače, kód, obrázky a jednoduché tabulky. Callout s ikonou `🚧` je
soukromá poznámka: na web se nevypíše. Toggly, sloupce, videa a embedy, rovnice,
podtržení a barvy textu se na web nepřenášejí.

## Publikování

Cílový postup je jednoduchý:

1. Dokonči článek v Notionu a nastav **Stav** na **K publikaci**.
2. Otevři uložený odkaz „Publikovat blog“ na GitHubu a klikni na **Run workflow**.
3. GitHub Actions převede všechny připravené články, sestaví a nasadí web. Po
   úspěšném nasazení je v Notionu označí jako **Publikováno** a doplní jejich URL.

> Tento jednoklikový publikační workflow se ještě doplní. Do té doby se převod
> provádí příkazem `python3 scripts/notion-blog.py publish <slug>`; následné
> odeslání do větve `main` spustí stávající nasazení na GitHub Pages.

Push do `main` vždy spouští sestavení a nasazení webu na produkci. Běžně trvá
přibližně dvě minuty.

## Lokální náhled

```bash
git submodule update --init   # jen poprvé (stáhne téma)
hugo server -D                # -D zobrazí i drafty
```

Web pak běží na http://localhost:1313.

## Ruční Markdown (jen výjimečně)

Pro jednorázový technický zásah lze založit článek příkazem
`hugo new posts/nazev-clanku/index.md`. Nejde ale o běžný způsob psaní článků:
pokud bude mít stejný slug článek v Notionu, další převod tento soubor přepíše.

## Affiliate odkazy

Používej střídmě — jen v článcích, kde o produktu opravdu píšeš. Takový článek musí mít na konci upozornění (řádek je připravený v šabloně článku) a odkaz na [affiliate prohlášení](https://zkousimtedyjsem.cz/affiliate-prohlaseni/).
