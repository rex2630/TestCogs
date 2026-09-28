# FlightWatch

[English](README.md) | [Čeština](README.cs.md)

FlightWatch vyhledává nejnižší jednosměrný nebo zpáteční tarif Ryanairu a sleduje nejnižší jednosměrnou cenu pro trasu a kalendářní měsíc. Zpáteční hledání porovnává celé kombinace odletu a návratu v zadaných časových oknech. Do Discordu může poslat upozornění při změně sledované ceny, novém minimu nebo dosažení volitelného cenového limitu. Příkazy fungují jako slash i jako prefix příkazy.

## Instalace

Přidej repozitář a nainstaluj cog:

```text
[p]repo add TestCogs https://github.com/rex2630/TestCogs
[p]cog install TestCogs flightwatch
[p]load flightwatch
```

Cog si nainstaluje závislost `ryanair-py`. API klíč není potřeba.

## Jazyk

Ve výchozím stavu cog respektuje jazyk nastavený na serveru: české servery dostanou češtinu, ostatní angličtinu. Jazyk lze změnit:

```text
/flight language cs
/flight language en
/flight language auto
```

Volba `auto` znovu použije jazyk serveru.

## Příkazy

| Příkaz | Popis |
| --- | --- |
| `/flight search <odlet> <cíl> <rok> <měsíc> [měna]` | Najde nejnižší tarif Ryanairu pro měsíc. |
| `/flight returnsearch <odlet> <cíl> <odlet_od> <odlet_do> <návrat_od> <návrat_do> [měna]` | Najde nejlevnější zpáteční kombinaci v zadaných termínech. Data zadej jako `RRRR-MM-DD`. |
| `/flight watch <odlet> <cíl> <rok> <měsíc> [měna] [cenový_limit]` | Sleduje měsíční minimum a volitelně hlídá cenový limit. |
| `/flight watches` | Vypíše sledované trasy na serveru. |
| `/flight unwatch <id>` | Odebere sledování podle jeho ID. |
| `/flight check` | Okamžitě zkontroluje sledované ceny. |
| `/flight language <auto|cs|en>` | Nastaví jazyk odpovědí na serveru. |

Používej třípísmenné IATA kódy letišť. Příklady:

```text
/flight search PRG STN 2026 11 EUR
/flight returnsearch PRG STN 2026-11-01 2026-11-10 2026-11-05 2026-11-20 EUR
/flight watch PRG STN 2026 11 EUR 40
```

Zpáteční hledání vyhodnocuje kompletní kombinace odletu a návratu, nesčítá dvě nesouvisející nejlevnější jednosměrné letenky. Může hledat v pružných časových oknech, ale délku pobytu omezují pouze zadaná okna. Ceny jsou pro jednoho cestujícího; toto API wrapper rozhraní neumí spolehlivě ocenit více osob. Sledování se kontrolují každých 30 minut. Upozornění obsahuje odkaz na rezervaci u Ryanairu. Bot letenky nekupuje.

## Rozsah a omezení

Tato verze sleduje pouze jednosměrné tarify Ryanairu. Vyhledávání podporuje jednosměrné i zpáteční itineráře, ale zpáteční ceny zatím nelze sledovat upozorněními. Cog nesleduje každý jednotlivý let ani jiné aerolinky. Skyscanner není součástí, protože jeho Live Prices API vyžaduje partnerský přístup a veřejně garantovaný bezplatný tarif nenabízí.

Balíček `ryanair-py` používá neoficiální a nedokumentovaná rozhraní Ryanairu. Mohou se změnit, omezovat požadavky nebo vracet cenu odlišnou od konečné částky při rezervaci. Kontrola po 30 minutách není realtime feed. Před nákupem cenu vždy ověř na webu Ryanairu.

## Ukládaná data

Cog ukládá sledované trasy, měnu, volitelný cenový limit, poslední a nejnižší cenu, ID kanálů pro upozornění a jazyk serveru v Red Configu. Neukládá osobní profilová data.
