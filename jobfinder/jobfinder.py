import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import aiohttp
import discord
from bs4 import BeautifulSoup
from discord.ext import tasks
from redbot.core import Config, commands

log = logging.getLogger("red.jobfinder")

MAX_JOBS_PER_RUN = 100
DEFAULT_INTERVAL = 60
DEFAULT_SOURCES = [
    {"type": "sitprace", "name": "Síť práce (ČR)", "url": "https://sitprace.cz/api/public/jobs/latest.json", "source_url": "https://sitprace.cz/prace"},
    {"type": "upjobs", "name": "Upjobs (ČR)", "url": "https://upjobs.cz/api/v1/jobs?per_page=100&page=1", "source_url": "https://upjobs.cz"},
    {"type": "mpsv", "name": "Úřad práce ČR / MPSV", "url": "https://data.mpsv.cz/od/soubory/volna-mista/", "source_url": "https://data.mpsv.cz/web/data/prirustky-volnych-mist-za-celou-cr", "interval_minutes": 1440},
    {"type": "remotive", "name": "Remotive", "url": "https://remotive.com/api/remote-jobs", "source_url": "https://remotive.com"},
    {"type": "arbeitnow", "name": "Arbeitnow", "url": "https://www.arbeitnow.com/api/job-board-api", "source_url": "https://www.arbeitnow.com"},
]


class PreferencesModal(discord.ui.Modal):
    def __init__(self, cog, guild, user, language, prefs):
        super().__init__(title=cog._t(guild, "modal_title", prefs={"locale": language}))
        self.cog = cog
        self.guild = guild
        self.user = user
        self.language = language
        self.keywords = discord.ui.TextInput(
            label=cog._t(guild, "include_label", prefs={"locale": language}),
            default=", ".join(prefs.get("keywords", []))[:1000], required=False,
            max_length=1000, style=discord.TextStyle.paragraph,
        )
        self.excluded = discord.ui.TextInput(
            label=cog._t(guild, "exclude_label", prefs={"locale": language}),
            default=", ".join(prefs.get("excluded_keywords", []))[:1000], required=False,
            max_length=1000, style=discord.TextStyle.paragraph,
        )
        self.locations = discord.ui.TextInput(
            label=cog._t(guild, "location_label", prefs={"locale": language}),
            default=", ".join(prefs.get("locations", []))[:1000], required=False,
            max_length=1000, style=discord.TextStyle.paragraph,
        )
        self.types = discord.ui.TextInput(
            label=cog._t(guild, "employment_label", prefs={"locale": language}),
            default=", ".join(prefs.get("employment_types", []))[:1000], required=False,
            max_length=1000, style=discord.TextStyle.paragraph,
        )
        minimum = prefs.get("min_salary")
        maximum = prefs.get("max_salary")
        salary_default = "-".join(str(value) if value is not None else "" for value in (minimum, maximum))
        if minimum is None and maximum is None:
            salary_default = ""
        self.salary = discord.ui.TextInput(
            label=cog._t(guild, "salary_range_label", prefs={"locale": language}),
            default=salary_default[:100], required=False, max_length=100,
            placeholder=cog._t(guild, "salary_range_placeholder", prefs={"locale": language}),
        )
        for item in (self.keywords, self.excluded, self.locations, self.types, self.salary):
            self.add_item(item)

    async def on_submit(self, interaction):
        config = self.cog.config.user(self.user)
        split = lambda value: [part.strip() for part in value.split(",") if part.strip()]
        keywords = split(self.keywords.value)
        excluded = split(self.excluded.value)
        locations = split(self.locations.value)
        employment_types = split(self.types.value)
        if not (keywords or locations or employment_types):
            await interaction.response.send_message(
                self.cog._t(self.guild, "filters_required", prefs={"locale": self.language}),
                ephemeral=True,
            )
            return
        salary = self.salary.value.strip().replace(" ", "")
        minimum = maximum = None
        if salary:
            try:
                parts = salary.split("-", 1)
                minimum = float(parts[0]) if parts[0] else None
                maximum = float(parts[1]) if len(parts) > 1 and parts[1] else None
                if minimum is not None and minimum < 0 or maximum is not None and maximum < 0:
                    raise ValueError
                if minimum is not None and maximum is not None and minimum > maximum:
                    raise ValueError
            except ValueError:
                await interaction.response.send_message(
                    self.cog._t(self.guild, "invalid_salary", prefs={"locale": self.language}),
                    ephemeral=True,
                )
                return
        await config.keywords.set(keywords)
        await config.excluded_keywords.set(excluded)
        await config.locations.set(locations)
        await config.employment_types.set(employment_types)
        await config.min_salary.set(minimum)
        await config.max_salary.set(maximum)
        await config.enabled.set(True)
        await interaction.response.send_message(
            self.cog._t(self.guild, "preferences_saved", prefs={"locale": self.language}),
            ephemeral=True,
        )


class PreferenceSelect(discord.ui.Select):
    def __init__(self, cog, guild, user, language, key, options, current, placeholder):
        self.cog = cog
        self.guild = guild
        self.user = user
        self.language = language
        self.key = key
        select_options = [
            discord.SelectOption(label=label, value=value, default=value == current)
            for value, label in options
        ]
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=select_options)

    async def callback(self, interaction):
        value = self.values[0]
        config = self.cog.config.user(self.user)
        if self.key == "seniority":
            await config.seniority.set([] if value == "any" else [value])
        else:
            await config.set_raw(self.key, value=value)
        await interaction.response.send_message(
            self.cog._t(self.guild, "setting_updated", prefs={"locale": self.language}),
            ephemeral=True,
        )


class PreferencesView(discord.ui.View):
    def __init__(self, cog, guild, user, prefs):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.user = user
        self.language = cog._language(guild, prefs)
        mode_options = [("any", "mode_any"), ("remote", "mode_remote"), ("hybrid", "mode_hybrid"), ("onsite", "mode_onsite")]
        cadence_options = [("instant", "frequency_instant"), ("daily", "frequency_daily")]
        seniority_options = [("any", "seniority_any"), ("junior", "seniority_junior"), ("mid", "seniority_mid"), ("senior", "seniority_senior"), ("lead", "seniority_lead")]
        locale_options = [("auto", "locale_auto"), ("cs", "locale_cs"), ("en", "locale_en")]
        for key, entries in (("remote_mode", mode_options), ("notification_frequency", cadence_options), ("seniority", seniority_options), ("locale", locale_options)):
            options = [(value, cog._t(guild, label, prefs={"locale": self.language})) for value, label in entries]
            current = prefs.get(key, "any")
            if key == "seniority":
                current = (prefs.get("seniority") or ["any"])[0]
            self.add_item(PreferenceSelect(
                cog, guild, user, self.language, key, options, current,
                cog._t(guild, f"{key}_placeholder", prefs={"locale": self.language}),
            ))
        self.edit_filters.label = cog._t(guild, "edit_filters", prefs={"locale": self.language})

    @discord.ui.button(label="Edit filters", style=discord.ButtonStyle.primary, row=4)
    async def edit_filters(self, interaction, button):
        prefs = await self.cog.config.user(self.user).all()
        await interaction.response.send_modal(
            PreferencesModal(self.cog, self.guild, self.user, self.language, prefs)
        )


class JobFinder(commands.Cog):
    """Job alerts for Discord communities."""

    __version__ = "0.2.0"
    LOCALES = {
        language: json.loads((Path(__file__).parent / "locales" / f"{language}.json").read_text(encoding="utf-8"))
        for language in ("cs", "en")
    }

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=91472531, force_registration=True)
        self.config.register_guild(
            enabled=True,
            alert_channel=None,
            alert_role=None,
            ping_users=False,
            interval_minutes=DEFAULT_INTERVAL,
            sources=DEFAULT_SOURCES,
            initialized=False,
            source_defaults_version=0,
        )
        self.config.register_global(source_polling={})
        self.config.register_user(
            enabled=False,
            keywords=[],
            excluded_keywords=[],
            locations=[],
            excluded_locations=[],
            remote_mode="any",
            min_salary=None,
            max_salary=None,
            salary_currency="CZK",
            employment_types=[],
            seniority=[],
            languages=[],
            notification_frequency="instant",
            last_digest_date={},
            locale="auto",
        )
        self.db = sqlite3.connect(
            str(self.data_path() / "jobs.sqlite3"),
            check_same_thread=False,
        )
        self.db.row_factory = sqlite3.Row
        self._init_db()
        self.session: Optional[aiohttp.ClientSession] = None
        self._provider_cache = {}
        self.poller.start()

    async def cog_unload(self):
        self.poller.cancel()
        if self.session and not self.session.closed:
            await self.session.close()
        self.db.close()

    async def red_delete_data_for_user(self, *, requester, user_id):
        self.db.execute("DELETE FROM matches WHERE user_id=?", (user_id,))
        self.db.commit()
        await self.config.user_from_id(user_id).clear()

    def data_path(self):
        path = self.bot.get_cog_data_path(self.__class__.__name__.lower())
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _init_db(self):
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                title TEXT NOT NULL,
                company TEXT,
                location TEXT,
                description TEXT,
                url TEXT NOT NULL,
                salary TEXT,
                employment_type TEXT,
                published_at TEXT,
                work_mode TEXT,
                seniority TEXT,
                category TEXT,
                currency TEXT,
                source_url TEXT,
                salary_min REAL,
                salary_max REAL,
                fingerprint TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                UNIQUE(source, external_id)
            );

            CREATE TABLE IF NOT EXISTS matches (
                job_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                score INTEGER NOT NULL,
                notified_at TEXT NOT NULL,
                sent INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(job_id, guild_id, user_id)
            );
            """
        )
        self._ensure_column("jobs", "work_mode", "TEXT")
        self._ensure_column("jobs", "seniority", "TEXT")
        self._ensure_column("jobs", "category", "TEXT")
        self._ensure_column("jobs", "currency", "TEXT")
        self._ensure_column("jobs", "source_url", "TEXT")
        self._ensure_column("jobs", "salary_min", "REAL")
        self._ensure_column("jobs", "salary_max", "REAL")
        if self._ensure_column("matches", "sent", "INTEGER NOT NULL DEFAULT 0"):
            self.db.execute("UPDATE matches SET sent=1")
        self.db.commit()

    def _ensure_column(self, table, column, definition):
        columns = {row[1] for row in self.db.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            return True
        return False

    def _language(self, guild, prefs=None):
        selected = (prefs or {}).get("locale", "auto")
        if selected in ("cs", "en"):
            return selected
        return "cs" if (guild and guild.preferred_locale and guild.preferred_locale.value.startswith("cs")) else "en"

    def _t(self, guild, key, prefs=None, **kwargs):
        language = self._language(guild, prefs)
        return self.LOCALES[language].get(key, self.LOCALES["en"].get(key, key)).format(**kwargs)

    async def _get_session(self):
        if not self.session or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=25)
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": "Redbot-JobFinder/0.1 (+Discord bot)"}
            )
        return self.session

    @staticmethod
    def _norm(value):
        return re.sub(r"\s+", " ", (value or "").strip().lower())

    def _job_id(self, source, external_id, url):
        return external_id or hashlib.sha256(f"{source}|{url}".encode()).hexdigest()

    async def _fetch_rss(self, url):
        session = await self._get_session()
        async with session.get(url) as resp:
            resp.raise_for_status()
            content = await resp.text()
        soup = BeautifulSoup(content, "xml")
        jobs = []
        for item in soup.find_all(["item", "entry"])[:MAX_JOBS_PER_RUN]:
            title = item.find("title")
            link = item.find("link")
            if link:
                link = link.get("href") or link.text
            guid = item.find("guid") or item.find("id")
            desc = item.find(["description", "summary", "content"])
            pub = item.find(["pubDate", "published", "updated"])
            jobs.append({
                "external_id": self._norm(guid.text if guid else link),
                "title": title.text.strip() if title else "Untitled",
                "company": "",
                "location": "",
                "description": BeautifulSoup(desc.text if desc else "", "html.parser").get_text(" ", strip=True),
                "url": (link or "").strip(),
                "salary": "",
                "employment_type": "",
                "published_at": pub.text.strip() if pub else "",
            })
        return jobs

    @staticmethod
    def _api_text(value):
        if value is None:
            return ""
        if isinstance(value, dict):
            return str(value.get("name") or value.get("title") or value.get("value") or value.get("city") or value.get("location") or value.get("label") or "")
        if isinstance(value, list):
            return ", ".join(filter(None, (JobFinder._api_text(item) for item in value)))
        return str(value)

    async def _fetch_api(self, source):
        session = await self._get_session()
        payload = None
        if source.get("type") == "mpsv":
            jobs = []
            today = datetime.now(timezone.utc).date()
            for days_ago in range(7):
                day = today.fromordinal(today.toordinal() - days_ago)
                filename = f"volna-mista-prirustek-{day.isoformat()}.json"
                async with session.get(source["url"] + filename) as resp:
                    if resp.status == 404:
                        continue
                    resp.raise_for_status()
                    payload = await resp.json(content_type=None)
                records = payload.get("polozky", [])
                jobs.extend(self._normalize_api_item("mpsv", item) for item in records)
            return jobs[:MAX_JOBS_PER_RUN]
        if source.get("type") == "upjobs":
            async with session.get(source["url"]) as resp:
                resp.raise_for_status()
                payload = await resp.json(content_type=None)
            return [self._normalize_api_item("upjobs", item) for item in payload.get("items", [])[:MAX_JOBS_PER_RUN]]
        async with session.get(source["url"]) as resp:
            resp.raise_for_status()
            payload = await resp.json(content_type=None)
        if isinstance(payload, list):
            records = payload
        else:
            records = payload.get("jobs", payload.get("items", payload.get("data", []))) or []
            if isinstance(records, dict):
                records = records.get("jobs", records.get("items", []))
        return [self._normalize_api_item(source.get("type"), item) for item in records[:MAX_JOBS_PER_RUN]]

    @staticmethod
    def _normalize_api_item(provider, item):
        if provider == "mpsv":
            profession = item.get("pozadovanaProfese") or {}
            employer = item.get("zamestnavatel") or {}
            workplace = item.get("mistoVykonuPrace") or {}
            clarification = item.get("upresnujiciInformace") or {}
            work_sites = workplace.get("pracoviste") or []
            location = workplace.get("adresaText") or ", ".join(
                site.get("nazev", "") for site in work_sites if site.get("nazev")
            )
            contract_codes = [entry.get("id", "").rsplit("/", 1)[-1] for entry in item.get("pracovnePravniVztahy") or []]
            contract_labels = {
                "dpp": "DPP brigáda temporary",
                "dpc": "DPČ part-time",
                "plny": "HPP full-time",
                "zkraceny": "zkrácený úvazek part-time",
                "sluzebni": "služební poměr public service",
            }
            contract_types = [contract_labels.get(code, code) for code in contract_codes]
            skills = [entry.get("popis") or (entry.get("dovednost") or {}).get("id", "") for entry in item.get("pozadovanaDovednost") or []]
            languages = [entry.get("popis") or (entry.get("jazyk") or {}).get("id", "") for entry in item.get("pozadovanaJazykovaZnalost") or []]
            details = [clarification.get("cs", "")]
            details.extend(skills)
            details.extend(languages)
            details.append("Pracovněprávní vztah: " + ", ".join(contract_types) if contract_types else "")
            salary_min = item.get("mesicniMzdaOd")
            salary_max = item.get("mesicniMzdaDo")
            wage_type = (item.get("typMzdy") or {}).get("id", "")
            if "hod" in wage_type.lower():
                hours = item.get("pocetHodinTydne") or 40
                factor = hours * 4.33
                salary_min = salary_min * factor if salary_min is not None else None
                salary_max = salary_max * factor if salary_max is not None else None
            salary = ""
            if salary_min is not None or salary_max is not None:
                display_min = item.get("mesicniMzdaOd")
                display_max = item.get("mesicniMzdaDo")
                salary = f"{display_min or '—'}–{display_max or '—'} Kč"
                salary += " / hod." if "hod" in wage_type.lower() else " / měsíc"
            return {
                "external_id": str(item.get("portalId") or item.get("id") or item.get("referencniCislo") or ""),
                "title": profession.get("cs") or item.get("referencniCislo") or "Volné pracovní místo",
                "company": employer.get("nazev", ""),
                "location": location,
                "description": "\n".join(filter(None, details)),
                "url": item.get("urlAdresa") or "",
                "salary": salary,
                "currency": "CZK" if salary else "",
                "salary_min": salary_min,
                "salary_max": salary_max,
                "employment_type": ", ".join(contract_types),
                "work_mode": "",
                "seniority": "",
                "category": "CZ-ISCO " + str((item.get("profeseCzIsco") or {}).get("id", "")),
                "published_at": item.get("datumVlozeni", ""),
            }
        if provider == "upjobs":
            location = item.get("jobLocation") or {}
            organization = item.get("hiringOrganization") or {}
            salary = item.get("salaryText") or ""
            if not salary and item.get("baseSalary"):
                base = item["baseSalary"]
                salary = f"{base.get('minValue', '')}–{base.get('maxValue', '')} {base.get('currency', '')}/{base.get('unitText', '')}"
            description = item.get("description") or item.get("summary") or ""
            return {
                "external_id": str(item.get("id") or item.get("url", "")),
                "title": item.get("title") or "Untitled",
                "company": organization.get("name", ""),
                "location": location.get("city") or location.get("country") or "",
                "description": BeautifulSoup(description, "html.parser").get_text(" ", strip=True),
                "url": item.get("url", ""),
                "salary": salary,
                "currency": (item.get("baseSalary") or {}).get("currency", ""),
                "employment_type": item.get("employmentType", ""),
                "work_mode": item.get("jobLocationType", ""),
                "seniority": item.get("seniority", ""),
                "category": (item.get("category") or {}).get("name", ""),
                "published_at": item.get("datePosted", ""),
                "salary_min": (item.get("baseSalary") or {}).get("minValue"),
                "salary_max": (item.get("baseSalary") or {}).get("maxValue"),
            }
        location = item.get("candidate_required_location") or item.get("location") or item.get("locations") or ""
        company = item.get("company_name") or item.get("company") or item.get("hiringOrganization") or ""
        description = item.get("description") or item.get("description_text") or item.get("summary") or ""
        return {
            "external_id": str(item.get("id") or item.get("url") or item.get("job_url") or ""),
            "title": item.get("title") or "Untitled",
            "company": JobFinder._api_text(company),
            "location": JobFinder._api_text(location),
            "description": BeautifulSoup(JobFinder._api_text(description), "html.parser").get_text(" ", strip=True),
            "url": item.get("url") or item.get("job_url") or "",
            "salary": JobFinder._api_text(item.get("salary") or item.get("salaryText")),
            "currency": item.get("salary_currency_code") or "",
            "employment_type": JobFinder._api_text(item.get("job_type") or item.get("employment_type")),
            "work_mode": "remote" if provider == "remotive" or item.get("remote") is True else JobFinder._api_text(item.get("work_mode") or item.get("workplace_type") or ""),
            "seniority": JobFinder._api_text(item.get("seniority") or ""),
            "category": JobFinder._api_text(item.get("category") or ""),
            "published_at": item.get("publication_date") or item.get("datePosted") or item.get("created_at") or "",
            "salary_min": item.get("salary_min"),
            "salary_max": item.get("salary_max"),
        }

    async def _collect_jobs(self, guild):
        gconf = self.config.guild(guild)
        sources = await gconf.sources()
        if await gconf.source_defaults_version() < 2:
            configured_urls = {source.get("url") for source in sources}
            sources.extend(source for source in DEFAULT_SOURCES if source["url"] not in configured_urls)
            await gconf.sources.set(sources)
            await gconf.source_defaults_version.set(2)
        guild_interval = await gconf.interval_minutes()
        all_jobs = []
        for source in sources:
            try:
                interval = source.get("interval_minutes", 360 if source.get("type") == "remotive" else guild_interval)
                now = datetime.now(timezone.utc).timestamp()
                if source.get("type") == "remotive":
                    interval = max(interval, 360)
                cached = self._provider_cache.get(source["url"])
                if cached and now - cached[0] < interval * 60:
                    jobs = [dict(job) for job in cached[1]]
                else:
                    last_poll = (await self.config.source_polling()).get(source["url"], 0)
                    if now - last_poll < interval * 60:
                        continue
                    if source.get("type") == "rss":
                        jobs = await self._fetch_rss(source["url"])
                    elif source.get("type") in ("remotive", "arbeitnow", "sitprace", "upjobs", "mpsv"):
                        jobs = await self._fetch_api(source)
                    else:
                        continue
                    self._provider_cache[source["url"]] = (now, [dict(job) for job in jobs])
                    polling = await self.config.source_polling()
                    polling[source["url"]] = now
                    await self.config.source_polling.set(polling)
                for job in jobs:
                    job["source"] = source.get("name") or urlparse(source["url"]).netloc
                    job["source_url"] = source.get("source_url") or source["url"]
                all_jobs.extend(jobs)
            except Exception:
                log.exception("Failed to fetch RSS source %s", source.get("url"))
        return all_jobs

    def _store_job(self, job):
        source = job["source"]
        external_id = self._job_id(source, job["external_id"], job["url"])
        fingerprint = hashlib.sha256(
            self._norm(f"{job['title']}|{job['company']}|{job['url']}").encode()
        ).hexdigest()
        cur = self.db.execute(
            """
            INSERT OR IGNORE INTO jobs
            (source, external_id, title, company, location, description, url,
             salary, employment_type, published_at, work_mode, seniority, category,
             currency, source_url, salary_min, salary_max, fingerprint, first_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source, external_id, job["title"], job.get("company"),
                job.get("location"), job.get("description"), job["url"],
                job.get("salary"), job.get("employment_type"),
                job.get("published_at"), job.get("work_mode"), job.get("seniority"),
                job.get("category"), job.get("currency"), job.get("source_url"),
                job.get("salary_min"), job.get("salary_max"), fingerprint,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.db.commit()
        if cur.rowcount:
            return self.db.execute(
                "SELECT * FROM jobs WHERE source=? AND external_id=?",
                (source, external_id)
            ).fetchone()
        return None

    def _match(self, job, prefs):
        text = self._norm(
            " ".join([
                job["title"] or "", job["company"] or "",
                job["location"] or "", job["description"] or "",
                job["employment_type"] or "", job["salary"] or "",
                job["work_mode"] or "", job["seniority"] or "", job["category"] or "",
            ])
        )
        if not prefs["enabled"]:
            return 0

        keywords = prefs.get("keywords", [])
        locations = prefs.get("locations", [])
        excluded_keywords = prefs.get("excluded_keywords", [])
        excluded_locations = prefs.get("excluded_locations", [])

        keyword_hits = sum(1 for k in keywords if self._norm(k) in text)
        location_text = self._norm(job["location"] or "")
        location_hits = sum(1 for x in locations if self._norm(x) in location_text)

        if any(self._norm(term) in text for term in excluded_keywords):
            return 0
        if any(self._norm(term) in location_text for term in excluded_locations):
            return 0
        if keywords and keyword_hits == 0:
            return 0
        if locations and location_hits == 0:
            return 0

        score = 0
        score += min(60, keyword_hits * 20)
        score += min(25, location_hits * 25)

        mode = prefs.get("remote_mode", "any")
        job_mode = self._norm(job["work_mode"] or "")
        if not job_mode:
            if any(term in text for term in ("remote", "home office", "homeoffice", "práce z domova", "na dálku")):
                job_mode = "remote"
            elif "hybrid" in text or "hybridní" in text:
                job_mode = "hybrid"
            elif job["work_mode"]:
                job_mode = "onsite"
        if mode != "any":
            job_mode_aliases = {
                "remote": ("remote", "na dálku", "fully_remote", "full_remote"),
                "hybrid": ("hybrid", "hybridní"),
                "onsite": ("onsite", "on_site", "na pracovišti", "office"),
            }
            if not job_mode or not any(alias in job_mode for alias in job_mode_aliases.get(mode, ())):
                return 0
            score += 10

        wanted_types = prefs.get("employment_types", [])
        if wanted_types:
            type_aliases = {
                "hpp": ("hpp", "full_time", "full-time", "permanent"),
                "full-time": ("full_time", "full-time", "hpp", "permanent"),
                "dpp": ("dpp", "casual", "temporary"),
                "dpc": ("dpc", "dpč", "part_time", "part-time"),
                "dpč": ("dpč", "dpc", "part_time", "part-time"),
                "part-time": ("part_time", "part-time", "dpč", "dpc"),
                "ico": ("ičo", "ico", "contractor", "contract"),
                "ičo": ("ičo", "ico", "contractor", "contract"),
            }
            if not any(
                any(alias in text for alias in type_aliases.get(self._norm(term), (self._norm(term),)))
                for term in wanted_types
            ):
                return 0
            score += 10

        wanted_seniority = prefs.get("seniority", [])
        if wanted_seniority:
            aliases = {"mid": ("mid", "medior", "intermediate"), "lead": ("lead", "manager", "management")}
            if not any(
                any(alias in text for alias in aliases.get(self._norm(item), (self._norm(item),)))
                for item in wanted_seniority
            ):
                return 0
            score += 5

        wanted_languages = prefs.get("languages", [])
        if wanted_languages and not all(self._norm(item) in text for item in wanted_languages):
            return 0

        wanted_currency = prefs.get("salary_currency", "CZK")
        job_currency = (job["currency"] or wanted_currency).upper()
        min_salary = prefs.get("min_salary")
        max_salary = prefs.get("max_salary")
        if min_salary is not None or max_salary is not None:
            salary_min = job["salary_min"]
            salary_max = job["salary_max"]
            if job_currency != wanted_currency or (salary_min is None and salary_max is None):
                return 0
            if min_salary is not None and salary_max is not None and salary_max < min_salary:
                return 0
            if max_salary is not None and salary_min is not None and salary_min > max_salary:
                return 0
            score += 10

        return max(1, min(100, score))

    def _job_embed(self, guild, job, score=None, prefs=None, brief=False):
        source = job["source"]
        color = 0x177E89 if "ČR" in source or "Upjobs" in source else 0x4863A0
        embed = discord.Embed(
            title=(job["title"] or self._t(guild, "untitled", prefs=prefs))[:180 if brief else 256],
            url=job["url"] or None,
            description=(job["description"] or "")[:350 if brief else 1800] or self._t(guild, "no_description", prefs=prefs),
            timestamp=datetime.now(timezone.utc),
            color=color,
        )
        if job["source_url"]:
            embed.set_author(name=self._t(guild, "source_by", prefs=prefs, source=source)[:256], url=job["source_url"])
        fields = (
            ("company", job["company"]), ("location", job["location"]),
            ("salary", job["salary"]), ("employment", job["employment_type"]),
            ("work_mode", job["work_mode"]), ("seniority", job["seniority"]),
            ("category", job["category"]), ("published", job["published_at"]),
        )
        for key, value in fields:
            if brief and key not in ("company", "location", "salary", "employment"):
                continue
            if value:
                embed.add_field(name=self._t(guild, key, prefs=prefs), value=str(value)[:140 if brief else 1024], inline=True)
        if score is not None:
            embed.add_field(name=self._t(guild, "match", prefs=prefs), value=f"{score}%", inline=True)
        embed.set_footer(text=self._t(guild, "source_footer", prefs=prefs, source=source))
        return embed

    async def _publish_news(self, guild, jobs):
        channel_id = await self.config.guild(guild).alert_channel()
        channel = guild.get_channel(channel_id) if channel_id else None
        if not channel:
            return
        role_id = await self.config.guild(guild).alert_role()
        role = guild.get_role(role_id) if role_id else None
        for offset in range(0, len(jobs), 4):
            batch = jobs[offset:offset + 4]
            embeds = [self._job_embed(guild, job, brief=True) for job in batch]
            await channel.send(
                content=role.mention if role and offset == 0 else None,
                embeds=embeds,
                allowed_mentions=discord.AllowedMentions(roles=bool(role and offset == 0)),
            )

    async def _notify(self, guild, job, member=None, score=None, prefs=None):
        gconf = self.config.guild(guild)
        embed = self._job_embed(guild, job, score, prefs)
        if member:
            await member.send(embed=embed)
            return
        channel_id = await gconf.alert_channel()
        channel = guild.get_channel(channel_id) if channel_id else None
        if not channel:
            return
        role_id = await gconf.alert_role()
        role = guild.get_role(role_id) if role_id else None
        await channel.send(
            content=role.mention if role else None,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=bool(role)),
        )

    async def _process_guild(self, guild):
        if not await self.config.guild(guild).enabled():
            return
        jobs = await self._collect_jobs(guild)
        users = []
        for member in guild.members:
            if member.bot:
                continue
            prefs = await self.config.user(member).all()
            if prefs.get("enabled"):
                users.append((member, prefs))
        if not jobs:
            await self._send_pending_instant(guild, users)
            await self._send_daily_digests(guild, users)
            return

        if not await self.config.guild(guild).initialized():
            for raw in jobs:
                self._store_job(raw)
            await self.config.guild(guild).initialized.set(True)
            return

        new_jobs = []
        for raw in jobs:
            job = self._store_job(raw)
            if not job:
                continue
            new_jobs.append(job)

            for member, prefs in users:
                score = self._match(job, prefs)
                if score <= 0:
                    continue
                try:
                    exists = self.db.execute(
                        "SELECT sent FROM matches WHERE job_id=? AND guild_id=? AND user_id=?",
                        (job["id"], guild.id, member.id)
                    ).fetchone()
                    if exists:
                        continue
                    self.db.execute(
                        "INSERT INTO matches(job_id,guild_id,user_id,score,notified_at,sent) VALUES(?,?,?,?,?,0)",
                        (job["id"], guild.id, member.id, score, datetime.now(timezone.utc).isoformat()),
                    )
                    self.db.commit()
                    if prefs.get("notification_frequency", "instant") == "instant":
                        await self._deliver_match(guild, member, job, score, prefs)
                except Exception:
                    log.exception("Failed to process match for %s", member)
        if new_jobs and await self.config.guild(guild).ping_users():
            await self._publish_news(guild, new_jobs)
        await self._send_pending_instant(guild, users)
        await self._send_daily_digests(guild, users)

    async def _send_pending_instant(self, guild, users):
        for member, prefs in users:
            if prefs.get("notification_frequency", "instant") != "instant":
                continue
            rows = self.db.execute(
                """SELECT jobs.*, matches.score FROM matches
                   JOIN jobs ON jobs.id=matches.job_id
                   WHERE matches.guild_id=? AND matches.user_id=? AND matches.sent=0
                   ORDER BY matches.notified_at""",
                (guild.id, member.id),
            ).fetchall()
            for row in rows:
                await self._deliver_match(guild, member, row, row["score"], prefs)

    async def _deliver_match(self, guild, member, job, score, prefs):
        try:
            await self._notify(guild, job, member=member, score=score, prefs=prefs)
        except discord.Forbidden:
            log.info("Cannot DM job alert to user %s", member.id)
        finally:
            self.db.execute(
                "UPDATE matches SET sent=1 WHERE job_id=? AND guild_id=? AND user_id=?",
                (job["id"], guild.id, member.id),
            )
            self.db.commit()

    async def _send_daily_digests(self, guild, users):
        now = datetime.now(timezone.utc)
        if now.hour < 18:
            return
        today = now.date().isoformat()
        for member, prefs in users:
            delivered_dates = prefs.get("last_digest_date", {})
            if prefs.get("notification_frequency") != "daily" or delivered_dates.get(str(guild.id)) == today:
                continue
            rows = self.db.execute(
                """SELECT jobs.*, matches.score FROM matches
                   JOIN jobs ON jobs.id=matches.job_id
                   WHERE matches.guild_id=? AND matches.user_id=? AND matches.sent=0
                   ORDER BY matches.notified_at""",
                (guild.id, member.id),
            ).fetchall()
            if not rows:
                continue
            delivered_ids = []
            for offset in range(0, len(rows), 4):
                batch = rows[offset:offset + 4]
                embeds = [self._job_embed(guild, row, score=row["score"], prefs=prefs, brief=True) for row in batch]
                try:
                    await member.send(
                        content=self._t(guild, "daily_digest", prefs=prefs, count=len(rows)) if offset == 0 else None,
                        embeds=embeds,
                    )
                except discord.Forbidden:
                    log.info("Cannot send job digest to user %s", member.id)
                    break
                delivered_ids.extend(row["id"] for row in batch)
            if delivered_ids:
                placeholders = ",".join("?" for _ in delivered_ids)
                self.db.execute(
                    f"UPDATE matches SET sent=1 WHERE guild_id=? AND user_id=? AND job_id IN ({placeholders})",
                    (guild.id, member.id, *delivered_ids),
                )
                self.db.commit()
            delivered_dates[str(guild.id)] = today
            await self.config.user(member).last_digest_date.set(delivered_dates)

    @tasks.loop(minutes=5)
    async def poller(self):
        for guild in self.bot.guilds:
            try:
                await self._process_guild(guild)
            except Exception:
                log.exception("JobFinder poll failed in guild %s", guild.id)

    @poller.before_loop
    async def before_poller(self):
        await self.bot.wait_until_ready()

    @commands.hybrid_group(name="job", invoke_without_command=True)
    @commands.guild_only()
    async def job(self, ctx):
        """Configure job alerts."""
        await ctx.send(self._t(ctx.guild, "job_help"))

    @job.command(name="preferences")
    async def preferences(self, ctx):
        p = await self.config.user(ctx.author).all()
        language = self._language(ctx.guild, p)
        embed = discord.Embed(
            title=self._t(ctx.guild, "preferences_title", prefs=p),
            description=self._t(ctx.guild, "preferences_intro", prefs=p),
            color=0x177E89,
        )
        def show(key):
            return ", ".join(p.get(key, [])) or "—"
        summary = [
            f"**{self._t(ctx.guild, 'include_label', prefs=p)}:** {show('keywords')}",
            f"**{self._t(ctx.guild, 'exclude_label', prefs=p)}:** {show('excluded_keywords')}",
            f"**{self._t(ctx.guild, 'location_label', prefs=p)}:** {show('locations')}",
            f"**{self._t(ctx.guild, 'employment_label', prefs=p)}:** {show('employment_types')}",
            f"**{self._t(ctx.guild, 'salary', prefs=p)}:** {p.get('min_salary') or '—'} – {p.get('max_salary') or '—'} CZK",
            f"**{self._t(ctx.guild, 'seniority', prefs=p)}:** {show('seniority')}",
            f"**{self._t(ctx.guild, 'work_mode', prefs=p)}:** {self._t(ctx.guild, 'mode_' + p.get('remote_mode', 'any'), prefs=p)}",
            f"**{self._t(ctx.guild, 'alert_frequency', prefs=p)}:** {self._t(ctx.guild, 'frequency_' + p.get('notification_frequency', 'instant'), prefs=p)}",
        ]
        embed.add_field(name=self._t(ctx.guild, "profile", prefs=p), value="\n".join(summary)[:1024], inline=False)
        embed.add_field(name=self._t(ctx.guild, "alert_status", prefs=p), value=self._t(ctx.guild, "enabled" if p.get("enabled") else "disabled", prefs=p), inline=False)
        view = PreferencesView(self, ctx.guild, ctx.author, p)
        if ctx.interaction:
            await ctx.send(embed=embed, view=view, ephemeral=True)
        else:
            try:
                await ctx.author.send(embed=embed, view=view)
                await ctx.send(self._t(ctx.guild, "preferences_sent", prefs=p))
            except discord.Forbidden:
                await ctx.send(self._t(ctx.guild, "preferences_dm_blocked", prefs=p))

    @job.command(name="set")
    async def set_preferences(self, ctx, keywords: str, locations: str = "", employment_types: str = ""):
        """Set comma-separated keywords, locations, and employment types."""
        split_values = lambda value: [item.strip() for item in value.split(",") if item.strip()]
        keywords = split_values(keywords)
        locations = split_values(locations)
        types = split_values(employment_types)
        if not (keywords or locations or types):
            await ctx.send(self._t(ctx.guild, "filters_required"))
            return

        await self.config.user(ctx.author).set_raw("keywords", value=keywords)
        await self.config.user(ctx.author).set_raw("locations", value=locations)
        await self.config.user(ctx.author).set_raw("employment_types", value=types)
        await self.config.user(ctx.author).set_raw("enabled", value=True)
        await ctx.send(self._t(ctx.guild, "preferences_saved"))

    @job.command(name="remote")
    async def remote(self, ctx, value: bool):
        await self.config.user(ctx.author).remote_mode.set("remote" if value else "onsite")
        await ctx.send(self._t(ctx.guild, "remote_set"))

    @job.command(name="language")
    async def set_language(self, ctx, language: str):
        """Set alert language to auto, Czech, or English."""
        language = language.lower()
        if language not in ("auto", "cs", "en"):
            await ctx.send(self._t(ctx.guild, "language_invalid"))
            return
        await self.config.user(ctx.author).locale.set(language)
        label = {"auto": "Auto", "cs": "Čeština", "en": "English"}[language]
        await ctx.send(self._t(ctx.guild, "language_set", language=label))

    @job.command(name="enable")
    async def enable(self, ctx):
        prefs = await self.config.user(ctx.author).all()
        if not (prefs.get("keywords") or prefs.get("locations") or prefs.get("employment_types")):
            await ctx.send(self._t(ctx.guild, "filters_required", prefs=prefs))
            return
        await self.config.user(ctx.author).set_raw("enabled", value=True)
        await ctx.send(self._t(ctx.guild, "enabled"))

    @job.command(name="disable")
    async def disable(self, ctx):
        await self.config.user(ctx.author).set_raw("enabled", value=False)
        await ctx.send(self._t(ctx.guild, "disabled"))

    @commands.hybrid_group(name="jobset", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def jobset(self, ctx):
        """Server administration for JobFinder."""
        await ctx.send(self._t(ctx.guild, "admin_help"))

    @jobset.command(name="channel")
    async def set_channel(self, ctx, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).set_raw("alert_channel", value=channel.id)
        await ctx.send(self._t(ctx.guild, "news_channel_set", channel=channel.mention))

    @jobset.command(name="role")
    async def set_role(self, ctx, role: Optional[discord.Role] = None):
        await self.config.guild(ctx.guild).set_raw("alert_role", value=role.id if role else None)
        await ctx.send(self._t(ctx.guild, "role_set" if role else "role_cleared"))

    @jobset.command(name="news")
    async def set_news(self, ctx, enabled: bool):
        """Enable or disable new listing posts in the public job channel."""
        if enabled and not await self.config.guild(ctx.guild).alert_channel():
            await ctx.send(self._t(ctx.guild, "set_news_channel_first"))
            return
        await self.config.guild(ctx.guild).set_raw("ping_users", value=enabled)
        await ctx.send(self._t(ctx.guild, "news_enabled" if enabled else "news_disabled"))

    @jobset.command(name="interval")
    async def interval(self, ctx, minutes: int):
        """Set how often feeds are checked (5 to 1440 minutes)."""
        if not 5 <= minutes <= 1440:
            await ctx.send(self._t(ctx.guild, "interval_invalid"))
            return
        await self.config.guild(ctx.guild).set_raw("interval_minutes", value=minutes)
        await ctx.send(self._t(ctx.guild, "interval_set", minutes=minutes))

    @jobset.command(name="source")
    async def source(self, ctx, url: str, *, name: str = ""):
        if not url.startswith(("http://", "https://")):
            await ctx.send(self._t(ctx.guild, "url_invalid"))
            return
        sources = await self.config.guild(ctx.guild).sources()
        if any(s.get("url") == url for s in sources):
            await ctx.send(self._t(ctx.guild, "duplicate_source"))
            return
        sources.append({"type": "rss", "url": url, "name": name or urlparse(url).netloc})
        await self.config.guild(ctx.guild).set_raw("sources", value=sources)
        await ctx.send(self._t(ctx.guild, "source_added"))

    @jobset.command(name="sources")
    async def sources(self, ctx):
        gconf = self.config.guild(ctx.guild)
        sources = await gconf.sources()
        if not sources:
            await ctx.send(self._t(ctx.guild, "sources_empty"))
            return
        default_interval = await gconf.interval_minutes()
        lines = [
            self._t(
                ctx.guild,
                "source_listing",
                index=index,
                name=source.get("name", "RSS"),
                interval=source.get("interval_minutes", 360 if source.get("type") == "remotive" else default_interval),
                url=source["url"],
            )
            for index, source in enumerate(sources, start=1)
        ]
        batch = ""
        for line in lines:
            if len(batch) + len(line) + 1 > 1800:
                await ctx.send(batch)
                batch = ""
            batch += ("\n" if batch else "") + line
        if batch:
            await ctx.send(batch)

    @jobset.command(name="remove")
    async def remove_source(self, ctx, index: int):
        """Remove a feed by its number from /jobset sources."""
        sources = await self.config.guild(ctx.guild).sources()
        if index < 1 or index > len(sources):
            await ctx.send(self._t(ctx.guild, "source_index_invalid"))
            return
        removed = sources.pop(index - 1)
        await self.config.guild(ctx.guild).set_raw("sources", value=sources)
        await ctx.send(self._t(ctx.guild, "source_removed", name=removed.get("name", "RSS")))

    @jobset.command(name="run")
    async def run_now(self, ctx):
        await ctx.send(self._t(ctx.guild, "run_started"))
        await self._process_guild(ctx.guild)
        await ctx.send(self._t(ctx.guild, "run_finished"))

    @commands.command(name="jobfinderinfo")
    async def info(self, ctx):
        await ctx.send(f"JobFinder `{self.__version__}` — RSS-based job alerts.")
