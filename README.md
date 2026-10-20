# The Talas Herald

The website of the *Talas Herald* (*Talas Jarchysy*), the English-language daily of Talas, Irad, published Monday to Saturday since March 1993. Part of the Irad worldbuilding project, alongside [IradOne](https://iradone.com/) and the [Irad wiki](https://irad.wiki/).

It's a static site for GitHub Pages. Each morning's edition (Monday to Saturday, with The Herald's View on Saturdays) arrives as story files in `data/stories/`, written and committed from elsewhere; `newsroom/build.py` turns them and the rest of `data/` into the pages.

## Setup

1. Turn on Pages: Settings > Pages > Deploy from a branch > `main`, folder `/ (root)`.
2. If you use a custom domain, set it in Settings > Pages (this creates a `CNAME` file) and put the same address in `site_url` in `newsroom/config.json`, then run `python newsroom/build.py`.

## Building

```
python newsroom/build.py          # rebuild every page from data/
```

`newsroom/config.json` holds what the build needs: `site_url`, `site_name`, the first issue's date (for volume and issue numbers) and the paywall meter.

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
| Photos | add to `photos/` with a `credits.json` entry |
| Layout | `newsroom/templates/page.html`, `assets/herald.css`, then `python newsroom/build.py` |
| Fix or remove a story | edit or delete its file in `data/stories/`, then build |
| Staff list and bylines | `data/staff.json` (each entry needs `from`/`to` years and `beats`) |
