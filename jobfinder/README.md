# JobFinder — Red-DiscordBot Cog

MVP cog pro sledování pracovních nabídek a jejich párování s preferencemi uživatelů.

## Instalace

1. Zkopíruj adresář `jobfinder` do svého Redbot cog path.
2. Ujisti se, že prostředí Redbotu má:
   - `aiohttp`
   - `beautifulsoup4`
3. V Redbotu:
   ```
   [p]load jobfinder
   ```

## Nastavení serveru

Nejdřív nastav kanál:

```text
[p]jobset channel #jobs
```

Přidej RSS zdroj:

```text
[p]jobset source https://example.com/jobs/rss Example Jobs
```

Zkontroluj zdroje:

```text
[p]jobset sources
```

Ruční kontrola:

```text
[p]jobset run
```

Volitelně nastav roli:

```text
[p]jobset role @JobAlerts
```

Nebo osobní DM upozornění:

```text
[p]jobset personal true
```

## Nastavení uživatele

Příklad:

```text
[p]job set python, backend, django | Ostrava, remote | HPP, freelance
```

Remote filtr:

```text
[p]job remote true
```

Zapnutí/vypnutí:

```text
[p]job enable
[p]job disable
```

Zobrazení:

```text
[p]job preferences
```

## Důležité

Toto je MVP. RSS parser je schválně obecný a neobsahuje scraping konkrétních pracovních portálů.

Pro produkční verzi doporučuji přidat samostatné providery:

```text
providers/
  base.py
  startupjobs.py
  adzuna.py
  rss.py
```

a normalizovat nabídky do společného `Job` modelu.

Také je vhodné:
- nepoužívat scraping tam, kde to odporují podmínky služby,
- cachovat výsledky,
- respektovat rate limits,
- přidat cleanup starých jobů,
- přidat admin příkaz na odstranění RSS zdroje,
- přesunout periodu polleru na dynamickou konfiguraci,
- přidat Discord UI/modals místo textových preference příkazů.
