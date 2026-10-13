"""Build the Talas Herald static site from data/.

Writes index.html, news/<slug>.html, section/*.html, archive/ (index, years,
entries), staff.html, the static pages in content/, tv.html, feed.xml,
sitemap.xml and robots.txt.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NEWSROOM = ROOT / "newsroom"
DATA = ROOT / "data"
STORIES = DATA / "stories"
CONTENT = ROOT / "content"
CONFIG = json.loads((NEWSROOM / "config.json").read_text(encoding="utf-8"))
SITE = CONFIG["site_url"].rstrip("/")
IRAD_TZ = dt.timezone(dt.timedelta(hours=6))
TEMPLATE = (NEWSROOM / "templates" / "page.html").read_text(encoding="utf-8")
SECTIONS = ["News", "Courts", "Business", "Across Irad", "Culture", "Sport", "Opinion"]
ARCHIVE_SECTIONS = SECTIONS + ["Features", "Obituaries"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
PHOTO_CREDITS = json.loads((ROOT / "photos" / "credits.json").read_text(encoding="utf-8"))

e = html.escape


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:70].rstrip("-")


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")


def when(a: dict) -> dt.datetime:
    return dt.datetime.fromisoformat(a["published"]).astimezone(IRAD_TZ)


def long_date(d: dt.date) -> str:
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} {d.year}"


def short_date(d: dt.date) -> str:
    return f"{d.day} {MONTHS[d.month - 1][:3]} {d.year}"


def inline(text: str) -> str:
    """Escape, then allow *italics* (titles of books, films, papers)."""
    t = e(text, quote=False)
    return re.sub(r"\*([^*\n]+)\*", r"<i>\1</i>", t)


def plain(text: str) -> str:
    return re.sub(r"\*([^*\n]+)\*", r"\1", text)


def issue_number(day: dt.date) -> tuple[int, int]:
    first = dt.date.fromisoformat(CONFIG["edition"]["first_issue"])
    weeks, rest = divmod((day - first).days, 7)
    number = weeks * 6 + min(rest, 5) + 1
    volume = day.year - first.year + (1 if (day.month, day.day) >= (first.month, first.day) else 0)
    return volume, number


def staff_index() -> dict:
    staff = json.loads((DATA / "staff.json").read_text(encoding="utf-8"))["staff"]
    return {s["name"]: s for s in staff}


STAFF = staff_index()


def byline_html(name: str, base: str) -> str:
    if name in STAFF:
        return f'<a href="{base}staff.html#{STAFF[name]["id"]}">{e(name)}</a>'
    return e(name)


def photo_credit(img: str | None) -> str:
    if not img:
        return ""
    c = PHOTO_CREDITS.get(img) or {}
    who = re.sub(r"<[^>]+>", "", c.get("author") or "")
    lic = c.get("licence") or ""
    return " · ".join(x for x in (who, lic) if x)


def page(title: str, content: str, *, base: str = "", description: str = "", canonical: str = "",
         og_image: str = "", og_type: str = "website", body_class: str = "", scripts: str = "",
         day: dt.date | None = None) -> str:
    day = dt.datetime.now(IRAD_TZ).date()  # the top bar always shows today's paper
    vol, no = issue_number(day)
    out = TEMPLATE
    for k, v in {
        "title": e(title), "description": e(description or "The Talas Herald: news from Talas and across Irad since 1993."),
        "canonical": f"{SITE}/{canonical}", "og_image": f"{SITE}/{og_image or 'assets/herald-og.png'}",
        "og_type": og_type, "base": base, "body_class": body_class, "scripts": scripts,
        "issue": f"Vol. {vol} · No. {no:,}", "year": str(day.year), "content": content,
        "meter": str(CONFIG.get("meter", {}).get("free_articles", 5)),
    }.items():
        out = out.replace("{{" + k + "}}", v)
    return out


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

def load_stories() -> list[dict]:
    arts = [json.loads(p.read_text(encoding="utf-8")) for p in STORIES.glob("*.json")]
    arts.sort(key=lambda a: a["published"], reverse=True)
    return arts


def load_archive() -> list[dict]:
    path = DATA / "archive.json"
    if not path.exists():
        return []
    items = json.loads(path.read_text(encoding="utf-8"))
    for a in items:
        a.setdefault("slug", slugify(a["headline"]))
    items.sort(key=lambda a: a["date"], reverse=True)
    return items


def story_url(a: dict, base: str) -> str:
    return f"{base}news/{a['slug']}.html"


def archive_url(a: dict, base: str) -> str:
    return f"{base}archive/{a['date'][:4]}/{a['slug']}.html"


# --------------------------------------------------------------------------
# fragments
# --------------------------------------------------------------------------

def kicker(a: dict, base: str) -> str:
    sec = a.get("section", "News")
    label = "The Herald's View" if a.get("kind") == "editorial" else sec
    return f'<a class="kicker" href="{base}section/{slugify(sec)}.html">{e(label)}</a>'


def figure(img: str | None, base: str, cls: str = "", caption: str = "") -> str:
    if not img:
        return ""
    credit = photo_credit(img)
    cap = ""
    if caption or credit:
        cap = (f'<figcaption>{inline(caption)}'
               + (f' <span class="credit">Photo: {e(credit)}</span>' if credit else "") + "</figcaption>")
    return f'<figure class="{cls}"><img src="{base}{e(img)}" alt="" loading="lazy">{cap}</figure>'


def teaser(a: dict, base: str, *, size: str = "m", image: bool = True, dek: bool = True) -> str:
    url = story_url(a, base)
    img = ""
    if image and a.get("image"):
        img = f'<a class="teaser-img" href="{url}"><img src="{base}{e(a["image"])}" alt="" loading="lazy"></a>'
    meta = f'<p class="meta">{e(a["byline"])} · <time datetime="{e(a["published"])}">{short_date(when(a).date())}</time></p>'
    return (f'<article class="teaser teaser-{size}">{img}<div class="teaser-text">{kicker(a, base)}'
            f'<h3><a href="{url}">{inline(a["headline"])}</a></h3>'
            + (f'<p class="dek">{inline(a["dek"])}</p>' if dek else "") + meta + "</div></article>")


def headline_list(items: list[dict], base: str, with_time: bool = True) -> str:
    lis = []
    for a in items:
        t = when(a)
        today = dt.datetime.now(IRAD_TZ).date()
        label = f"{t:%H:%M}" if t.date() == today else f"{t.day} {MONTHS[t.month - 1][:3]}"
        stamp = f'<span class="li-time">{label}</span>' if with_time else ""
        lis.append(f'<li>{stamp}<a href="{story_url(a, base)}">{inline(a["headline"])}</a>'
                   f'<span class="li-sec">{e(a["section"])}</span></li>')
    return '<ol class="headline-list">' + "".join(lis) + "</ol>"


def on_this_day(archive: list[dict], day: dt.date, base: str, n: int = 3) -> str:
    if not archive:
        return ""
    def distance(a):
        d = dt.date.fromisoformat(a["date"])
        delta = abs((d.replace(year=2000) - day.replace(year=2000)).days) if not (d.month == 2 and d.day == 29) else 400
        return min(delta, 366 - delta)
    seed = int(hashlib.sha1(day.isoformat().encode()).hexdigest()[:8], 16)
    ranked = sorted(archive, key=lambda a: (distance(a), (int(hashlib.sha1(a["slug"].encode()).hexdigest()[:8], 16) ^ seed)))
    lis = "".join(
        f'<li><span class="otd-year">{a["date"][:4]}</span><a href="{archive_url(a, base)}">{inline(a["headline"])}</a></li>'
        for a in ranked[:n])
    return (f'<section class="box otd"><h2 class="box-head">From the archive</h2>'
            f'<p class="box-sub">Around this date in Herald history</p><ul>{lis}</ul>'
            f'<p class="more"><a href="{base}archive/index.html">Browse the archive since 1993 &rarr;</a></p></section>')


def tv_box(base: str) -> str:
    return (f'<section class="box tv-box"><h2 class="box-head">Television tonight</h2>'
            f'<p class="box-sub">IradOne, Irad Time</p>'
            f'<ol class="tv-list" data-tv="evening"><li class="tv-loading">Loading listings…</li></ol>'
            f'<p class="more"><a href="{base}tv.html">Full listings &rarr;</a></p></section>')


def paper_box(day: dt.date, base: str) -> str:
    vol, no = issue_number(day)
    price = "8 som" if day.weekday() == 5 else "5 som"
    return (f'<section class="box paper-box"><h2 class="box-head">Today\'s paper</h2>'
            f'<p><strong>{long_date(day)}</strong><br>Vol. {vol}, No. {no:,} · {price}</p>'
            f'<p class="box-sub">On sale at kiosks in Talas, Nurzhan and Iyık Janyl from 6am. '
            f'<a href="{base}subscribe.html">Subscribe</a> for home delivery or the digital edition.</p></section>')


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------

def article_page(a: dict, stories: list[dict]) -> str:
    base = "../"
    t = when(a)
    is_ed = a.get("kind") == "editorial"
    paras = []
    pq_at = 3 if len(a["paragraphs"]) > 5 else None
    for i, p in enumerate(a["paragraphs"]):
        paras.append(f"<p>{inline(p)}</p>")
        if pq_at is not None and i == pq_at - 1 and a.get("pull_quote"):
            by = f'<cite>{e(a.get("pull_quote_by", ""))}</cite>' if a.get("pull_quote_by") else ""
            paras.append(f'<blockquote class="pull">“{inline(a["pull_quote"])}”{by}</blockquote>')
    dateline = f'<span class="dateline">{e(a["dateline"])} — </span>' if a.get("dateline") else ""
    if paras and dateline:
        paras[0] = paras[0].replace("<p>", f"<p>{dateline}", 1)

    related = [s for s in stories if s["slug"] != a["slug"] and s.get("kind", "story") == "story"
               and s["section"] == a["section"]][:4]
    if len(related) < 4:
        related += [s for s in stories if s["slug"] != a["slug"] and s not in related
                    and s.get("kind", "story") == "story"][:4 - len(related)]

    notes = []
    if a.get("iradone") and a["iradone"].get("slug"):
        notes.append(f'<p class="note-iradone">IradOne also reported this story: '
                     f'<a href="https://iradone.com/news/{e(a["iradone"]["slug"])}.html" rel="noopener">'
                     f'{inline(a["iradone"]["headline"])}</a>.</p>')
    if is_ed:
        about = [s for s in stories if s["slug"] in a.get("about", [])]
        if about:
            notes.append("<p class=\"note-about\">This editorial discusses: " + "; ".join(
                f'<a href="{story_url(s, base)}">{inline(s["headline"])}</a>' for s in about) + ".</p>")
    src = a.get("source")
    if src and src.get("url"):
        notes.append(f'<p class="note-source">Adapted for the Irad worldbuilding project from real reporting: '
                     f'<a href="{e(src["url"])}" rel="noopener nofollow">{e(src.get("name", "source"))}</a>.</p>')

    tags = "".join(f'<li>{e(t_)}</li>' for t_ in a.get("tags", []))
    byline = ("<p class=\"byline\"><span class=\"view-label\">The Herald's View</span> · "
              if is_ed else f'<p class="byline">By {byline_html(a["byline"], base)} · ')
    body = f"""
<article class="story{' editorial' if is_ed else ''}" data-meter="{e(a['slug'])}">
  <header class="story-head">
    {kicker(a, base)}
    <h1>{inline(a['headline'])}</h1>
    <p class="story-dek">{inline(a['dek'])}</p>
    {byline}<time datetime="{e(a['published'])}">{long_date(t.date())}, {t:%H:%M} IRT</time></p>
  </header>
  {figure(a.get('image'), base, 'story-figure')}
  <div class="story-body">
    {''.join(paras)}
    {'<ul class="tags">' + tags + '</ul>' if tags else ''}
    <div class="story-notes">{''.join(notes)}</div>
  </div>
</article>
<aside class="story-more">
  <h2 class="rule-head">More from the Herald</h2>
  <div class="grid-4">{''.join(teaser(r, base, size='s', dek=False) for r in related)}</div>
</aside>"""
    return page(f"{plain(a['headline'])} | The Talas Herald", body, base=base, description=plain(a["dek"]),
                canonical=f"news/{a['slug']}.html", og_image=a.get("image") or "", og_type="article",
                body_class="page-story", day=t.date())


def front_page(stories: list[dict], archive: list[dict]) -> str:
    base = ""
    today = dt.datetime.now(IRAD_TZ).date()
    news = [s for s in stories if s.get("kind", "story") == "story"]
    eds = [s for s in stories if s.get("kind") == "editorial"]
    if not news:
        return page("The Talas Herald", "<p>No stories yet.</p>")
    lead, rest = news[0], news[1:]
    latest = headline_list(rest[:6], base)
    band = "".join(teaser(a, base, size="m") for a in rest[:3])
    more = rest[3:]

    blocks = []
    for sec in ["News", "Courts", "Business", "Across Irad", "Culture", "Sport"]:
        items = [a for a in more if a["section"] == sec][:3]
        if not items:
            items = [a for a in news if a["section"] == sec][:3]
        if not items:
            continue
        first, others = items[0], items[1:]
        blocks.append(
            f'<section class="sec-block"><h2 class="rule-head"><a href="section/{slugify(sec)}.html">{e(sec)}</a></h2>'
            + teaser(first, base, size="s", dek=True)
            + "".join(f'<h3 class="sec-more"><a href="{story_url(o, base)}">{inline(o["headline"])}</a></h3>' for o in others)
            + "</section>")

    opinion = ""
    if eds:
        ed = eds[0]
        opinion = (f'<section class="box view-box"><h2 class="box-head">The Herald\'s View</h2>'
                   f'<h3><a href="{story_url(ed, base)}">{inline(ed["headline"])}</a></h3>'
                   f'<p>{inline(ed["dek"])}</p>'
                   + "".join(f'<p class="prev-view"><a href="{story_url(x, base)}">{inline(x["headline"])}</a></p>' for x in eds[1:3])
                   + '</section>')

    body = f"""
<div class="front">
  <section class="front-top">
    <div class="lead">
      <a class="lead-img" href="{story_url(lead, base)}">{figure(lead.get('image'), base, 'lead-figure')}</a>
      {kicker(lead, base)}
      <h2 class="lead-head"><a href="{story_url(lead, base)}">{inline(lead['headline'])}</a></h2>
      <p class="dek">{inline(lead['dek'])}</p>
      <p class="meta">By {byline_html(lead['byline'], base)} · {short_date(when(lead).date())}</p>
    </div>
    <div class="latest">
      <h2 class="rule-head">Latest</h2>
      {latest}
    </div>
  </section>
  <section class="band grid-3">{band}</section>
  <div class="front-cols">
    <div class="front-main">{''.join(blocks)}</div>
    <aside class="front-side">
      {opinion}
      {paper_box(today, base)}
      {on_this_day(archive, today, base)}
      {tv_box(base)}
    </aside>
  </div>
</div>"""
    return page("The Talas Herald | News from Talas and across Irad", body, base=base,
                canonical="", body_class="page-front", scripts=tv_scripts(base))


def tv_scripts(base: str) -> str:
    return ('\n  <script src="https://iradone.com/sport.js"></script>'
            '\n  <script src="https://iradone.com/schedule.js"></script>'
            f'\n  <script src="{base}assets/tv.js"></script>')


def section_page(sec: str, stories: list[dict], archive: list[dict]) -> str:
    base = "../"
    items = [s for s in stories if s.get("section") == sec]
    old = [a for a in archive if a.get("section") == sec][:12]
    if items:
        top = teaser(items[0], base, size="l")
        rest = "".join(teaser(a, base, size="row") for a in items[1:40])
    else:
        top, rest = "<p class=\"empty\">No recent stories in this section.</p>", ""
    arch = ""
    if old:
        arch = ('<section class="sec-archive"><h2 class="rule-head">From the archive</h2><ul class="archive-list">'
                + "".join(f'<li><time>{short_date(dt.date.fromisoformat(a["date"]))}</time>'
                          f'<a href="{archive_url(a, base)}">{inline(a["headline"])}</a></li>' for a in old)
                + "</ul></section>")
    title = "Opinion" if sec == "Opinion" else sec
    body = (f'<header class="sec-head"><h1>{e(title)}</h1></header>'
            f'<div class="sec-page"><div class="sec-list">{top}{rest}</div>'
            f'<aside class="front-side">{arch}</aside></div>')
    return page(f"{title} | The Talas Herald", body, base=base, canonical=f"section/{slugify(sec)}.html",
                body_class="page-section")


def archive_entry_page(a: dict) -> str:
    base = "../../"
    d = dt.date.fromisoformat(a["date"])
    vol, no = issue_number(d) if d >= dt.date(1993, 3, 15) else (1, 1)
    paras = [f"<p>{inline(p)}</p>" for p in a["paragraphs"]]
    if paras and a.get("dateline"):
        paras[0] = paras[0].replace("<p>", f'<p><span class="dateline">{e(a["dateline"])} — </span>', 1)
    by = a.get("byline") or "Herald staff"
    byline = f'By {byline_html(by, base)}' if by not in ("Herald staff", "The Herald's View") else e(by)
    body = f"""
<article class="story archive-story">
  <header class="story-head">
    <p class="archive-flag"><a href="{base}archive/index.html">Archive</a> · <a href="{base}archive/{d.year}.html">{d.year}</a> · From the print edition of {long_date(d)}, Vol. {vol}, No. {no:,}</p>
    <span class="kicker">{e(a.get('section', 'News'))}</span>
    <h1>{inline(a['headline'])}</h1>
    <p class="story-dek">{inline(a.get('dek', ''))}</p>
    <p class="byline">{byline} · <time datetime="{a['date']}">{long_date(d)}</time></p>
  </header>
  <div class="story-body">
    {''.join(paras)}
    <div class="archive-wall">
      <p><strong>This article continues in the Herald archive.</strong> Subscribers can read every edition since March 1993 in full.</p>
      <p><a class="btn" href="{base}subscribe.html">Subscribe</a> <a class="btn btn-ghost" href="{base}login.html">Log in</a></p>
    </div>
  </div>
</article>"""
    return page(f"{plain(a['headline'])} ({d.year}) | Talas Herald archive", body, base=base,
                description=plain(a.get("dek", "")), canonical=f"archive/{d.year}/{a['slug']}.html",
                body_class="page-story page-archive", day=dt.datetime.now(IRAD_TZ).date())


def archive_year_page(year: int, items: list[dict], live: list[dict], years: list[int]) -> str:
    base = "../"
    rows = []
    merged = [(a["date"], "archive", a) for a in items] + [(when(s).date().isoformat(), "live", s) for s in live]
    merged.sort(key=lambda x: x[0], reverse=True)
    month = None
    for date, kind, a in merged:
        d = dt.date.fromisoformat(date)
        if d.month != month:
            if month is not None:
                rows.append("</ul>")
            month = d.month
            rows.append(f'<h2 class="month-head">{MONTHS[d.month - 1]} {year}</h2><ul class="archive-list">')
        url = archive_url(a, base) if kind == "archive" else story_url(a, base)
        rows.append(f'<li><time>{d.day} {MONTHS[d.month - 1][:3]}</time><a href="{url}">{inline(a["headline"])}</a>'
                    f'<span class="li-sec">{e(a.get("section", ""))}</span></li>')
    if month is not None:
        rows.append("</ul>")
    nav = " ".join(f'<a href="{y}.html"{" class=\"on\"" if y == year else ""}>{y}</a>' for y in years)
    body = (f'<header class="sec-head"><h1>Archive: {year}</h1><p class="sec-sub">Stories from the Herald '
            f'in {year}. Archive pages show the opening of each article; subscribers read the full text.</p></header>'
            f'<nav class="year-nav">{nav}</nav><div class="archive-year">{"".join(rows)}</div>')
    return page(f"{year} | Talas Herald archive", body, base=base, canonical=f"archive/{year}.html",
                body_class="page-archive-year")


def archive_index(archive: list[dict], live: list[dict]) -> tuple[str, list[int]]:
    base = "../"
    by_year: dict[int, int] = {}
    for a in archive:
        by_year[int(a["date"][:4])] = by_year.get(int(a["date"][:4]), 0) + 1
    for s in live:
        by_year[when(s).year] = by_year.get(when(s).year, 0) + 1
    years = sorted(by_year, reverse=True)
    eras = [
        ("1993–2006", "The territorial years", "The Herald was founded in March 1993, during the US territorial administration, by Medet Orozaliev and Margaret Hendricks."),
        ("2007", "Civil war", "The paper kept publishing through the Second Civil War and the fall of Talas."),
        ("2008–", "Under the ISEZ", "Courts, councils and public records under editor Elmira Beishenova (since 2009)."),
    ]
    era_html = "".join(f'<div class="era"><h3>{e(a)}</h3><p class="era-name">{e(b)}</p><p>{e(c)}</p></div>' for a, b, c in eras)
    cells = "".join(f'<a class="year-cell" href="{y}.html"><span class="y">{y}</span><span class="n">{by_year[y]} stories</span></a>' for y in years)
    search_items = [{"h": plain(a["headline"]), "d": a["date"], "u": f"{a['date'][:4]}/{a['slug']}.html", "s": a.get("section", "")} for a in archive]
    body = (f'<header class="sec-head"><h1>The archive</h1><p class="sec-sub">Every edition since Monday 15 March 1993. '
            f'Search the headlines, or browse by year.</p></header>'
            f'<div class="archive-search"><input type="search" id="archive-q" placeholder="Search archive headlines" aria-label="Search archive headlines">'
            f'<ul id="archive-results" class="archive-list"></ul></div>'
            f'<div class="eras">{era_html}</div><div class="year-grid">{cells}</div>'
            f'<script type="application/json" id="archive-data">{json.dumps(search_items, ensure_ascii=False)}</script>')
    return page("Archive | The Talas Herald", body, base=base, canonical="archive/index.html",
                body_class="page-archive-index"), years


def staff_page() -> str:
    staff = json.loads((DATA / "staff.json").read_text(encoding="utf-8"))["staff"]
    current = [s for s in staff if s["to"] is None]
    former = [s for s in staff if s["to"] is not None]
    def card(s):
        years = f'{s["from"]}–{s["to"] or ""}'.rstrip("–") if s["to"] else f'since {s["from"]}'
        return (f'<li id="{s["id"]}" class="staff-card"><h3>{e(s["name"])}</h3><p class="staff-role">{e(s["role"])} · {years}</p>'
                f'<p>{e(s["bio"])}</p></li>')
    body = (f'<header class="sec-head"><h1>Our journalists</h1></header><div class="prose">'
            f'<h2>The newsroom</h2><ul class="staff-list">{"".join(card(s) for s in current)}</ul>'
            f'<h2>Former staff</h2><ul class="staff-list">{"".join(card(s) for s in former)}</ul>'
            f'<p>Write to any of us at <em>firstname.lastname@talasherald.ir.ad</em>, or to the newsdesk at news@talasherald.ir.ad.</p></div>')
    return page("Our journalists | The Talas Herald", body, canonical="staff.html", body_class="page-prose")


def credits_page() -> str:
    rows = []
    for name, c in sorted(PHOTO_CREDITS.items()):
        if not (ROOT / name).exists():
            continue
        src = c.get("source") or ""
        rows.append(f'<li><strong>{e(Path(name).name)}</strong>: {e(c.get("alt", ""))}. '
                    f'{e(re.sub(r"<[^>]+>", "", c.get("author", "")))}, {e(c.get("licence", ""))}'
                    + (f' (<a href="{e(src)}" rel="noopener nofollow">source</a>)' if src else "") + "</li>")
    body = (f'<header class="sec-head"><h1>Photo credits</h1></header><div class="prose">'
            f'<p>Photographs on this site come from Wikimedia Commons and are used under their licences.</p>'
            f'<ul class="credits-list">{"".join(rows)}</ul></div>')
    return page("Photo credits | The Talas Herald", body, canonical="credits.html", body_class="page-prose")


def content_page(path: Path) -> tuple[str, str]:
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"<!--\s*title:\s*(.*?)\s*-->\s*", raw)
    title = m.group(1) if m else path.stem.title()
    body = raw[m.end():] if m else raw
    return f"{path.stem}.html", page(f"{title} | The Talas Herald", body, canonical=f"{path.stem}.html",
                                     body_class=f"page-prose page-{path.stem}")


def tv_page() -> str:
    body = ('<header class="sec-head"><h1>Television listings</h1><p class="sec-sub">IradOne, today and tomorrow. '
            'Times are Irad Time; the broadcast day runs from 5am. Listings supplied by IradOne.</p></header>'
            '<div class="tv-page"><section><h2 class="rule-head" data-tv-day="0">Today</h2><ol class="tv-list tv-full" data-tv="today"></ol></section>'
            '<section><h2 class="rule-head" data-tv-day="1">Tomorrow</h2><ol class="tv-list tv-full" data-tv="tomorrow"></ol></section></div>')
    return page("Television listings | The Talas Herald", body, canonical="tv.html", body_class="page-tv",
                scripts=tv_scripts(""))


def feed_xml(stories: list[dict]) -> str:
    items = []
    for a in stories[:30]:
        t = when(a)
        items.append(f"""  <item>
    <title>{e(plain(a['headline']))}</title>
    <link>{SITE}/news/{a['slug']}.html</link>
    <guid>{SITE}/news/{a['slug']}.html</guid>
    <pubDate>{t.strftime('%a, %d %b %Y %H:%M:%S %z')}</pubDate>
    <category>{e(a['section'])}</category>
    <description>{e(plain(a['dek']))}</description>
  </item>""")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0">\n<channel>\n'
            f"  <title>The Talas Herald</title>\n  <link>{SITE}/</link>\n"
            "  <description>News from Talas and across Irad.</description>\n  <language>en</language>\n"
            + "\n".join(items) + "\n</channel>\n</rss>\n")


def sitemap(urls: list[str]) -> str:
    rows = "\n".join(f"  <url><loc>{SITE}/{u}</loc></url>" for u in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + rows + "\n</urlset>\n")


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def build():
    stories = load_stories()
    archive = load_archive()
    urls = [""]

    keep = {f"{a['slug']}.html" for a in stories}
    for old in (ROOT / "news").glob("*.html"):
        if old.name not in keep:
            old.unlink()
    for a in stories:
        write(ROOT / "news" / f"{a['slug']}.html", article_page(a, stories))
        urls.append(f"news/{a['slug']}.html")

    for sec in SECTIONS:
        write(ROOT / "section" / f"{slugify(sec)}.html", section_page(sec, stories, archive))
        urls.append(f"section/{slugify(sec)}.html")

    for a in archive:
        write(ROOT / "archive" / a["date"][:4] / f"{a['slug']}.html", archive_entry_page(a))
        urls.append(f"archive/{a['date'][:4]}/{a['slug']}.html")
    idx, years = archive_index(archive, stories)
    write(ROOT / "archive" / "index.html", idx)
    urls.append("archive/index.html")
    for y in years:
        write(ROOT / "archive" / f"{y}.html", archive_year_page(
            y, [a for a in archive if a["date"].startswith(str(y))],
            [s for s in stories if when(s).year == y], years))
        urls.append(f"archive/{y}.html")

    write(ROOT / "staff.html", staff_page())
    write(ROOT / "credits.html", credits_page())
    write(ROOT / "tv.html", tv_page())
    urls += ["staff.html", "credits.html", "tv.html"]
    for path in sorted(CONTENT.glob("*.html")):
        name, text = content_page(path)
        write(ROOT / name, text)
        if name != "404.html":
            urls.append(name)

    write(ROOT / "index.html", front_page(stories, archive))
    write(ROOT / "feed.xml", feed_xml(stories))
    write(ROOT / "sitemap.xml", sitemap(urls))
    write(ROOT / "robots.txt", f"User-agent: *\nAllow: /\nSitemap: {SITE}/sitemap.xml\n")
    print(f"Built {len(stories)} stories and {len(archive)} archive entries", flush=True)


if __name__ == "__main__":
    build()
