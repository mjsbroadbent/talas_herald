#!/usr/bin/env python3
"""Talas Herald newsroom.

Pulls real Central Asian news from RSS feeds, has Claude recast a few items as
Talas Herald stories set in Irad, and rebuilds the static site. The paper comes
out Monday to Saturday; on Saturdays it adds an editorial, The Herald's View.

  python newsroom/herald.py run      # publish today's edition if it is due, rebuild
  python newsroom/herald.py due      # is the edition due now? (--preview 14 shows the plan)
  python newsroom/herald.py build    # rebuild pages from data/ only

Options for run:
  --force          publish now, even on a Sunday or after today's edition is out
  --max N          news stories this run (default: edition.stories)
  --no-editorial   skip The Herald's View even on an editorial day
  --editorial      write The Herald's View now, whatever the day
  --dry-run        do everything except write files
  --fixtures DIR   read feeds from DIR/*.xml instead of the network (testing)
  --mock           fake the Claude calls (testing, no API key needed)

Environment:
  ANTHROPIC_API_KEY   required for real runs
  LORE_DIR            folder holding quickref.md and canon.md (default: _lore)
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NEWSROOM = ROOT / "newsroom"
DATA = ROOT / "data"
STORIES = DATA / "stories"
SEEN_FILE = DATA / "seen.json"
CONFIG = json.loads((NEWSROOM / "config.json").read_text(encoding="utf-8"))
EDITION = CONFIG["edition"]
IMAGES = {k: v for k, v in json.loads((NEWSROOM / "images.json").read_text(encoding="utf-8")).items()
          if not k.startswith("_")}
STAFF = json.loads((DATA / "staff.json").read_text(encoding="utf-8"))["staff"]

IRAD_TZ = dt.timezone(dt.timedelta(hours=6))  # Irad Time, UTC+6
SECTIONS = ["News", "Courts", "Business", "Across Irad", "Culture", "Sport"]
DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
USER_AGENT = "Mozilla/5.0 (compatible; TalasHeraldNewsroom/1.0)"

RUN_LOG: list[str] = []


def log(*args):
    line = " ".join(str(a) for a in args)
    RUN_LOG.append(line)
    print(line, file=sys.stderr, flush=True)


def save_run_log():
    text = "\n".join(RUN_LOG) + "\n"
    (NEWSROOM / "last_run.log").write_text(text, encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## Talas Herald newsroom run\n\n```\n" + text + "```\n")


def now_irad() -> dt.datetime:
    return dt.datetime.now(IRAD_TZ)


# --------------------------------------------------------------------------
# Feeds
# --------------------------------------------------------------------------

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def strip_html(text: str) -> str:
    text = TAG_RE.sub(" ", text or "")
    return WS_RE.sub(" ", html.unescape(text)).strip()


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def child_text(el, *names) -> str:
    for c in el:
        if local(c.tag) in names:
            if local(c.tag) == "link" and c.get("href"):
                return c.get("href")
            return (c.text or "").strip()
    return ""


def parse_date(value: str):
    if not value:
        return None
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            d = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


def parse_feed(raw: bytes, feed: dict) -> list[dict]:
    root = ET.fromstring(raw)
    items = []
    for e in (e for e in root.iter() if local(e.tag) in ("item", "entry")):
        title = strip_html(child_text(e, "title"))
        link = child_text(e, "link") or child_text(e, "guid", "id")
        summary = strip_html(child_text(e, "description", "summary", "content", "encoded"))
        published = parse_date(child_text(e, "pubDate", "published", "updated", "date"))
        if not title or not link:
            continue
        items.append({"source": feed["name"], "country": feed.get("country", ""), "title": title,
                      "url": link, "summary": summary[:1500],
                      "published": published.isoformat() if published else None})
    return items


def fetch_url(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_feeds(fixtures: Path | None) -> list[dict]:
    items = []
    for i, feed in enumerate(CONFIG["feeds"]):
        try:
            if fixtures:
                path = fixtures / f"{i}.xml"
                if not path.exists():
                    continue
                raw = path.read_bytes()
            else:
                raw = fetch_url(feed["url"])
            got = parse_feed(raw, feed)
            log(f"  {feed['name']}: {len(got)} items")
            items.extend(got)
        except Exception as exc:  # one broken feed must not stop the run
            log(f"  {feed['name']}: FAILED ({exc.__class__.__name__}: {exc})")
    return items


def fetch_iradone(fixtures: Path | None) -> list[dict]:
    """IradOne's recent stories, with the real report each was adapted from."""
    try:
        if fixtures:
            path = fixtures / "iradone.json"
            raw = path.read_bytes() if path.exists() else b"[]"
        else:
            raw = fetch_url(CONFIG["iradone_index"], 15)
        got = json.loads(raw)
        log(f"  IradOne: {len(got)} stories")
        return got
    except Exception as exc:
        log(f"  IradOne: FAILED ({exc.__class__.__name__}: {exc})")
        return []


def url_key(url: str) -> str:
    return hashlib.sha1(url.strip().encode()).hexdigest()[:16]


def load_seen() -> dict:
    return json.loads(SEEN_FILE.read_text(encoding="utf-8")) if SEEN_FILE.exists() else {}


def prune_seen(seen: dict, days: int = 45) -> dict:
    cutoff = (now_irad() - dt.timedelta(days=days)).isoformat()
    return {k: v for k, v in seen.items() if v.get("at", "") >= cutoff}


def skip_reason(item: dict) -> str | None:
    text = f"{item['title']} {item['summary']}".lower()
    for kw in CONFIG["skip_keywords"]:
        if re.search(r"\b" + re.escape(kw.lower()) + r"\b", text):
            return f"skip keyword: {kw}"
    return None


# --------------------------------------------------------------------------
# Claude
# --------------------------------------------------------------------------

TRIAGE_TOOL = {
    "name": "select_stories",
    "description": "Pick the real news items the Herald should adapt for today's edition.",
    "input_schema": {
        "type": "object",
        "properties": {
            "picks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "treatment": {"type": "string", "enum": ["domestic", "regional"]},
                        "section": {"type": "string", "enum": SECTIONS},
                        "angle": {"type": "string", "description": "One sentence: the Herald's angle (records, cost, who is affected)."},
                    },
                    "required": ["id", "treatment", "section", "angle"],
                },
            }
        },
        "required": ["picks"],
    },
}

PEOPLE = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "role": {"type": "string"},
            "canon": {"type": "boolean", "description": "True if this person already exists in the lore."},
        },
        "required": ["name", "role", "canon"],
    },
}

STORY_TOOL = {
    "name": "file_story",
    "description": "File the finished Talas Herald story.",
    "input_schema": {
        "type": "object",
        "properties": {
            "publishable": {"type": "boolean", "description": "False if the item breaks the house rules (tragedy, real people at its core, nothing to adapt)."},
            "reason_if_not": {"type": "string"},
            "headline": {"type": "string", "description": "Sentence case."},
            "dek": {"type": "string"},
            "dateline": {"type": "string", "description": "Place in Irad, e.g. TALAS"},
            "section": {"type": "string", "enum": SECTIONS},
            "image_category": {"type": "string", "enum": sorted(IMAGES)},
            "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
            "paragraphs": {"type": "array", "items": {"type": "string"}, "minItems": 5, "maxItems": 10},
            "pull_quote": {"type": "string", "description": "Optional: one short quote copied exactly from the story, without quotation marks."},
            "pull_quote_by": {"type": "string"},
            "people_quoted": PEOPLE,
        },
        "required": ["publishable", "headline", "dek", "dateline", "section", "image_category",
                     "tags", "paragraphs", "people_quoted"],
    },
}

EDITORIAL_TOOL = {
    "name": "file_editorial",
    "description": "File The Herald's View, the paper's unsigned Saturday editorial.",
    "input_schema": {
        "type": "object",
        "properties": {
            "headline": {"type": "string", "description": "Sentence case, a view, under 80 characters."},
            "dek": {"type": "string"},
            "about": {"type": "array", "items": {"type": "string"}, "description": "Slugs of the week's stories it discusses."},
            "paragraphs": {"type": "array", "items": {"type": "string"}, "minItems": 4, "maxItems": 7},
        },
        "required": ["headline", "dek", "about", "paragraphs"],
    },
}

EDITORIAL_RULES = (
    "Write The Herald's View, the unsigned editorial in Saturday's paper, on one subject from this week's "
    "Herald stories below (pick the one with the most at stake for readers). 380 to 560 words. The Herald's "
    "editorial voice: measured, practical, civic, specific; it argues from the record for transparency, "
    "published tenders and budgets, working courts and councils, fair treatment of the districts and the "
    "highlands, and value for taxpayers. It can criticise ministries, councils, companies and the Mejlis, "
    "politely and with evidence, and it usually ends with a concrete ask (publish the figures, hold the "
    "hearing, set a date). It never criticises the Governor or his family, never speculates about the "
    "Governor's health, and does not take sides between candidates in the 2027 election. No real living "
    "people. Do not invent new facts beyond small, plausible context; the facts come from the stories."
)


class Claude:
    def __init__(self, mock: bool):
        self.mock = mock
        self.system = None
        self.no_forced_tools: set[str] = set()
        if mock:
            return
        import anthropic  # imported here so `build` and `--mock` need no SDK
        self.client = anthropic.Anthropic()
        lore_dir = Path(os.environ.get("LORE_DIR", ROOT / "_lore"))
        lore = []
        for name in CONFIG["lore_files"]:
            path = lore_dir / name
            if not path.exists():
                raise SystemExit(f"Lore file missing: {path} (set LORE_DIR)")
            lore.append(f"<lore file=\"{name}\">\n{path.read_text(encoding='utf-8')}\n</lore>")
        house = (NEWSROOM / "prompts" / "house_style.md").read_text(encoding="utf-8")
        # The rules and lore are identical on every call, so they are cached.
        self.system = [
            {"type": "text", "text": house},
            {"type": "text", "text": "\n\n".join(lore), "cache_control": {"type": "ephemeral"}},
        ]

    def _call(self, model: str, tool: dict, user: str, max_tokens: int) -> dict:
        import anthropic
        kwargs = dict(model=model, max_tokens=max_tokens, system=self.system, tools=[tool])
        if model in self.no_forced_tools:
            resp = self._auto_tool_call(kwargs, tool, user)
        else:
            try:
                resp = self.client.messages.create(
                    **kwargs, tool_choice={"type": "tool", "name": tool["name"]},
                    messages=[{"role": "user", "content": user}])
            except anthropic.BadRequestError as exc:
                if "tool_choice" not in str(exc):
                    raise
                # Some models don't accept a forced tool; ask for it in the message instead.
                self.no_forced_tools.add(model)
                resp = self._auto_tool_call(kwargs, tool, user)
        u = resp.usage
        log(f"    [{model}] in={u.input_tokens} cache_read={getattr(u, 'cache_read_input_tokens', 0)} "
            f"cache_write={getattr(u, 'cache_creation_input_tokens', 0)} out={u.output_tokens}")
        for block in resp.content:
            if block.type == "tool_use" and block.name == tool["name"]:
                return block.input
        raise RuntimeError(f"model returned no {tool['name']} call (stop_reason={resp.stop_reason})")

    def _auto_tool_call(self, kwargs: dict, tool: dict, user: str):
        nudge = (f"\n\nRespond only by calling the {tool['name']} tool, exactly once. "
                 "Do not reply in plain text.")
        return self.client.messages.create(
            **kwargs, tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": user + nudge}])

    def triage(self, candidates: list[dict], recent: list[str], n: int, today: str, overlap_day: bool) -> list[dict]:
        if self.mock:
            return [{"id": c["id"], "treatment": "domestic" if c["country"] == "Kyrgyzstan" else "regional",
                     "section": "News", "angle": "mock angle"} for c in candidates[:n]]
        lines = []
        for c in candidates:
            mark = f" [IradOne also adapted this: \"{c['iradone']['headline']}\"]" if c.get("iradone") else ""
            lines.append(f"[{c['id']}] ({c['source']}, {c['country']}) {c['title']}{mark} :: {c['summary'][:300]}")
        overlap = ("Today, if any candidate is marked as also adapted by IradOne, make one of your picks such "
                   "an item, so readers can compare the two outlets' coverage. "
                   if overlap_day else
                   "Avoid items marked as also adapted by IradOne today unless nothing else fits. ")
        user = (
            f"Today is {today}. Choose up to {n} items below for today's Talas Herald. Prefer items that give "
            "the Herald something to report from the record: council and court business, budgets and tenders, "
            "prices and wages, utilities, water, schools and hospitals, trade and trucking, business, culture, "
            "sport. Vary the sections across the edition. Avoid near-duplicates of each other and of recent "
            "Herald headlines. Do not pick anything centred on deaths, crime, attacks, disasters with "
            f"casualties, or a real person's personal conduct. {overlap}Pick fewer, or none, if nothing fits.\n\n"
            "Recent Herald headlines:\n" + "\n".join(f"- {h}" for h in recent[:20]) +
            "\n\nCandidates:\n" + "\n".join(lines)
        )
        out = self._call(CONFIG["triage_model"], TRIAGE_TOOL, user, 1500)
        return out.get("picks", [])[:n]

    def write(self, item: dict, pick: dict, recent: list[str], today: str, recent_people: list[str]) -> dict:
        if self.mock:
            return mock_story(item, pick)
        iradone = ""
        if item.get("iradone"):
            io = item["iradone"]
            iradone = (
                "\n\nIradOne adapted the same real report. Its story (for contrast; do not copy it, and keep "
                "names, places and figures consistent with it where it set them, unless the record would "
                f"differ):\n<iradone headline=\"{io.get('headline', '')}\" dateline=\"{io.get('dateline', '')}\">"
                f"{io.get('dek', '')}</iradone>\nWrite the Herald's version: the same event from the record, "
                "with the voices and costs IradOne left out. You may note IradOne's coverage once, neutrally.")
        user = (
            f"Today is {today} (Irad Time). Treatment: **{pick['treatment']}**. Section: {pick.get('section', 'News')}. "
            f"Desk note: {pick['angle']}\n\n"
            "Recent Herald headlines (stay consistent, don't repeat):\n"
            + "\n".join(f"- {h}" for h in recent[:20]) +
            "\n\nMinor characters already used recently (do not reuse these names):\n"
            + (", ".join(recent_people) or "none") + iradone +
            "\n\nReal source item (facts only; never reuse its wording):\n"
            f"<source name=\"{item['source']}\" country=\"{item['country']}\" published=\"{item['published']}\">\n"
            f"Title: {item['title']}\nSummary: {item['summary']}\n</source>\n\n"
            "Write the Herald story and file it with the file_story tool."
        )
        return self._call(CONFIG["writer_model"], STORY_TOOL, user, 8000)

    def editorial(self, week: list[dict], recent_editorials: list[str], today: str) -> dict:
        if self.mock:
            return {"headline": "Publish the figures", "dek": "A mock editorial.",
                    "about": [week[0]["slug"]] if week else [],
                    "paragraphs": ["Mock editorial paragraph. " * 20] * 4}
        brief = []
        for a in week:
            brief.append(f"<story slug=\"{a['slug']}\" section=\"{a['section']}\" date=\"{a['published'][:10]}\">\n"
                         f"{a['headline']}\n{a['dek']}\n" + "\n".join(a["paragraphs"]) + "\n</story>")
        user = (f"Today is {today} (Irad Time). {EDITORIAL_RULES}\n\nRecent editorials (pick a different subject):\n"
                + ("\n".join(f"- {h}" for h in recent_editorials[:8]) or "none") +
                "\n\nThis week's stories:\n" + "\n\n".join(brief) +
                "\n\nFile it with the file_editorial tool.")
        return self._call(CONFIG["writer_model"], EDITORIAL_TOOL, user, 4000)


def recent_minor_people(articles: list[dict], n: int = 40) -> list[str]:
    names = []
    for a in articles[:n]:
        for p in a.get("people_quoted", []):
            if not p.get("canon") and p["name"] not in names:
                names.append(p["name"])
    return names


def mock_story(item: dict, pick: dict) -> dict:
    return {
        "publishable": True,
        "headline": f"Council minutes show delay on {item['title'][:40].lower()}",
        "dek": "A mock story generated without the Claude API, for testing the pipeline.",
        "dateline": "TALAS",
        "section": pick.get("section", "News"),
        "image_category": "city",
        "tags": ["test"],
        "paragraphs": ["This is a placeholder paragraph written by the newsroom's mock mode, "
                       "so the build and templates can be tested without an API key. " * 3] * 6,
        "pull_quote": "",
        "people_quoted": [],
    }


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def copied_run(story_text: str, source_text: str, n: int = 9) -> str | None:
    """Return a run of n consecutive words shared with the source, if any."""
    src = words(source_text)
    grams = {" ".join(src[i:i + n]) for i in range(len(src) - n + 1)}
    out = words(story_text)
    for i in range(len(out) - n + 1):
        g = " ".join(out[i:i + n])
        if g in grams:
            return g
    return None


def real_person(text: str) -> str | None:
    for name in CONFIG["real_people_watchlist"]:
        if re.search(r"\b" + re.escape(name) + r"\b", text, re.I):
            return name
    return None


def validate(story: dict, item: dict) -> str | None:
    if not story.get("publishable", False):
        return "writer declined: " + story.get("reason_if_not", "")
    text = " ".join([story["headline"], story["dek"], *story["paragraphs"]])
    name = real_person(text)
    if name:
        return f"names a real person: {name}"
    run = copied_run(text, f"{item['title']} {item['summary']}")
    if run:
        return f"copies source wording: '{run}'"
    n_words = len(words(" ".join(story["paragraphs"])))
    if n_words < 300:
        return f"too short ({n_words} words)"
    if story["section"] not in SECTIONS:
        return f"bad section {story['section']}"
    return None


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:70].rstrip("-")


def load_articles() -> list[dict]:
    arts = [json.loads(p.read_text(encoding="utf-8")) for p in STORIES.glob("*.json")]
    arts.sort(key=lambda a: a["published"], reverse=True)
    return arts


def choose_image(category: str, articles: list[dict]) -> str:
    pool = IMAGES.get(category) or IMAGES["city"]
    recent = [a.get("image") for a in articles[:14]]
    # unused images first, then the one used longest ago
    return min(pool, key=lambda img: (1, -recent.index(img)) if img in recent else (0, 0))


def choose_byline(section: str, slug: str, year: int, taken: set[str]) -> str:
    pool = [s for s in STAFF
            if section in s["beats"] and s["from"] <= year and (s["to"] is None or s["to"] >= year)
            and s["id"] not in ("orozaliev", "hendricks")]
    if not pool:
        return "Herald staff"
    pool.sort(key=lambda s: hashlib.sha1(f"{slug}|{s['id']}".encode()).hexdigest())
    for s in pool:
        if s["name"] not in taken:
            return s["name"]
    return pool[0]["name"]


def write_json(path: Path, obj: dict, dry: bool):
    if dry:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def new_slug(published: dt.datetime, headline: str) -> str:
    base = f"{published:%Y-%m-%d}-{slugify(headline)}"
    slug, n = base, 2
    while (STORIES / f"{slug}.json").exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


def save_story(story: dict, item: dict, pick: dict, articles: list[dict], published: dt.datetime,
               taken: set[str], dry: bool) -> dict:
    slug = new_slug(published, story["headline"])
    art = {
        "slug": slug,
        "kind": "story",
        "headline": story["headline"].strip(),
        "dek": story["dek"].strip(),
        "dateline": story["dateline"].strip().upper(),
        "section": story["section"],
        "byline": choose_byline(story["section"], slug, published.year, taken),
        "tags": story.get("tags", []),
        "paragraphs": [p.strip() for p in story["paragraphs"] if p.strip()],
        "pull_quote": (story.get("pull_quote") or "").strip().strip('"“”'),
        "pull_quote_by": (story.get("pull_quote_by") or "").strip(),
        "people_quoted": story.get("people_quoted", []),
        "image": choose_image(story["image_category"], articles),
        "published": published.isoformat(timespec="minutes"),
        "treatment": pick["treatment"],
        "source": {"name": item["source"], "title": item["title"], "url": item["url"],
                   "published": item["published"]},
        "generated_by": CONFIG["writer_model"],
    }
    if item.get("iradone"):
        art["iradone"] = {"headline": item["iradone"].get("headline"), "slug": item["iradone"].get("slug")}
    write_json(STORIES / f"{slug}.json", art, dry)
    return art


def save_editorial(ed: dict, published: dt.datetime, dry: bool) -> dict:
    slug = new_slug(published, ed["headline"])
    art = {
        "slug": slug,
        "kind": "editorial",
        "headline": ed["headline"].strip(),
        "dek": ed["dek"].strip(),
        "dateline": "",
        "section": "Opinion",
        "byline": "The Herald's View",
        "tags": [],
        "about": ed.get("about", []),
        "paragraphs": [p.strip() for p in ed["paragraphs"] if p.strip()],
        "people_quoted": [],
        "image": None,
        "published": published.isoformat(timespec="minutes"),
        "generated_by": CONFIG["writer_model"],
    }
    write_json(STORIES / f"{slug}.json", art, dry)
    return art


# --------------------------------------------------------------------------
# Edition schedule
# --------------------------------------------------------------------------

def _day_random(day: dt.date, salt: str) -> float:
    h = hashlib.sha256(f"talasherald|{day.isoformat()}|{salt}".encode()).hexdigest()
    return int(h[:12], 16) / 16 ** 12


def is_edition_day(day: dt.date) -> bool:
    return DAY_KEYS[day.weekday()] in EDITION["days"]


def is_editorial_day(day: dt.date) -> bool:
    return DAY_KEYS[day.weekday()] in EDITION["editorial_days"]


def is_overlap_day(day: dt.date) -> bool:
    return _day_random(day, "iradone") < 1 / 3


def on_day(articles: list[dict], day: dt.date, kind: str) -> list[dict]:
    return [a for a in articles if a.get("kind", "story") == kind
            and dt.datetime.fromisoformat(a["published"]).astimezone(IRAD_TZ).date() == day]


def publish_from(day: dt.date) -> dt.datetime:
    h, m = (int(x) for x in EDITION["publish_from"].split(":"))
    return dt.datetime.combine(day, dt.time(h, m), IRAD_TZ)


def is_due(now: dt.datetime, articles: list[dict]) -> tuple[bool, str]:
    today = now.date()
    if not is_edition_day(today):
        return False, "no paper today"
    stories = len(on_day(articles, today, "story"))
    editorial_due = is_editorial_day(today) and not on_day(articles, today, "editorial")
    if stories >= EDITION["stories"] and not editorial_due:
        return False, "today's edition is out"
    if now < publish_from(today):
        return False, f"edition due from {EDITION['publish_from']} IRT"
    if now.hour >= EDITION["give_up_hour"]:
        return False, "today's edition window has closed"
    return True, f"edition due ({stories}/{EDITION['stories']} stories out" + \
        (", editorial to write)" if editorial_due else ")")


def issue_number(day: dt.date) -> tuple[int, int]:
    """Volume and number of the print edition for a day (Monday to Saturday since the first issue)."""
    first = dt.date.fromisoformat(EDITION["first_issue"])
    days = (day - first).days
    weeks, rest = divmod(days, 7)
    number = weeks * 6 + min(rest, 5) + 1
    volume = day.year - first.year + (1 if (day.month, day.day) >= (first.month, first.day) else 0)
    return volume, number


def due_command(args) -> int:
    now = now_irad()
    articles = load_articles()
    due, why = is_due(now, articles)
    print(f"{now:%Y-%m-%d %H:%M} IRT: {'DUE' if due else 'not due'} ({why})")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"due={'true' if due else 'false'}\n")
    for i in range(args.preview):
        day = now.date() + dt.timedelta(days=i)
        if not is_edition_day(day):
            print(f"  {day:%a %d %b}: no paper")
            continue
        vol, no = issue_number(day)
        extras = []
        if is_editorial_day(day):
            extras.append("The Herald's View")
        if is_overlap_day(day):
            extras.append("covers an IradOne story if one is fresh")
        print(f"  {day:%a %d %b}: Vol. {vol}, No. {no:,}, {EDITION['stories']} stories from {EDITION['publish_from']}"
              + (f"; {', '.join(extras)}" if extras else ""))
    return 0


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

def run(args) -> int:
    now = now_irad()
    today = now.date()
    articles = load_articles()
    log(f"Talas Herald newsroom run, {now:%Y-%m-%d %H:%M} IRT")
    if not args.force:
        due, why = is_due(now, articles)
        log(f"Edition: {why}")
        if not due:
            return 0

    target = args.max if args.max is not None else EDITION["stories"]
    need = target if args.force else max(0, target - len(on_day(articles, today, "story")))
    want_editorial = not args.no_editorial and (
        args.editorial or (is_editorial_day(today) and not on_day(articles, today, "editorial")))
    log(f"Plan: {need} stories" + (", plus The Herald's View" if want_editorial else ""))

    today_str = f"{now:%A %d %B %Y}"
    claude = None
    published_new, failures = 0, 0
    seen = prune_seen(load_seen())

    if need > 0:
        log("Fetching feeds")
        fixtures = Path(args.fixtures) if args.fixtures else None
        items = fetch_feeds(fixtures)
        iradone = fetch_iradone(fixtures)
        by_source = {s["source_url"]: s for s in iradone if s.get("source_url")}

        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=CONFIG["max_age_hours"])
        candidates, by_title = [], set()
        for it in items:
            key = url_key(it["url"])
            title_key = re.sub(r"\W+", "", it["title"].lower())[:60]
            if key in seen or title_key in by_title:
                continue
            if it["published"] and dt.datetime.fromisoformat(it["published"]) < cutoff:
                continue
            by_title.add(title_key)
            reason = skip_reason(it)
            if reason:
                seen[key] = {"decision": reason, "title": it["title"], "at": now.isoformat()}
                continue
            if it["url"] in by_source:
                it["iradone"] = by_source[it["url"]]
            candidates.append(it)

        candidates.sort(key=lambda c: c["published"] or "", reverse=True)
        candidates = candidates[: CONFIG["max_candidates_for_triage"]]
        for i, c in enumerate(candidates):
            c["id"] = i
        overlap_day = is_overlap_day(today)
        log(f"{len(candidates)} fresh candidates, {sum(1 for c in candidates if c.get('iradone'))} also on IradOne"
            + (" (IradOne overlap day)" if overlap_day else ""))

        recent = [a["headline"] for a in articles if a.get("kind", "story") == "story"]
        taken = {a["byline"] for a in on_day(articles, today, "story")}
        if candidates:
            claude = Claude(args.mock)
            try:
                picks = claude.triage(candidates, recent, need + 1, today_str, overlap_day)  # one spare
                log(f"Triage picked {len(picks)}: " + json.dumps(picks, ensure_ascii=False))
            except Exception as exc:
                log(f"Triage failed: {exc.__class__.__name__}: {exc}")
                picks, failures = [], failures + 1
            used = set()
            for pick in picks:
                if published_new >= need:
                    break
                try:
                    pid = int(pick.get("id"))
                except (TypeError, ValueError):
                    continue
                if pid not in range(len(candidates)) or pid in used:
                    continue
                used.add(pid)
                pick.setdefault("treatment", "regional")
                pick.setdefault("angle", "")
                item = candidates[pid]
                log(f"Writing: {item['title']}  [{pick['treatment']}, {pick.get('section')}]"
                    + ("  (IradOne overlap)" if item.get("iradone") else ""))
                try:
                    story = claude.write(item, pick, recent, today_str, recent_minor_people(articles))
                except Exception as exc:
                    log(f"  write failed: {exc.__class__.__name__}: {exc}")
                    failures += 1
                    continue
                problem = validate(story, item)
                key = url_key(item["url"])
                if problem:
                    log(f"  rejected: {problem}")
                    seen[key] = {"decision": f"rejected: {problem}", "title": item["title"], "at": now.isoformat()}
                    continue
                stamp = now - dt.timedelta(minutes=9 * published_new)  # keep order stable within a run
                art = save_story(story, item, pick, articles, stamp, taken, args.dry_run)
                taken.add(art["byline"])
                articles.insert(0, art)
                recent.insert(0, art["headline"])
                seen[key] = {"decision": f"published {art['slug']}", "title": item["title"], "at": now.isoformat()}
                published_new += 1
                log(f"  published: {art['headline']}  ({art['byline']})")

    if want_editorial:
        week = [a for a in articles if a.get("kind", "story") == "story"
                and dt.datetime.fromisoformat(a["published"]).astimezone(IRAD_TZ).date() > today - dt.timedelta(days=7)]
        if week:
            claude = claude or Claude(args.mock)
            recent_eds = [a["headline"] for a in articles if a.get("kind") == "editorial"]
            log(f"Writing The Herald's View on {len(week)} stories from this week")
            try:
                ed = claude.editorial(week, recent_eds, today_str)
                text = " ".join([ed["headline"], ed["dek"], *ed["paragraphs"]])
                problem = real_person(text)
                if problem:
                    log(f"  editorial rejected: names a real person: {problem}")
                else:
                    art = save_editorial(ed, now + dt.timedelta(minutes=1), args.dry_run)
                    articles.insert(0, art)
                    published_new += 1
                    log(f"  published: {art['headline']}")
            except Exception as exc:
                log(f"  editorial failed: {exc.__class__.__name__}: {exc}")
                failures += 1
        else:
            log("No stories this week to write an editorial about")

    if args.dry_run:
        log(f"Dry run: {published_new} pieces would be published")
        return 0
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    import build  # local module
    build.build()
    log(f"Done: {published_new} new pieces, {failures} failed calls")
    if failures and not published_new:
        log("Every Claude call failed; marking the run as failed.")
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--max", type=int)
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--fixtures")
    r.add_argument("--mock", action="store_true")
    r.add_argument("--force", action="store_true", help="publish now, whatever the day")
    r.add_argument("--editorial", action="store_true", help="write The Herald's View now")
    r.add_argument("--no-editorial", action="store_true")
    d = sub.add_parser("due", help="say whether the edition is due now")
    d.add_argument("--preview", type=int, default=0, metavar="DAYS", help="also show the next DAYS days")
    sub.add_parser("build")
    args = ap.parse_args()
    sys.path.insert(0, str(NEWSROOM))
    if args.cmd == "build":
        import build
        build.build()
        return 0
    if args.cmd == "due":
        return due_command(args)
    try:
        return run(args)
    except Exception as exc:
        log(f"Run crashed: {exc.__class__.__name__}: {exc}")
        raise
    finally:
        if not args.dry_run:
            save_run_log()


if __name__ == "__main__":
    sys.exit(main())
