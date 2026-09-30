import asyncio
import hashlib
import logging
import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import aiohttp
import discord
from bs4 import BeautifulSoup
from redbot.core import Config, commands, app_commands, tasks

log = logging.getLogger("red.jobfinder")

DEFAULT_INTERVAL = 30
MAX_JOBS_PER_RUN = 100


class JobFinder(commands.Cog):
    """Job alerts for Discord communities."""

    __version__ = "0.1.0"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=91472531, force_registration=True)
        self.config.register_guild(
            enabled=True,
            alert_channel=None,
            alert_role=None,
            ping_users=False,
            interval_minutes=30,
            sources=[],
        )
        self.config.register_user(
            enabled=False,
            keywords=[],
            locations=[],
            remote=None,
            min_salary=None,
            employment_types=[],
        )
        self.db = sqlite3.connect(
            str(self.data_path() / "jobs.sqlite3"),
            check_same_thread=False,
        )
        self.db.row_factory = sqlite3.Row
        self._init_db()
        self.session: Optional[aiohttp.ClientSession] = None
        self.poller.start()

    def cog_unload(self):
        self.poller.cancel()
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())
        self.db.close()

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
                PRIMARY KEY(job_id, guild_id, user_id)
            );
            """
        )
        self.db.commit()

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

    @staticmethod
    def _terms(text):
        return {x for x in re.findall(r"[a-zA-Z0-9+#.\\-]{2,}", text.lower())}

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

    async def _collect_jobs(self, guild):
        sources = await self.config.guild(guild).sources()
        all_jobs = []
        for source in sources:
            if source.get("type") != "rss":
                continue
            try:
                jobs = await self._fetch_rss(source["url"])
                for job in jobs:
                    job["source"] = source.get("name") or urlparse(source["url"]).netloc
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
             salary, employment_type, published_at, fingerprint, first_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source, external_id, job["title"], job.get("company"),
                job.get("location"), job.get("description"), job["url"],
                job.get("salary"), job.get("employment_type"),
                job.get("published_at"), fingerprint,
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
                job["employment_type"] or "", job["salary"] or ""
            ])
        )
        if not prefs["enabled"]:
            return 0

        keywords = prefs["keywords"]
        locations = prefs["locations"]

        keyword_hits = sum(1 for k in keywords if self._norm(k) in text)
        location_hits = sum(1 for x in locations if self._norm(x) in text)

        if keywords and keyword_hits == 0:
            return 0
        if locations and location_hits == 0:
            return 0

        score = 0
        score += min(60, keyword_hits * 20)
        score += min(25, location_hits * 25)

        remote = prefs["remote"]
        if remote is not None:
            remote_words = ("remote", "home office", "homeoffice", "remote work")
            is_remote = any(w in text for w in remote_words)
            if remote and not is_remote:
                return 0
            if remote and is_remote:
                score += 15

        wanted_types = prefs["employment_types"]
        if wanted_types:
            if not any(self._norm(x) in text for x in wanted_types):
                return 0
            score += 10

        # Keep a useful minimum even when only one broad keyword matches.
        return min(100, score)

    async def _notify(self, guild, job):
        gconf = self.config.guild(guild)
        channel_id = await gconf.alert_channel()
        if not channel_id:
            return

        channel = guild.get_channel(channel_id)
        if not channel:
            return

        role_id = await gconf.alert_role()
        role = guild.get_role(role_id) if role_id else None
        ping_users = await gconf.ping_users()

        embed = discord.Embed(
            title=job["title"],
            url=job["url"],
            description=(job["description"] or "")[:1500] or None,
            timestamp=datetime.now(timezone.utc),
        )
        if job["company"]:
            embed.add_field(name="Firma", value=job["company"][:1024], inline=True)
        if job["location"]:
            embed.add_field(name="Lokalita", value=job["location"][:1024], inline=True)
        if job["salary"]:
            embed.add_field(name="Mzda", value=job["salary"][:1024], inline=True)
        embed.set_footer(text=f"Zdroj: {job['source']}")

        content = role.mention if role else None
        if ping_users:
            # User-specific pings are sent by the matching loop below; this keeps
            # the common role-alert path clean.
            content = content

        await channel.send(content=content, embed=embed, allowed_mentions=discord.AllowedMentions(roles=True))

    async def _process_guild(self, guild):
        if not await self.config.guild(guild).enabled():
            return
        jobs = await self._collect_jobs(guild)
        if not jobs:
            return

        users = []
        for member in guild.members:
            if member.bot:
                continue
            prefs = await self.config.user(member).all()
            if prefs["enabled"]:
                users.append((member, prefs))

        for raw in jobs:
            job = self._store_job(raw)
            if not job:
                continue

            notified_any = False
            for member, prefs in users:
                score = self._match(job, prefs)
                if score <= 0:
                    continue
                try:
                    exists = self.db.execute(
                        "SELECT 1 FROM matches WHERE job_id=? AND guild_id=? AND user_id=?",
                        (job["id"], guild.id, member.id)
                    ).fetchone()
                    if exists:
                        continue

                    # If personal alerts are enabled, send a DM. This avoids
                    # leaking users' private job preferences into a public channel.
                    if await self.config.guild(guild).ping_users():
                        try:
                            await member.send(
                                f"🎯 **Nová nabídka ({score}% match)**\n"
                                f"**{job['title']}**\n{job['url']}"
                            )
                        except discord.Forbidden:
                            pass

                    self.db.execute(
                        "INSERT INTO matches(job_id,guild_id,user_id,score,notified_at) VALUES(?,?,?,?,?)",
                        (job["id"], guild.id, member.id, score, datetime.now(timezone.utc).isoformat())
                    )
                    self.db.commit()
                    notified_any = True
                except Exception:
                    log.exception("Failed to process match for %s", member)
            if notified_any:
                await self._notify(guild, job)

    @tasks.loop(minutes=DEFAULT_INTERVAL)
    async def poller(self):
        for guild in self.bot.guilds:
            try:
                await self._process_guild(guild)
            except Exception:
                log.exception("JobFinder poll failed in guild %s", guild.id)

    @poller.before_loop
    async def before_poller(self):
        await self.bot.wait_until_ready()

    @commands.group(name="job", invoke_without_command=True)
    @commands.guild_only()
    async def job(self, ctx):
        """Configure job alerts."""
        await ctx.send_help(ctx.command)

    @job.command(name="preferences")
    async def preferences(self, ctx):
        p = await self.config.user(ctx.author).all()
        kws = ", ".join(p["keywords"]) or "—"
        locs = ", ".join(p["locations"]) or "—"
        types = ", ".join(p["employment_types"]) or "—"
        await ctx.send(
            f"**JobFinder preferences**\n"
            f"Enabled: `{p['enabled']}`\n"
            f"Keywords: `{kws}`\n"
            f"Locations: `{locs}`\n"
            f"Remote: `{p['remote']}`\n"
            f"Employment types: `{types}`\n"
            f"Min salary: `{p['min_salary'] or '—'}`"
        )

    @job.command(name="set")
    async def set_preferences(self, ctx, *, text: str):
        """Example: [python, backend] | [Ostrava, remote] | [HPP, freelance]"""
        parts = [x.strip() for x in text.split("|")]
        if len(parts) < 2:
            await ctx.send("Použij: `!job set python, backend | Ostrava, remote | HPP, freelance`")
            return

        keywords = [x.strip() for x in parts[0].split(",") if x.strip()]
        locations = [x.strip() for x in parts[1].split(",") if x.strip()]
        types = [x.strip() for x in parts[2].split(",") if x.strip()] if len(parts) >= 3 else []

        await self.config.user(ctx.author).set_raw("keywords", value=keywords)
        await self.config.user(ctx.author).set_raw("locations", value=locations)
        await self.config.user(ctx.author).set_raw("employment_types", value=types)
        await self.config.user(ctx.author).set_raw("enabled", value=True)
        await ctx.send("✅ Preference uloženy a upozornění zapnuto.")

    @job.command(name="remote")
    async def remote(self, ctx, value: bool):
        await self.config.user(ctx.author).set_raw("remote", value=value)
        await ctx.send(f"✅ Remote filtr: `{value}`")

    @job.command(name="enable")
    async def enable(self, ctx):
        await self.config.user(ctx.author).set_raw("enabled", value=True)
        await ctx.send("🔔 Job alerty zapnuty.")

    @job.command(name="disable")
    async def disable(self, ctx):
        await self.config.user(ctx.author).set_raw("enabled", value=False)
        await ctx.send("🔕 Job alerty vypnuty.")

    @commands.group(name="jobset", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def jobset(self, ctx):
        """Server administration for JobFinder."""
        await ctx.send_help(ctx.command)

    @jobset.command(name="channel")
    async def set_channel(self, ctx, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).set_raw("alert_channel", value=channel.id)
        await ctx.send(f"✅ Alert kanál: {channel.mention}")

    @jobset.command(name="role")
    async def set_role(self, ctx, role: Optional[discord.Role]):
        await self.config.guild(ctx.guild).set_raw("alert_role", value=role.id if role else None)
        await ctx.send("✅ Alert role nastavena." if role else "✅ Alert role vypnuta.")

    @jobset.command(name="personal")
    async def set_personal(self, ctx, value: bool):
        await self.config.guild(ctx.guild).set_raw("ping_users", value=value)
        await ctx.send(f"✅ Osobní DM upozornění: `{value}`")

    @jobset.command(name="source")
    async def source(self, ctx, url: str, *, name: str = ""):
        if not url.startswith(("http://", "https://")):
            await ctx.send("URL musí začínat `http://` nebo `https://`.")
            return
        sources = await self.config.guild(ctx.guild).sources()
        sources.append({"type": "rss", "url": url, "name": name or urlparse(url).netloc})
        await self.config.guild(ctx.guild).set_raw("sources", value=sources)
        await ctx.send(f"✅ RSS zdroj přidán: `{url}`")

    @jobset.command(name="sources")
    async def sources(self, ctx):
        sources = await self.config.guild(ctx.guild).sources()
        if not sources:
            await ctx.send("Žádné zdroje nejsou nastavené.")
            return
        lines = [f"{i+1}. **{s.get('name','RSS')}** — {s['url']}" for i, s in enumerate(sources)]
        await ctx.send("\n".join(lines))

    @jobset.command(name="run")
    async def run_now(self, ctx):
        await ctx.send("🔎 Spouštím kontrolu pracovních nabídek…")
        await self._process_guild(ctx.guild)
        await ctx.send("✅ Kontrola dokončena.")

    @jobset.command(name="interval")
    async def interval(self, ctx, minutes: int):
        if minutes < 5:
            await ctx.send("Minimum je 5 minut.")
            return
        await self.config.guild(ctx.guild).set_raw("interval_minutes", value=minutes)
        await ctx.send(
            "⚠️ Interval byl uložen. V MVP je poller nastaven na 30 minut; "
            "dynamický interval můžeme zapnout v další verzi."
        )

    @commands.command(name="jobfinderinfo")
    async def info(self, ctx):
        await ctx.send(f"JobFinder `{self.__version__}` — RSS-based job alerts.")
