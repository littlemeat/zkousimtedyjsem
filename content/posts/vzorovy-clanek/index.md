---
title: "Vzorový článek — co všechno umí formátování"
date: 2026-09-16
draft: false
description: "Katalog formátování, které pipeline Notion → blog umí přenést."
summary: "Vzorový článek: všechno, co se z Notionu přenese na blog — a co ne."
categories: ["Pracuju"]
tags: ["návod", "blog"]
ShowToc: false
---

Tenhle článek je katalog. Každý blok níž pipeline umí převést na blog. Piš normálně jako v Notionu, jen se drž těchhle bloků — a **na konci stránky** najdeš seznam věcí, kterým se vyhnout.

## Velký nadpis (H1)

Nadpisy: H1 je hlavní sekce článku, H2 podsekce, H3 detail. Titulek článku se bere z názvu stránky, ne z textu.

### Menší nadpis (H2)

#### Nejmenší nadpis (H3)

## Styly textu

Umíme **tučné**, *kurzívu*, ~~přeškrtnuté~~, `inline kód` a [odkaz](https://zkousimtedyjsem.cz/). Podtržení se NEpřenese (markdown ho nemá) — nepoužívej ho. Barvy textu se nepřenáší taky.

## Seznamy

- odrážka
- odrážka s vnořením
    - vnořená odrážka

1. číslovaný seznam
2. druhá položka

- [ ] checkbox (hodí se na packing listy)
- [x] odškrtnutý checkbox

## Citace, oddělovač, kód

> Citace vypadá na blogu takhle. Hodí se i na zvýraznění jedné myšlenky.

---

```python
doplnky = ["hořčík", "vitamín D", "omega-3"]
for d in doplnky:
    print(f"ráno: {d}")
```

## Obrázky

Fotku prostě přetáhni do stránky. Při publikaci se automaticky stáhne, **zmenší na rozumnou velikost** a uloží k článku — nemusíš nic řešit. Popisek fotky (caption) se přenese jako popisek pod fotkou.

![Tohle je popisek fotky — napiš ho v Notionu jako caption](foto-01.jpg)
*Tohle je popisek fotky — napiš ho v Notionu jako caption*

## Tabulka

Jednoduchá tabulka projde (první řádek se bere jako hlavička):

| Co | Funguje? | Poznámka |
|---|---|---|
| Tabulky | ano | jen jednoduché |
| Emoji 🎉 | ano | v pohodě |

## Čemu se vyhnout

Tyhle bloky se na blog nepřevedou (pipeline tě na ně při publikaci upozorní): **callouty, toggles (rozbalovací bloky), sloupce, vložená videa a embedy, rovnice, propojené databáze**. A nepoužívej podtržení ani barvy textu — tiše zmizí.

Až budeš chtít článek vydat: přepni Stav na **K publikaci** a napiš Claudovi „publishni vzorový článek“. Zbytek se stane sám.

## Soukromé poznámky

Potřebuješ si v článku nechat vzkaz pro sebe („sem doplnit číslo“)? Udělej **callout s ikonou 🚧** (napiš /callout a klikni na ikonu). Takový blok se na web nepřenese — zůstane jen ve zdrojovém kódu stránky jako neviditelný komentář. Callout s jakoukoli jinou ikonou se publikuje jako citace.

<!-- Takhle vypadá poznámka, kterou čtenář nikdy neuvidí. -->
