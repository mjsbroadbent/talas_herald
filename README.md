# The Talas Herald

The website of the *Talas Herald* (*Talas Jarchysy*), the English-language daily of Talas, Irad, published Monday to Saturday since March 1993. Part of the Irad worldbuilding project, alongside [IradOne](https://iradone.com/) and the [Irad wiki](https://irad.wiki/).

It's a static site for GitHub Pages. A GitHub Action writes each morning's edition with Claude from real Central Asian news, in the Herald's own voice: court records, council debates, numbers, several voices and the official reply, with the usual self-censorship about the governing family.

## Setup

1. Add a repository secret named `ANTHROPIC_API_KEY` (Settings > Secrets and variables > Actions).
2. Turn on Pages: Settings > Pages > Deploy from a branch > `main`, folder `/ (root)`.
3. If you use a custom domain, set it in Settings > Pages (this creates a `CNAME` file) and put the same address in `site_url` in `newsroom/config.json`, then run `python newsroom/herald.py build`.
4. To publish now: Actions > Talas Herald newsroom > Run workflow.

## The edition

- **When:** Monday to Saturday, no Sunday paper. The workflow wakes hourly from 05:41 to 12:41 Irad Time and runs `herald.py due` (free). From 05:30 the edition is due; the run publishes `edition.stories` stories (2) and retries hourly until 13:00 if nothing usable came up.
- **Saturday:** The Herald's View, an unsigned editorial on one of the week's stories.
- **IradOne overlap:** the run reads `iradone.com/news/index.json`, which lists the real report behind each IradOne story. On about one edition day in three, triage covers one of those same reports, so readers can compare the two outlets. The Herald story links to IradOne's.
- **Bylines** come from `data/staff.json`, by section beat. Edit the staff list freely; each entry needs `from`/`to` years and `beats`.
- `python newsroom/herald.py due --preview 14` shows the next two weeks: volume and issue numbers, editorials, overlap days.

## How a run works

1. **Fetch** the feeds in `newsroom/config.json`. Items older than 96 hours, already seen, or matching `skip_keywords` are dropped.
2. **Triage** (cheap model) picks the edition's items, with section and angle.
3. **Write** (writer model) with `newsroom/prompts/house_style.md` plus the lore (`quickref.md` and `canon.md`, checked out from `mjsbroadbent/irad_persona_skill` on every run).
4. **Check**: dropped if it names anyone on `real_people_watchlist`, copies 9+ words from the source, or is under 300 words.
5. **Build** (`newsroom/build.py`) the whole site.

## The archive

`data/archive.json` holds 112 archive entries from 1996 to 2026, one for every Herald article cited in the Irad wiki, with the same headline and date as the citation. Each archive page shows the article's opening and a subscriber wall, like a real paper's archive. Entries are listed by year at `archive/` with a headline search. The front page's "From the archive" box picks stories from around today's date in past years. To add an entry, append to `data/archive.json` and rebuild.

## Other pages

| Page | Made from |
|---|---|
| Front page, sections, stories | `data/stories/*.json` |
| About, Subscribe, Corrections, Contact, Advertise, Jarchy, 404 | `content/*.html` (first line `<!-- title: ... -->`) |
| Our journalists | `data/staff.json` |
| TV listings and "Television tonight" | IradOne's own `schedule.js`/`schedule.json`, loaded from iradone.com (the Herald publishes IradOne's schedules) |
| Photo credits | `photos/credits.json` |

The metered paywall (`assets/herald.js`) counts articles per month in the reader's browser; after `meter.free_articles` (5) a subscribe panel appears, which the reader can always close. Set it to 0 to turn the meter off.

## Everyday changes

| To change | Edit |
|---|---|
| Stories per day, days, time, editorial day | `edition` in `newsroom/config.json` |
| Models, feeds, skip words | `newsroom/config.json` |
| Voice and editorial rules | `newsroom/prompts/house_style.md` |
| Photos | add to `photos/` with a `credits.json` entry, then list in `newsroom/images.json` |
| Layout | `newsroom/templates/page.html`, `assets/herald.css`, then `python newsroom/herald.py build` |
| Fix or remove a story | edit or delete its file in `data/stories/`, then `build` |

## Local testing

```
python newsroom/herald.py build                                   # rebuild pages only
python newsroom/herald.py run --dry-run --force                   # real run, writes nothing
python newsroom/herald.py run --mock --force --fixtures DIR       # no API key; feeds from DIR/0.xml..., IradOne from DIR/iradone.json
```
