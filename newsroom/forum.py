#!/usr/bin/env python3
"""FREEFORUM live regulars.

Once a day, reads the last day's Talas Herald and IradOne stories, plans a day of activity on the
freeirad.org forum (new threads, replies, welcomes, the odd reply to a real member), writes the posts in
the voices of the forum's sample members, checks them, spreads them across the next 24 hours and queues
them on the forum, which releases each post when its time comes.

  python newsroom/forum.py run               # plan, write and queue the next day
  python newsroom/forum.py preview --days 7  # write a week as a preview file, queue nothing

Options:
  --mock         fake the Claude calls (testing, no API key needed)
  --force        run even if the forum already has a day queued
  --state FILE   read the forum state from a JSON file instead of the forum (testing)
  --dry-run      plan and write but queue nothing (prints the items)

Environment:
  ANTHROPIC_API_KEY  Claude API key (not needed with --mock)
  FORUM_LIVE_KEY     shared key, the same as 'live_key' in freeirad-secrets.php on the forum's server
  LORE_DIR           folder holding quickref.md, canon.md and freeforum/CHARACTERS.md (default: _lore)
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import math
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NEWSROOM = ROOT / "newsroom"
FCONFIG = json.loads((NEWSROOM / "forum_config.json").read_text(encoding="utf-8"))
HCONFIG = json.loads((NEWSROOM / "config.json").read_text(encoding="utf-8"))
STATE_FILE = NEWSROOM / "forum_state.json"
PREVIEW_FILE = ROOT / "_work" / "forum_preview.md"
IRT = dt.timezone(dt.timedelta(hours=6))
LOG: list[str] = []


def log(msg: str) -> None:
    print(msg, flush=True)
    LOG.append(msg)


# ---------------------------------------------------------------- forum API

class Forum:
    def __init__(self, url: str, key: str):
        self.url = url.rstrip("/") + "/live.php"
        self.key = key

    def _request(self, method: str, action: str, body: bytes = b"", query: str = "") -> dict:
        t = str(int(time.time()))
        sig = hmac.new(self.key.encode(), f"{t}\n{method}\n{action}\n".encode() + body, hashlib.sha256).hexdigest()
        req = urllib.request.Request(f"{self.url}?action={action}{query}", data=body if method == "POST" else None, method=method,
                                     headers={"X-Live-Time": t, "X-Live-Signature": sig, "Content-Type": "application/json",
                                              "User-Agent": "TalasHeraldNewsroom/1.0 (forum)"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            try:
                return json.loads(raw)
            except ValueError:
                raise SystemExit(f"The forum didn't answer with data (HTTP 200): {raw[:300].decode('utf-8', 'replace')}")
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "replace")
            hint = {403: "the key doesn't match live_key in freeirad-secrets.php (or the server clock is off)",
                    404: "forum/live.php isn't on the server yet (upload freeirad_live_regulars.zip)",
                    503: "live_key is missing from freeirad-secrets.php"}.get(e.code, "")
            print(f"::error::Forum said {e.code} {hint}: {detail}", flush=True)
            raise SystemExit(f"Forum said {e.code}: {detail}")
        except urllib.error.URLError as e:
            print(f"::error::Couldn't reach the forum at {self.url}: {e.reason}", flush=True)
            raise SystemExit(f"Couldn't reach the forum: {e.reason}")

    def state(self, since: int) -> dict:
        return self._request("GET", "state", query=f"&since={since}")

    def queue(self, batch: str, items: list[dict]) -> dict:
        return self._request("POST", "queue", json.dumps({"batch": batch, "items": items}, ensure_ascii=False).encode())


# ---------------------------------------------------------------- material

def parse_characters(text: str) -> dict[str, dict]:
    chars, tier = {}, ""
    for block in re.split(r"\n(?=## |### )", text):
        if block.startswith("## "):
            tier = block[3:].split("\n", 1)[0].strip()
        elif block.startswith("### "):
            head, _, body = block[4:].partition("\n")
            name = head.split(" (")[0].strip()
            chars[name] = {"tier": tier, "text": "### " + block[4:].strip()}
    return chars


def recent_stories(hours: int, now: dt.datetime, until: dt.datetime | None = None) -> list[dict]:
    until = until or now
    since = until - dt.timedelta(hours=hours)
    out = []
    for f in sorted((ROOT / "data" / "stories").glob("*.json")):
        s = json.loads(f.read_text(encoding="utf-8"))
        try:
            pub = dt.datetime.fromisoformat(s["published"])
        except Exception:
            continue
        if since <= pub <= until:
            out.append({"outlet": "Talas Herald", "headline": s["headline"], "dek": s.get("dek", ""),
                        "section": s.get("section", ""), "published": s["published"],
                        "text": " ".join(s.get("paragraphs", []))[:2500]})
    try:
        with urllib.request.urlopen(urllib.request.Request(HCONFIG["iradone_index"], headers={"User-Agent": "TalasHeraldNewsroom/1.0"}), timeout=20) as r:
            for s in json.loads(r.read()):
                pub = dt.datetime.fromisoformat(s["published"])
                if since <= pub <= until:
                    out.append({"outlet": "IradOne", "headline": s["headline"], "dek": s.get("dek", ""),
                                "section": s.get("category", ""), "published": s["published"], "text": s.get("dek", "")})
    except Exception as exc:
        log(f"  IradOne index unavailable ({exc.__class__.__name__})")
    return out


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"memories": {}, "runs": 0, "last_run": None}


# ---------------------------------------------------------------- Claude

PLAN_TOOL = {
    "name": "plan_forum_day",
    "description": "Plan the next 24 hours of activity on FREEFORUM.",
    "input_schema": {"type": "object", "properties": {"events": {"type": "array", "items": {"type": "object", "properties": {
        "type": {"type": "string", "enum": ["new_thread", "reply", "real_reply", "welcome"],
                 "description": "new_thread: start a thread; reply: bump an existing thread; real_reply: answer a real member's post; welcome: greet a newcomer in their thread or the hello thread"},
        "board": {"type": "string", "description": "exact board name (for new_thread)"},
        "title": {"type": "string", "description": "thread title (for new_thread)"},
        "thread_id": {"type": "integer", "description": "existing thread id (for reply, real_reply, welcome)"},
        "real_post_id": {"type": "integer", "description": "the real member's post being answered (real_reply)"},
        "story": {"type": "string", "description": "headline of the story it reacts to, or empty"},
        "cast": {"type": "array", "items": {"type": "string"}, "description": "members who post, in order of first appearance"},
        "posts": {"type": "integer", "description": "how many posts in this event"},
        "angle": {"type": "string", "description": "one or two sentences: what happens, the jokes, the disagreement"},
    }, "required": ["type", "cast", "posts", "angle"]}}}, "required": ["events"]},
}

WRITE_TOOL = {
    "name": "write_posts",
    "description": "Write the forum posts for one planned event, in order.",
    "input_schema": {"type": "object", "properties": {
        "title": {"type": "string", "description": "thread title (new threads only)"},
        "icon": {"type": "string", "enum": ["", "erased", "map", "leak", "q", "alert", "news", "horse", "music", "film", "rant", "lol", "dpi"]},
        "posts": {"type": "array", "items": {"type": "object", "properties": {
            "user": {"type": "string"}, "body": {"type": "string", "description": "BBCode text"},
            "gap_minutes": {"type": "integer", "description": "minutes after the previous post in this event (first post: 0)"},
        }, "required": ["user", "body"]}},
        "memory": {"type": "array", "description": "durable new facts about members that later posts should remember (a new job, a trip, a promise); usually empty",
                   "items": {"type": "object", "properties": {"user": {"type": "string"}, "note": {"type": "string"}}, "required": ["user", "note"]}},
    }, "required": ["posts"]},
}


class Claude:
    def __init__(self, mock: bool, characters_md: str):
        self.mock = mock
        if mock:
            return
        import anthropic
        self.client = anthropic.Anthropic()
        lore_dir = Path(os.environ.get("LORE_DIR", ROOT / "_lore"))
        lore = "\n\n".join(f'<lore file="{n}">\n{(lore_dir / n).read_text(encoding="utf-8")}\n</lore>' for n in ("quickref.md", "canon.md"))
        rules = (NEWSROOM / "prompts" / "forum_rules.md").read_text(encoding="utf-8")
        self.system = [
            {"type": "text", "text": rules},
            {"type": "text", "text": lore + "\n\n<characters>\n" + characters_md + "\n</characters>",
             "cache_control": {"type": "ephemeral"}},
        ]

    def call(self, model: str, tool: dict, user: str, max_tokens: int) -> dict:
        for attempt in range(3):
            try:
                resp = self.client.messages.create(model=model, max_tokens=max_tokens, system=self.system, tools=[tool],
                                                   tool_choice={"type": "tool", "name": tool["name"]},
                                                   messages=[{"role": "user", "content": user}])
                u = resp.usage
                log(f"    [{model}] in={u.input_tokens} cache_read={getattr(u, 'cache_read_input_tokens', 0)} "
                    f"cache_write={getattr(u, 'cache_creation_input_tokens', 0)} out={u.output_tokens}")
                for b in resp.content:
                    if b.type == "tool_use":
                        return b.input
            except Exception as exc:
                log(f"    call failed ({exc.__class__.__name__}: {str(exc)[:200]}), retrying")
                time.sleep(5 * (attempt + 1))
        raise RuntimeError("Claude call failed three times")


def mock_plan(state: dict, stories: list[dict], n: int, members: list[str]) -> dict:
    rnd = random.Random(len(stories) + n)
    threads = [t for t in state["threads"] if not t["locked"]]
    ev = []
    for i, s in enumerate(stories[:2]):
        ev.append({"type": "new_thread", "board": "Leaks & Rumours" if i == 0 else "Talas, Nurzhan & the Cities",
                   "title": ("[HEARD] " if i == 0 else "") + s["headline"][:90], "story": s["headline"],
                   "cast": rnd.sample(members, 3), "posts": 3, "angle": "mock"})
    if threads:
        ev.append({"type": "reply", "thread_id": threads[0]["id"], "story": "", "cast": rnd.sample(members, 2), "posts": 2, "angle": "mock"})
    for rp in state.get("real_posts", [])[:1]:
        ev.append({"type": "real_reply", "thread_id": rp["thread_id"], "real_post_id": rp["post_id"], "cast": ["Kobozev_Kid"], "posts": 1, "angle": "mock"})
    for nc in state.get("newcomers", [])[:1]:
        hello = next((t for t in threads if t["title"].startswith("Hello from the first member")), threads[0] if threads else None)
        if hello:
            ev.append({"type": "welcome", "thread_id": hello["id"], "cast": ["Erbol8645"], "posts": 1, "angle": f"welcome {nc}"})
    return {"events": ev}


def mock_write(event: dict) -> dict:
    posts = []
    for i in range(event.get("posts", 1)):
        u = event["cast"][i % len(event["cast"])]
        posts.append({"user": u, "body": f"(mock post {i + 1} by {u} about: {event.get('story') or event.get('angle')})", "gap_minutes": 0 if i == 0 else 30})
    return {"title": event.get("title", ""), "icon": "", "posts": posts, "memory": []}


# ---------------------------------------------------------------- checking

URL_OK = re.compile(r"https?://(freeirad\.org|attelkom\.net/users/|iradone\.com)", re.I)


class Checker:
    def __init__(self, members: set[str]):
        self.members = members
        names = [n for n in HCONFIG.get("real_people_watchlist", []) if n not in FCONFIG["allowed_real_names"]]
        self.real = re.compile(r"\b(" + "|".join(map(re.escape, names)) + r")\b", re.I) if names else None
        self.secret = [re.compile(p, re.I) for p in FCONFIG["lore_secret_patterns"]]

    def post_problem(self, user: str, body: str, today: dt.date) -> str | None:
        if user not in self.members or user in FCONFIG["excluded_members"]:
            return f"{user} can't post"
        if not 2 <= len(body) <= 5000:
            return "length"
        for m in re.finditer(r"https?://\S+", body):
            if not URL_OK.match(m.group(0)):
                return "link"
        if re.search(r"\+?\d[\d\s().-]{8,}\d", body):
            return "phone-like number"
        if self.real and self.real.search(body):
            return "real person's name"
        for p in self.secret:
            if p.search(body):
                return f"secret lore ({p.pattern[:30]})"
        if today < dt.date(2026, 11, 16) and re.search(r"tulpar", body, re.I) and re.search(
                r"\b(judge|court) (ruled|decided|found)\b|\bverdict\b|\binjunction (was )?granted\b", body, re.I):
            return "decides the Tulparcoin case"
        return None


# ---------------------------------------------------------------- scheduling

def pick_time(rnd: random.Random, start: dt.datetime, end: dt.datetime) -> dt.datetime:
    weights = FCONFIG["hour_weights_irt"]
    for _ in range(200):
        t = start + dt.timedelta(seconds=rnd.uniform(0, (end - start).total_seconds()))
        if rnd.random() < weights[t.astimezone(IRT).hour] / max(weights):
            return t
    return start + (end - start) / 2


def nudge_for_member(user: str, t: dt.datetime) -> dt.datetime:
    win = FCONFIG["diaspora_active_irt"].get(user)
    if not win:
        return t
    h = t.astimezone(IRT).hour
    if win[0] <= h < win[1]:
        return t
    # wait until their active window opens
    local = t.astimezone(IRT)
    target = local.replace(hour=win[0], minute=random.randint(0, 59))
    if target <= local:
        target += dt.timedelta(days=1)
    return target


def schedule(rnd: random.Random, posts: list[dict], start: dt.datetime, end: dt.datetime) -> list[dt.datetime]:
    t = pick_time(rnd, start, end - dt.timedelta(hours=2))
    times = []
    for i, p in enumerate(posts):
        if i:
            gap = p.get("gap_minutes") or math.exp(rnd.gauss(math.log(45), 0.9))
            t = t + dt.timedelta(minutes=max(3, min(360, gap)))
        t = nudge_for_member(p["user"], t)
        times.append(t)
    return times


# ---------------------------------------------------------------- one day

def thread_digest(t: dict) -> str:
    lines = [f"Thread #{t['id']} [{t['board']}] \"{t['title']}\" ({t['replies']} replies)"]
    for p in t["recent"][-6:]:
        who = p["user"] + (" (REAL MEMBER)" if p.get("real_member") else "")
        when = dt.datetime.fromtimestamp(p["at"], IRT).strftime("%Y-%m-%d %H:%M")
        lines.append(f"  - {who}, {when}: {p['text'][:500]}")
    return "\n".join(lines)


def plan_and_write(claude: Claude, state: dict, stories: list[dict], memory: dict, chars: dict,
                   day_start: dt.datetime, mock: bool) -> tuple[list[dict], dict]:
    settings = state["settings"]
    per_day, real_cap = settings["per_day"], settings["real_replies"]
    members = [m for m in state["members"] if m in chars and m not in FCONFIG["excluded_members"]
               and not chars[m]["tier"].startswith("Legends")]
    today = day_start.astimezone(IRT).date()
    threads = [t for t in state["threads"] if not t["locked"]]
    if per_day <= 0:
        return [], {}

    roster = "\n".join(f"- {m} ({chars[m]['tier']}): " + chars[m]["text"].split("- voice: ", 1)[-1].split("\n")[0][:160]
                       + (" | remembered: " + "; ".join(x["note"] for x in memory.get(m, [])[-3:]) if memory.get(m) else "")
                       for m in members)
    story_txt = "\n".join(f"* [{s['outlet']}, {s['section']}, {s['published']}] {s['headline']}: {s['dek']}" for s in stories) or "(no new stories today)"
    thread_txt = "\n\n".join(thread_digest(t) for t in threads[:40])
    real_txt = "\n".join(f"* post {p['post_id']} by {p['user']} in thread #{p['thread_id']} \"{p['thread']}\": {p['text'][:400]}"
                         for p in state.get("real_posts", [])) or "(none)"
    newcomers = ", ".join(state.get("newcomers", [])) or "(none)"
    boards = ", ".join(b["name"] for b in state["boards"] if not b["locked"])

    user = (f"Today is {today:%A %d %B %Y} (Irad Time). Plan the next 24 hours on FREEFORUM: about {per_day} posts in total, "
            f"spread over 3 to {max(3, per_day // 2)} events. React to today's news where a regular would care (a court story in Leaks & Rumours, "
            "prices and football in the cities board, valley news in The Valleys, culture in Mor-Wave, Music & Films), bump one or two "
            "existing threads where it fits, and include at least one small off-topic or silly event. Not every story deserves a thread. "
            f"Use at most {real_cap} real_reply events (only for real members' posts listed below that invite an answer; skip anything "
            "personal, upsetting or off-world) and one welcome per newcomer. Choose casts whose voices fit; most posts come from core "
            "regulars, sometimes an occasional poster. Never the same member twice in a row in one event unless it's a joke.\n\n"
            f"Boards: {boards}\n\nToday's stories:\n{story_txt}\n\nActive threads (recent posts):\n{thread_txt}\n\n"
            f"Recent posts by real members:\n{real_txt}\n\nNew real members to welcome: {newcomers}\n\n"
            f"Members who can post:\n{roster}")
    plan = mock_plan(state, stories, per_day, members) if mock else claude.call(FCONFIG["planner_model"], PLAN_TOOL, user, 4000)

    checker = Checker(set(members))
    rnd = random.Random(int(day_start.timestamp()))
    items, new_mem = [], {}
    real_used = 0
    total = 0
    by_id = {t["id"]: t for t in threads}
    for n, ev in enumerate(plan.get("events", [])):
        if total >= per_day + 3:
            break
        kind = ev.get("type")
        if kind == "real_reply":
            if real_used >= real_cap:
                continue
            real_used += 1
        if kind != "new_thread" and ev.get("thread_id") not in by_id:
            log(f"  skip event {n}: unknown thread {ev.get('thread_id')}")
            continue
        if kind == "new_thread" and ev.get("board") not in {b["name"] for b in state["boards"] if not b["locked"] and not b["staff_only"]}:
            log(f"  skip event {n}: bad board {ev.get('board')}")
            continue
        story = next((s for s in stories if s["headline"] == ev.get("story")), None)
        ctx = []
        if story:
            ctx.append(f"The story they react to ({story['outlet']}): {story['headline']}\n{story['dek']}\n{story['text']}")
        if kind != "new_thread":
            ctx.append("The thread so far:\n" + thread_digest(by_id[ev["thread_id"]]))
        if kind == "real_reply":
            rp = next((p for p in state.get("real_posts", []) if p["post_id"] == ev.get("real_post_id")), None)
            if not rp:
                continue
            ctx.append(f"Reply to this post by real member {rp['user']} (quote a short line of it with [quote={rp['user']}]...[/quote]):\n{rp['text']}")
        mems = "\n".join(f"- {m}: " + "; ".join(x["note"] for x in memory.get(m, [])[-6:]) for m in ev["cast"] if memory.get(m))
        voice = "\n\n".join(chars[m]["text"] for m in ev["cast"] if m in chars)
        wuser = (f"Today is {today:%A %d %B %Y}. Write {ev.get('posts', 2)} posts for this event on FREEFORUM.\n"
                 f"Event: {kind}" + (f" in board \"{ev.get('board')}\", working title \"{ev.get('title')}\"" if kind == "new_thread" else "")
                 + f"\nWhat happens: {ev.get('angle')}\nCast, in order of first appearance: {', '.join(ev['cast'])}\n\n"
                 + "\n\n".join(ctx) + f"\n\nThe cast's voices:\n{voice}\n\n"
                 + (f"What we remember about them:\n{mems}\n\n" if mems else "")
                 + "Write like the real forum: mostly short posts, natural quoting of each other, typos where the voice has them. "
                   "Give gap_minutes between posts (people answer within minutes to hours). Add memory notes only for genuinely new durable facts.")
        out = mock_write(ev) if mock else claude.call(FCONFIG["writer_model"], WRITE_TOOL, wuser, 6000)
        posts = [p for p in out.get("posts", []) if p.get("user") and p.get("body")]
        good = []
        for i, p in enumerate(posts):
            prob = checker.post_problem(p["user"], p["body"], today)
            if prob:
                log(f"  dropped post by {p['user']} ({prob})")
                if i == 0 and kind == "new_thread":
                    good = []
                    break
                continue
            good.append(p)
        if not good:
            continue
        times = schedule(rnd, good, day_start, day_start + dt.timedelta(hours=24))
        ref = f"t{n}"
        title = (out.get("title") or ev.get("title") or "").strip()
        if kind == "new_thread" and ev["board"] == "Leaks & Rumours" and not re.match(r"\[(RUMOUR|HEARD|SEEN)\]", title):
            title = "[HEARD] " + title
        for i, (p, t) in enumerate(zip(good, times)):
            item = {"publish_at": int(t.timestamp()), "user": p["user"], "body": p["body"].strip()}
            if kind == "new_thread" and i == 0:
                item.update(kind="thread", thread_ref=ref, board=ev["board"], title=title[:150], icon=out.get("icon", ""))
            elif kind == "new_thread":
                item.update(kind="post", thread_ref=ref)
            else:
                item.update(kind="post", thread_id=ev["thread_id"])
                if kind == "real_reply" and i == 0:
                    item["quote_post_id"] = ev.get("real_post_id", 0)
            items.append(item)
        total += len(good)
        for m in out.get("memory", []) or []:
            if m.get("user") in members and m.get("note"):
                new_mem.setdefault(m["user"], []).append({"date": today.isoformat(), "note": m["note"][:200]})
        log(f"  event {n} ({kind}): {len(good)} posts" + (f", \"{title}\"" if kind == "new_thread" else f", thread #{ev.get('thread_id')}"))
    return items, new_mem


def merge_memory(memory: dict, new: dict) -> None:
    for u, notes in new.items():
        memory[u] = (memory.get(u, []) + notes)[-10:]


# ---------------------------------------------------------------- commands

def characters_text() -> str:
    lore_dir = Path(os.environ.get("LORE_DIR", ROOT / "_lore"))
    path = lore_dir / "freeforum" / "CHARACTERS.md"
    if not path.exists():
        raise SystemExit(f"Character record missing: {path} (set LORE_DIR)")
    return path.read_text(encoding="utf-8")


def get_state(args, since: int) -> dict:
    if args.state:
        return json.loads(Path(args.state).read_text(encoding="utf-8"))
    key = os.environ.get("FORUM_LIVE_KEY", "")
    if len(key) < 32:
        raise SystemExit("Set FORUM_LIVE_KEY (the same as live_key in freeirad-secrets.php).")
    return Forum(os.environ.get("FORUM_URL", FCONFIG["forum_url"]), key).state(since)


def cmd_run(args) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    mem_state = load_state()
    since = int((now - dt.timedelta(hours=30)).timestamp())
    st = get_state(args, since)
    if not st["settings"]["enabled"] and not args.force:
        log("Live regulars are paused on the forum's Admin page. Nothing to do.")
        return
    if st.get("queued_until") and st["queued_until"] > now.timestamp() + FCONFIG["skip_if_queued_hours"] * 3600 and not args.force:
        log("The forum already has the next day queued. Nothing to do.")
        return
    start = max(now, dt.datetime.fromtimestamp(st.get("queued_until") or 0, dt.timezone.utc)) + dt.timedelta(minutes=5)
    chars_md = characters_text()
    chars = parse_characters(chars_md)
    stories = recent_stories(FCONFIG["story_window_hours"], now)
    log(f"{len(stories)} stories, {len(st['threads'])} threads, {len(st.get('real_posts', []))} real posts, "
        f"{len(st.get('newcomers', []))} newcomers, target {st['settings']['per_day']} posts")
    claude = Claude(args.mock, chars_md)
    items, new_mem = plan_and_write(claude, st, stories, mem_state["memories"], chars, start, args.mock)
    if not items:
        log("Nothing written.")
        return
    if args.dry_run:
        for it in sorted(items, key=lambda x: x["publish_at"]):
            log(f"  {dt.datetime.fromtimestamp(it['publish_at'], IRT):%a %H:%M} {it['user']}: {it.get('title', '')} {it['body'][:120]!r}")
        return
    batch = f"day{now:%Y%m%d%H%M}"
    res = Forum(os.environ.get("FORUM_URL", FCONFIG["forum_url"]), os.environ["FORUM_LIVE_KEY"]).queue(batch, items)
    log(f"Queued {res.get('queued')} posts; rejected: {res.get('rejected')}")
    merge_memory(mem_state["memories"], new_mem)
    mem_state["runs"] = mem_state.get("runs", 0) + 1
    mem_state["last_run"] = now.isoformat(timespec="seconds")
    STATE_FILE.write_text(json.dumps(mem_state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def cmd_preview(args) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    st = get_state(args, int((now - dt.timedelta(hours=30)).timestamp()))
    st["settings"]["per_day"] = st["settings"]["per_day"] or 12
    chars_md = characters_text()
    chars = parse_characters(chars_md)
    claude = Claude(args.mock, chars_md)
    memory = json.loads(json.dumps(load_state()["memories"]))
    out = ["# FREEFORUM live regulars: preview", "",
           f"Written {now:%Y-%m-%d %H:%M} UTC. A simulation of {args.days} days using the stories of the last {args.days} days, "
           f"{st['settings']['per_day']} posts a day. Nothing here was posted to the forum.", ""]
    for d in range(args.days, 0, -1):
        day_end = now - dt.timedelta(days=d - 1)
        stories = recent_stories(24, day_end, until=day_end)
        items, new_mem = plan_and_write(claude, st, stories, memory, chars, day_end, args.mock)
        merge_memory(memory, new_mem)
        out.append(f"## Day ending {day_end.astimezone(IRT):%a %d %b %Y} ({len(stories)} stories, {len(items)} posts)\n")
        titles = {}
        for it in sorted(items, key=lambda x: x["publish_at"]):
            where = (f"NEW THREAD in {it['board']}: **{it['title']}**" if it["kind"] == "thread"
                     else f"in {titles.get(it.get('thread_ref'), '')}" if it.get("thread_ref")
                     else f"in thread #{it['thread_id']} " + next((f"\"{t['title']}\"" for t in st["threads"] if t["id"] == it["thread_id"]), ""))
            if it["kind"] == "thread":
                titles[it["thread_ref"]] = f"\"{it['title']}\""
            out.append(f"**{dt.datetime.fromtimestamp(it['publish_at'], IRT):%H:%M} {it['user']}** {where}\n\n" +
                       "\n".join("> " + line for line in it["body"].splitlines()) + "\n")
        if new_mem:
            out.append("_Remembered: " + "; ".join(f"{u}: {n['note']}" for u, ns in new_mem.items() for n in ns) + "_\n")
    PREVIEW_FILE.parent.mkdir(exist_ok=True)
    PREVIEW_FILE.write_text("\n".join(out) + "\n\n## Log\n\n```\n" + "\n".join(LOG) + "\n```\n", encoding="utf-8")
    log(f"Wrote {PREVIEW_FILE}")


def main() -> None:
    try:
        _main()
    except SystemExit as e:
        if e.code not in (0, None) and isinstance(e.code, str):
            print(f"::error::{e.code}", flush=True)
        raise
    except Exception as e:
        import traceback
        tb = traceback.extract_tb(e.__traceback__)[-1]
        print(f"::error::{e.__class__.__name__}: {str(e)[:300]} (forum.py line {tb.lineno})", flush=True)
        raise


def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "preview"):
        p = sub.add_parser(name)
        p.add_argument("--mock", action="store_true")
        p.add_argument("--state")
        if name == "run":
            p.add_argument("--force", action="store_true")
            p.add_argument("--dry-run", action="store_true")
        else:
            p.add_argument("--days", type=int, default=7)
    args = ap.parse_args()
    {"run": cmd_run, "preview": cmd_preview}[args.cmd](args)


if __name__ == "__main__":
    main()
