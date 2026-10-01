# JobFinder

[English](README.md) | [Čeština](README.cs.md)

JobFinder gathers listings from Czech public APIs, international job APIs, and custom RSS/Atom feeds. It normalizes them into consistent Discord embeds, matches private user profiles, and can publish a separate public news feed. Commands work as both slash and Red prefix commands. Responses and embeds use English or Czech based on the server locale; users can override their alert language.

## Installation

```text
[p]repo add TestCogs https://github.com/rex2630/TestCogs
[p]cog install TestCogs jobfinder
[p]load jobfinder
```

Red does not publish third-party slash commands automatically. As the bot owner, enable the groups and sync them once (replace `?` with your bot's prefix):

```text
?slash enable jobset slash
?slash enable job slash
?slash sync <server>
```

Then use `/jobset channel` and select the channel from Discord's channel picker; do not type `#job-news` as literal text. If commands still do not appear, check `?slash list` and restart the Discord client.

## Sources and update cadence

These sources are enabled by default:

| Source | Integration | Default check |
| --- | --- | --- |
| [Síť práce](https://sitprace.cz/pro-vyvojare) | Public JSON API; Czech listings and the site's daily refreshed MPSV dataset, latest 100 | 60 min |
| [Upjobs](https://upjobs.cz/api/v1/jobs) | Public paginated JSON API; structured salary, location, work mode, contract, and seniority | 60 min |
| [MPSV / Czech Labour Office](https://data.mpsv.cz/web/data/prirustky-volnych-mist-za-celou-cr) | Official daily increment files for listings changed on recent days; avoids downloading the 185 MB full snapshot | 24 h |
| [Remotive](https://remotive.com/api/remote-jobs) | Public remote-job API | 6 h |
| [Arbeitnow](https://www.arbeitnow.com/api/job-board-api) | Public job-board API | 60 min |

The polling loop runs every 5 minutes and applies a per-source cache shared across the bot's servers. The default source intervals above are independent. `/jobset interval <minutes>` changes the interval for Czech and custom feeds; Remotive remains capped at no more than four requests per day. The first successful fetch seeds the database without posting old listings; new listings are delivered on later checks.

Jobs.cz and Prace.cz do not publish a general, unauthenticated jobs API for this use. Their partner integrations need access from the provider, so this cog does not scrape their pages. The [StartupJobs API](https://firmy.startupjobs.cz/cs/articles/9506864-propojeni-s-vasim-ats) is scoped to a company's own listings and requires its bearer token; [Careerjet's partner API](https://www.careerjet.cz/partners/api) needs a publisher key and the originating user's IP. These are not universal server-wide feeds. Custom RSS/Atom feeds can be added when a portal or employer provides one. Some MPSV listings intentionally omit employer or listing URL details; the embed identifies the Labour Office as the source and links to its dataset.

## User preferences

Run `/job preferences` to open a private editor. It includes a text-filter modal and dropdowns; the profile itself is only shown ephemerally or in DM.

| Preference | Matching behavior |
| --- | --- |
| Included keywords | At least one phrase must appear in listing text |
| Excluded keywords | Any match suppresses the listing |
| Cities or regions | Match against the listing's location field |
| Contract types | Supports common equivalents such as HPP/full-time, DPP, DPČ/part-time, and IČO/contractor |
| Salary range | Optional minimum and maximum in CZK per month; hourly MPSV values are estimated monthly for matching |
| Work mode | Any, remote, hybrid, or on-site; a selected mode requires a provider to identify the mode |
| Seniority | Any, junior, mid-level, senior, or lead/management |
| Alert frequency | Send a match after the next source check, or batch matches in a daily digest at 18:00 UTC |
| Alert language | Follow the server locale, Czech, or English |

At least one keyword, location, or contract type is required before alerts can be enabled. Filters are applied to the fields each provider supplies, so an unknown work mode or missing salary cannot satisfy a strict mode or salary filter.

Prefix users can set a basic profile directly:

```text
[p]job set "python, backend" "Praha, Brno" "HPP, DPP"
```

Other personal commands:

```text
/job preferences
/job enable
/job disable
/job language auto
```

## Alerts and embeds

Personal alerts are private DMs by default. Each embed has a linked job title when the provider supplies a listing URL, employer, location, salary, contract type, work mode, seniority, category, publication date, and a profile-match score when sent to a user. The source is shown above and below the embed, with a link to the provider. Listings without a direct URL still show the provider's source link.

To publish new listings in a public news channel, configure the channel and enable news posts:

```text
/jobset channel #job-news
/jobset news true
/jobset role @JobAlerts
```

Public posts are off by default. New listings are grouped into small embed batches; the optional role is mentioned once per batch run. `/jobset news false` disables the public feed while personal DMs keep working.

## Server commands

| Command | Description |
| --- | --- |
| `/jobset sources` | List configured feeds and providers |
| `/jobset source <url> [name]` | Add an RSS or Atom feed |
| `/jobset remove <index>` | Remove a source by its displayed number |
| `/jobset interval <minutes>` | Set the default interval for Czech and custom sources, 5 minutes to 24 hours |
| `/jobset run` | Check due sources now |
| `/jobset channel <channel>` | Select the public news channel |
| `/jobset news <true\|false>` | Enable or disable public news posts |
| `/jobset role [role]` | Set or clear the role mentioned in public posts |

## Data and limits

Filters and personal alert settings are stored in Red Config. Source settings are stored per server. Normalized listings and per-user delivery history are kept in a local SQLite database so a listing is not sent twice. Disable personal notifications with `/job disable`; Discord must allow DMs from server members. Red's user-data deletion request clears the saved profile and delivery history.

Public APIs can change their fields or availability. Some providers expose only recent listings or omit salary, employer, or location. MPSV data is refreshed daily and some employers ask the Labour Office to hide their identity and direct listing URL. JobFinder links to the provider and does not submit applications.
