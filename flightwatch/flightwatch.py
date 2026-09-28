import asyncio
from calendar import monthrange
import json
import logging
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from discord.ext import tasks
from redbot.core import Config, commands
from ryanair import Ryanair as RyanairAPI


log = logging.getLogger("red.flightwatch")
AIRPORT_RE = re.compile(r"^[A-Z]{3}$")
CHECK_INTERVAL_MINUTES = 30
LOCALE_DIR = Path(__file__).parent / "locales"
MESSAGES = {
    language: json.loads((LOCALE_DIR / f"{language}.json").read_text(encoding="utf-8"))
    for language in ("en", "cs")
}


class FlightWatch(commands.Cog):
    """Search Ryanair one-way and return fares and track monthly lows."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=7461829031, force_registration=True)
        self.config.register_guild(watches=[], language="auto")
        self._apis = {}
        self._api_lock = asyncio.Lock()

    async def cog_load(self):
        self.check_prices.start()

    async def cog_unload(self):
        self.check_prices.cancel()

    @commands.hybrid_group(name="flight", invoke_without_command=True)
    @commands.guild_only()
    async def flight(self, ctx):
        """Search Ryanair fares and manage fare alerts."""
        language = await self._get_language(ctx.guild)
        await ctx.send(MESSAGES[language]["help"])

    @flight.command(name="airports")
    async def airport_codes(self, ctx):
        """Get the official IATA airport-code lookup link."""
        language = await self._get_language(ctx.guild)
        await ctx.send(MESSAGES[language]["airport_codes"])

    @flight.command(name="explore")
    async def explore(self, ctx, origin: str, date_from: str, date_to: str, currency: str = "EUR"):
        """Compare the lowest fares from one airport to many destinations without saving a watch."""
        language = await self._get_language(ctx.guild)
        origin = origin.upper()
        currency = currency.upper()
        try:
            self._validate_route(origin, None, currency, language)
            start, end = self._parse_date_window(date_from, date_to, language)
            api = await self._get_api(currency)
            flights = await asyncio.to_thread(api.get_cheapest_flights, origin, start, end)
        except ValueError as exc:
            error = self._user_error(exc, language)
            await ctx.send(MESSAGES[language]["explore_failed"].format(error=error))
            return
        except Exception:
            log.exception("Destination exploration failed for %s", origin)
            await ctx.send(MESSAGES[language]["api_error"])
            return

        fares = sorted(flights, key=lambda flight: flight.price)[:5]
        if not fares:
            await ctx.send(MESSAGES[language]["explore_empty"])
            return
        header = MESSAGES[language]["explore_header"].format(
            origin=origin, from_date=start.isoformat(), to_date=end.isoformat(), currency=currency
        )
        lines = [
            MESSAGES[language]["explore_line"].format(
                destination=flight.destination,
                price=flight.price,
                currency=flight.currency,
                date=flight.departureTime.date().isoformat(),
                url=self._booking_url(origin, flight.destination, flight.departureTime.date().isoformat()),
            )
            for flight in fares
        ]
        await ctx.send(f"{header}\n" + "\n".join(lines))

    @flight.command(name="search")
    async def search(
        self,
        ctx,
        origin: str,
        destination: str,
        year: int,
        month: int,
        currency: str = "EUR",
    ):
        """Find the cheapest Ryanair fare in a month (airport IATA codes)."""
        language = await self._get_language(ctx.guild)
        try:
            self._validate_search(origin, destination, year, month, currency, language)
            fares = await self._fetch_fares(origin.upper(), destination.upper(), year, month, currency.upper())
        except ValueError as exc:
            error = self._user_error(exc, language, "NO_FARES", "no_fares")
            await ctx.send(MESSAGES[language]["search_failed"].format(error=error))
            return
        except Exception:
            log.exception("Fare search failed for %s -> %s", origin, destination)
            await ctx.send(MESSAGES[language]["api_error"])
            return

        fare = fares[0]
        booking_url = self._booking_url(origin.upper(), destination.upper(), fare["date"])
        await ctx.send(MESSAGES[language]["search_result"].format(
            price=fare["price"], currency=currency.upper(), origin=origin.upper(),
            destination=destination.upper(), date=fare["date"], url=booking_url
        ))

    @flight.command(name="returnsearch")
    async def return_search(
        self,
        ctx,
        origin: str,
        destination: str,
        outbound_from: str,
        outbound_to: str,
        return_from: str,
        return_to: str,
        currency: str = "EUR",
    ):
        """Find the cheapest combined return fare within four ISO date bounds."""
        language = await self._get_language(ctx.guild)
        try:
            origin = origin.upper()
            destination = destination.upper()
            currency = currency.upper()
            self._validate_route(origin, destination, currency, language)
            outbound_start, outbound_end = self._parse_date_window(
                outbound_from, outbound_to, language
            )
            return_start, return_end = self._parse_date_window(return_from, return_to, language)
            if return_start < outbound_start or return_end < outbound_start:
                raise ValueError(MESSAGES[language]["invalid_return_dates"])
            trips = await self._fetch_return_fares(
                origin, destination, outbound_start, outbound_end,
                return_start, return_end, currency
            )
        except ValueError as exc:
            error = self._user_error(exc, language, "NO_RETURN_FARES", "no_return_fares")
            await ctx.send(MESSAGES[language]["return_search_failed"].format(error=error))
            return
        except Exception:
            log.exception("Return fare search failed for %s -> %s", origin, destination)
            await ctx.send(MESSAGES[language]["api_error"])
            return

        trip = min(trips, key=lambda candidate: candidate.totalPrice)
        booking_url = self._booking_url(
            origin, destination, trip.outbound.departureTime.date().isoformat(),
            trip.inbound.departureTime.date().isoformat(),
        )
        await ctx.send(MESSAGES[language]["return_search_result"].format(
            total=trip.totalPrice, currency=trip.outbound.currency,
            origin=origin, destination=destination,
            out_date=trip.outbound.departureTime.date().isoformat(),
            out_price=trip.outbound.price,
            return_date=trip.inbound.departureTime.date().isoformat(),
            return_price=trip.inbound.price, url=booking_url,
        ))

    @flight.command(name="returnwatch")
    async def return_watch_by_month(
        self,
        ctx,
        origin: str,
        destination: str,
        outbound_year: int,
        outbound_month: int,
        return_year: int,
        return_month: int,
        currency: str = "EUR",
        max_price: Optional[float] = None,
    ):
        """Track the lowest return fare using outbound and return months."""
        language = await self._get_language(ctx.guild)
        origin = origin.upper()
        destination = destination.upper()
        currency = currency.upper()
        try:
            self._validate_route(origin, destination, currency, language)
            outbound_start, outbound_end = self._get_month_window(
                outbound_year, outbound_month, language
            )
            return_start, return_end = self._get_month_window(
                return_year, return_month, language
            )
            if return_start < outbound_start:
                raise ValueError(MESSAGES[language]["invalid_return_months"])
            if max_price is not None and max_price <= 0:
                raise ValueError(MESSAGES[language]["invalid_limit"])
            trips = await self._fetch_return_fares(
                origin, destination, outbound_start, outbound_end,
                return_start, return_end, currency
            )
        except ValueError as exc:
            error = self._user_error(exc, language, "NO_RETURN_FARES", "no_return_fares")
            await ctx.send(MESSAGES[language]["watch_failed"].format(error=error))
            return
        except Exception:
            log.exception("Unable to start monthly return fare watch %s -> %s", origin, destination)
            await ctx.send(MESSAGES[language]["api_error"])
            return

        await self._save_return_watch(
            ctx, origin, destination, outbound_start, outbound_end,
            return_start, return_end, currency, max_price, trips, language,
            month_based=True,
        )

    @flight.command(name="returnwatchdates")
    async def return_watch_dates(
        self,
        ctx,
        origin: str,
        destination: str,
        outbound_from: str,
        outbound_to: str,
        return_from: str,
        return_to: str,
        currency: str = "EUR",
        max_price: Optional[float] = None,
    ):
        """Track the lowest combined return fare in exact date windows."""
        language = await self._get_language(ctx.guild)
        origin = origin.upper()
        destination = destination.upper()
        currency = currency.upper()
        try:
            self._validate_route(origin, destination, currency, language)
            outbound_start, outbound_end = self._parse_date_window(
                outbound_from, outbound_to, language
            )
            return_start, return_end = self._parse_date_window(return_from, return_to, language)
            if return_start < outbound_start or return_end < outbound_start:
                raise ValueError(MESSAGES[language]["invalid_return_dates"])
            if max_price is not None and max_price <= 0:
                raise ValueError(MESSAGES[language]["invalid_limit"])
            trips = await self._fetch_return_fares(
                origin, destination, outbound_start, outbound_end,
                return_start, return_end, currency
            )
        except ValueError as exc:
            error = self._user_error(exc, language, "NO_RETURN_FARES", "no_return_fares")
            await ctx.send(MESSAGES[language]["watch_failed"].format(error=error))
            return
        except Exception:
            log.exception("Unable to start return fare watch %s -> %s", origin, destination)
            await ctx.send(MESSAGES[language]["api_error"])
            return

        await self._save_return_watch(
            ctx, origin, destination, outbound_start, outbound_end,
            return_start, return_end, currency, max_price, trips, language,
        )

    async def _save_return_watch(
        self, ctx, origin, destination, outbound_start, outbound_end,
        return_start, return_end, currency, max_price, trips, language,
        month_based=False,
    ):
        watches = await self.config.guild(ctx.guild).watches()
        duplicate = any(
            item.get("kind") == "return"
            and item["origin"] == origin
            and item["destination"] == destination
            and item["outbound_from"] == outbound_start.isoformat()
            and item["outbound_to"] == outbound_end.isoformat()
            and item["return_from"] == return_start.isoformat()
            and item["return_to"] == return_end.isoformat()
            and item["currency"] == currency
            and item["channel_id"] == ctx.channel.id
            for item in watches
        )
        if duplicate:
            await ctx.send(MESSAGES[language]["return_watch_duplicate"])
            return

        trip = min(trips, key=lambda candidate: candidate.totalPrice)
        outbound_date = trip.outbound.departureTime.date().isoformat()
        return_date = trip.inbound.departureTime.date().isoformat()
        watch_id = uuid.uuid4().hex[:8]
        watches.append(
            {
                "id": watch_id,
                "kind": "return",
                "origin": origin,
                "destination": destination,
                "outbound_from": outbound_start.isoformat(),
                "outbound_to": outbound_end.isoformat(),
                "return_from": return_start.isoformat(),
                "return_to": return_end.isoformat(),
                "currency": currency,
                "channel_id": ctx.channel.id,
                "owner_id": ctx.author.id,
                "max_price": max_price,
                "last_price": float(trip.totalPrice),
                "lowest_price": float(trip.totalPrice),
                "last_date": outbound_date,
                "last_return_date": return_date,
                "last_out_price": float(trip.outbound.price),
                "last_return_price": float(trip.inbound.price),
            }
        )
        await self.config.guild(ctx.guild).watches.set(watches)
        cap = (
            MESSAGES[language]["return_limit_cap"].format(price=max_price, currency=currency)
            if max_price is not None else ""
        )
        message_key = "return_watch_month_created" if month_based else "return_watch_created"
        message_values = {
            "origin": origin,
            "destination": destination,
            "outbound_from": outbound_start.isoformat(),
            "outbound_to": outbound_end.isoformat(),
            "return_from": return_start.isoformat(),
            "return_to": return_end.isoformat(),
            "price": trip.totalPrice,
            "currency": currency,
            "out_date": outbound_date,
            "return_date": return_date,
            "cap": cap,
            "watch_id": watch_id,
            "minutes": CHECK_INTERVAL_MINUTES,
            "outbound_period": outbound_start.strftime("%Y-%m"),
            "return_period": return_start.strftime("%Y-%m"),
        }
        await ctx.send(MESSAGES[language][message_key].format(**message_values))

    @flight.command(name="watch")
    async def watch(
        self,
        ctx,
        origin: str,
        destination: str,
        year: int,
        month: int,
        currency: str = "EUR",
        max_price: Optional[float] = None,
    ):
        """Watch the monthly lowest fare and alert on every price change."""
        language = await self._get_language(ctx.guild)
        try:
            origin = origin.upper()
            destination = destination.upper()
            currency = currency.upper()
            self._validate_search(origin, destination, year, month, currency, language)
            if max_price is not None and max_price <= 0:
                raise ValueError(MESSAGES[language]["invalid_limit"])
            fares = await self._fetch_fares(origin, destination, year, month, currency)
        except ValueError as exc:
            error = self._user_error(exc, language, "NO_FARES", "no_fares")
            await ctx.send(MESSAGES[language]["watch_failed"].format(error=error))
            return
        except Exception:
            log.exception("Unable to start fare watch %s -> %s", origin, destination)
            await ctx.send(MESSAGES[language]["api_error"])
            return

        watches = await self.config.guild(ctx.guild).watches()
        duplicate = any(
            item.get("kind", "oneway") == "oneway"
            and item["origin"] == origin
            and item["destination"] == destination
            and item["year"] == year
            and item["month"] == month
            and item["currency"] == currency
            and item["channel_id"] == ctx.channel.id
            for item in watches
        )
        if duplicate:
            await ctx.send(MESSAGES[language]["duplicate"])
            return

        fare = fares[0]
        watch_id = uuid.uuid4().hex[:8]
        watches.append(
            {
                "id": watch_id,
                "kind": "oneway",
                "origin": origin,
                "destination": destination,
                "year": year,
                "month": month,
                "currency": currency,
                "channel_id": ctx.channel.id,
                "owner_id": ctx.author.id,
                "max_price": max_price,
                "last_price": fare["price"],
                "lowest_price": fare["price"],
                "last_date": fare["date"],
            }
        )
        await self.config.guild(ctx.guild).watches.set(watches)
        cap = (
            MESSAGES[language]["limit_cap"].format(price=max_price, currency=currency)
            if max_price is not None else ""
        )
        await ctx.send(MESSAGES[language]["watch_created"].format(
            origin=origin, destination=destination, period=f"{year}-{month:02d}",
            price=fare["price"], currency=currency, date=fare["date"], cap=cap,
            watch_id=watch_id, minutes=CHECK_INTERVAL_MINUTES
        ))

    @flight.command(name="watches")
    async def watches(self, ctx):
        """List fare alerts configured for this server."""
        language = await self._get_language(ctx.guild)
        entries = await self.config.guild(ctx.guild).watches()
        if not entries:
            await ctx.send(MESSAGES[language]["watches_empty"])
            return
        lines = []
        for item in entries:
            if item.get("kind", "oneway") == "return":
                lines.append(MESSAGES[language]["return_watch_line"].format(
                    id=item["id"], origin=item["origin"], destination=item["destination"],
                    outbound_from=item["outbound_from"], outbound_to=item["outbound_to"],
                    return_from=item["return_from"], return_to=item["return_to"],
                    price=item["last_price"], currency=item["currency"], lowest=item["lowest_price"],
                ))
            else:
                lines.append(MESSAGES[language]["watch_line"].format(
                    id=item["id"], origin=item["origin"], destination=item["destination"],
                    period=f"{item['year']}-{item['month']:02d}", price=item["last_price"],
                    currency=item["currency"], lowest=item["lowest_price"]
                ))
        await ctx.send("\n".join(lines))

    @flight.command(name="unwatch")
    async def unwatch(self, ctx, watch_id: str):
        """Remove a fare alert by its ID."""
        language = await self._get_language(ctx.guild)
        config = self.config.guild(ctx.guild).watches
        entries = await config()
        remaining = [item for item in entries if item["id"] != watch_id]
        if len(remaining) == len(entries):
            await ctx.send(MESSAGES[language]["watch_unknown"])
            return
        await config.set(remaining)
        await ctx.send(MESSAGES[language]["watch_removed"].format(watch_id=watch_id))

    @flight.command(name="check")
    async def check(self, ctx):
        """Check this server's tracked fares now."""
        language = await self._get_language(ctx.guild)
        await ctx.send(MESSAGES[language]["checking"])
        await self._check_guild(ctx.guild)
        await ctx.send(MESSAGES[language]["check_done"])

    @flight.command(name="language")
    async def set_language(self, ctx, language: str):
        """Set this server's language to auto, Czech (cs), or English (en)."""
        language = language.lower()
        current = await self._get_language(ctx.guild)
        if language not in {"auto", "cs", "en"}:
            await ctx.send(MESSAGES[current]["language_invalid"])
            return
        await self.config.guild(ctx.guild).language.set(language)
        selected = await self._get_language(ctx.guild)
        await ctx.send(MESSAGES[selected]["language_set"].format(language=selected))

    @staticmethod
    def _validate_route(origin, destination, currency, language):
        messages = MESSAGES[language]
        if not AIRPORT_RE.fullmatch(origin.upper()) or (
            destination is not None and not AIRPORT_RE.fullmatch(destination.upper())
        ):
            raise ValueError(messages["invalid_airport"])
        if destination is not None and origin.upper() == destination.upper():
            raise ValueError(messages["same_airport"])
        if not re.fullmatch(r"[A-Z]{3}", currency.upper()):
            raise ValueError(messages["invalid_currency"])

    @classmethod
    def _validate_search(cls, origin, destination, year, month, currency, language):
        cls._validate_route(origin, destination, currency, language)
        messages = MESSAGES[language]
        if not 1 <= month <= 12 or not 2024 <= year <= 2035:
            raise ValueError(messages["invalid_date"])
        if date(year, month, 1) < date.today().replace(day=1):
            raise ValueError(messages["past_month"])

    @staticmethod
    def _parse_date_window(date_from, date_to, language):
        try:
            start = date.fromisoformat(date_from)
            end = date.fromisoformat(date_to)
        except ValueError as exc:
            raise ValueError(MESSAGES[language]["invalid_date_range"]) from exc
        if (
            start > end
            or start < date.today()
            or any(bound.year < 2024 or bound.year > 2035 for bound in (start, end))
        ):
            raise ValueError(MESSAGES[language]["invalid_date_range"])
        return start, end

    @staticmethod
    def _get_month_window(year, month, language):
        if not 1 <= month <= 12 or not 2024 <= year <= 2035:
            raise ValueError(MESSAGES[language]["invalid_date"])
        start = date(year, month, 1)
        if start < date.today().replace(day=1):
            raise ValueError(MESSAGES[language]["past_month"])
        end = date(year, month, monthrange(year, month)[1])
        return start, end

    async def _get_language(self, guild):
        language = await self.config.guild(guild).language()
        if language in {"cs", "en"}:
            return language
        locale = str(getattr(guild, "preferred_locale", "en-US")).lower()
        return "cs" if locale.startswith("cs") else "en"

    @staticmethod
    def _user_error(error, language, sentinel=None, sentinel_key=None):
        if sentinel is not None and str(error) == sentinel:
            return MESSAGES[language][sentinel_key]
        if str(error) in MESSAGES[language].values():
            return str(error)
        return MESSAGES[language]["api_error"]

    async def _get_api(self, currency):
        if currency not in self._apis:
            async with self._api_lock:
                if currency not in self._apis:
                    self._apis[currency] = await asyncio.to_thread(RyanairAPI, currency)
        return self._apis[currency]

    async def _fetch_fares(self, origin, destination, year, month, currency):
        api = await self._get_api(currency)
        date_from = date(year, month, 1)
        date_to = date(year, month, monthrange(year, month)[1])
        flights = await asyncio.to_thread(
            api.get_cheapest_flights,
            origin,
            date_from,
            date_to,
            destination_airport=destination,
        )
        fares = [
            {
                "date": flight.departureTime.date().isoformat(),
                "price": float(flight.price),
            }
            for flight in flights
            if flight.destination.upper() == destination
        ]
        fares.sort(key=lambda item: item["price"])
        if not fares:
            raise ValueError("NO_FARES")
        return fares

    async def _fetch_return_fares(
        self, origin, destination, outbound_start, outbound_end,
        return_start, return_end, currency,
    ):
        api = await self._get_api(currency)
        trips = await asyncio.to_thread(
            api.get_cheapest_return_flights,
            origin,
            outbound_start,
            outbound_end,
            return_start,
            return_end,
            destination_airport=destination,
        )
        if not trips:
            raise ValueError("NO_RETURN_FARES")
        return trips

    @staticmethod
    def _booking_url(origin, destination, departure, return_date=None):
        query = urlencode(
            {
                "adults": 1,
                "teens": 0,
                "children": 0,
                "infants": 0,
                "dateOut": departure,
                "dateIn": return_date or "",
                "isConnectedFlight": "false",
                "discount": 0,
                "promoCode": "",
                "originIata": origin,
                "destinationIata": destination,
            }
        )
        return f"https://www.ryanair.com/gb/en/trip/flights/select?{query}"

    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def check_prices(self):
        for guild in self.bot.guilds:
            await self._check_guild(guild)

    @check_prices.before_loop
    async def before_check_prices(self):
        await self.bot.wait_until_ready()

    async def _check_guild(self, guild):
        config = self.config.guild(guild).watches
        watches = await config()
        changed = False
        cache = {}
        language = await self._get_language(guild)
        for item in watches:
            is_return = item.get("kind", "oneway") == "return"
            if is_return:
                key = (
                    "return", item["origin"], item["destination"], item["outbound_from"],
                    item["outbound_to"], item["return_from"], item["return_to"], item["currency"],
                )
            else:
                key = (
                    "oneway", item["origin"], item["destination"], item["year"],
                    item["month"], item["currency"],
                )
            try:
                if key not in cache:
                    if is_return:
                        trips = await self._fetch_return_fares(
                            item["origin"], item["destination"],
                            date.fromisoformat(item["outbound_from"]),
                            date.fromisoformat(item["outbound_to"]),
                            date.fromisoformat(item["return_from"]),
                            date.fromisoformat(item["return_to"]), item["currency"],
                        )
                        trip = min(trips, key=lambda candidate: candidate.totalPrice)
                        cache[key] = {
                            "price": float(trip.totalPrice),
                            "date": trip.outbound.departureTime.date().isoformat(),
                            "return_date": trip.inbound.departureTime.date().isoformat(),
                            "out_price": float(trip.outbound.price),
                            "return_price": float(trip.inbound.price),
                        }
                    else:
                        fares = await self._fetch_fares(
                            item["origin"], item["destination"], item["year"],
                            item["month"], item["currency"],
                        )
                        cache[key] = fares[0]
                    await asyncio.sleep(1)
                fare = cache[key]
                current_price = fare["price"]
                previous_price = item.get("last_price")
                new_minimum = current_price < item.get("lowest_price", current_price)
                crossed_limit = (
                    item.get("max_price") is not None
                    and current_price <= item["max_price"]
                    and (previous_price is None or previous_price > item["max_price"])
                )
                dates_changed = fare["date"] != item.get("last_date")
                if is_return:
                    dates_changed = dates_changed or fare["return_date"] != item.get("last_return_date")
                if previous_price is not None and (current_price != previous_price or dates_changed):
                    await self._notify(
                        guild, item, fare, previous_price, new_minimum, crossed_limit,
                        language, is_return,
                    )
                item["last_price"] = current_price
                item["lowest_price"] = min(item.get("lowest_price", current_price), current_price)
                item["last_date"] = fare["date"]
                if is_return:
                    item["last_return_date"] = fare["return_date"]
                    item["last_out_price"] = fare["out_price"]
                    item["last_return_price"] = fare["return_price"]
                changed = True
            except Exception as exc:
                log.warning("Fare check failed for %s -> %s: %s", item.get("origin"), item.get("destination"), exc)
        if changed:
            await config.set(watches)

    async def _notify(
        self, guild, item, fare, previous_price, new_minimum, crossed_limit, language, is_return
    ):
        channel = guild.get_channel(item["channel_id"])
        if channel is None:
            return
        currency = item["currency"]
        current_price = fare["price"]
        dates_changed = fare["date"] != item.get("last_date")
        if is_return:
            dates_changed = dates_changed or fare["return_date"] != item.get("last_return_date")
        if new_minimum:
            headline = MESSAGES[language]["minimum"]
        elif current_price == previous_price and dates_changed:
            headline_key = "return_changed" if is_return else "day_changed"
            headline = MESSAGES[language][headline_key]
        elif current_price < previous_price:
            headline = MESSAGES[language]["price_down"]
        else:
            headline = MESSAGES[language]["price_changed"]
        extra = MESSAGES[language]["limit_crossed"] if crossed_limit else ""
        booking_url = self._booking_url(
            item["origin"], item["destination"], fare["date"],
            fare.get("return_date") if is_return else None,
        )
        try:
            if is_return:
                message = MESSAGES[language]["return_alert"].format(
                    headline=headline, origin=item["origin"], destination=item["destination"],
                    previous=previous_price, current=current_price, currency=currency, limit=extra,
                    previous_out_date=item.get("last_date"), out_date=fare["date"],
                    out_price=fare["out_price"],
                    previous_return_date=item.get("last_return_date"),
                    return_date=fare["return_date"], return_price=fare["return_price"],
                    url=booking_url,
                )
            else:
                message = MESSAGES[language]["oneway_alert"].format(
                    headline=headline, origin=item["origin"], destination=item["destination"],
                    previous=previous_price, current=current_price, currency=currency,
                    previous_date=item.get("last_date"), date=fare["date"],
                    limit=extra, url=booking_url,
                )
            await channel.send(message)
        except Exception:
            log.exception("Unable to send fare alert to channel %s", item["channel_id"])


async def setup(bot):
    await bot.add_cog(FlightWatch(bot))
