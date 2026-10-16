#!/usr/bin/env python3
"""Queue a hand-written batch of FREEFORUM posts (freeirad.org/forum).

The daily job (forum.py) writes the regulars' posts itself; this script sends posts we wrote by hand,
for example about a new piece of lore, through the same signed API. The forum releases each post when
its time comes, exactly like the daily ones.

  python newsroom/forum_batch.py newsroom/forum_batches/irad-times-block.json            # queue it
  python newsroom/forum_batch.py newsroom/forum_batches/irad-times-block.json --dry-run  # check, show the schedule, queue nothing

Pushing a new file in newsroom/forum_batches/ to GitHub queues it automatically (.github/workflows/forum-batch.yml).

Batch file (JSON):
  {
    "batch": "irad-times-block",        unique name, 6-40 letters, digits, - or _ (a name can only be queued once)
    "start": "now",                     or "2026-10-17 09:00" (Irad Time); every "at" below counts from here
    "items": [
      {"kind": "thread", "ref": "times", "board": "Leaks & Rumours", "title": "They blocked the Irad Times",
       "icon": "", "user": "Kobozev_Kid", "at": "+0:15", "body": "..."},
      {"kind": "post", "ref": "times", "user": "statute_nerd", "at": "+1:40", "body": "..."},
      {"kind": "post", "thread": "POST YOUR EMPTY MAP (screenshot thread)", "user": "UncleTemir_Chicago", "at": "+9:05", "body": "..."}
    ]
  }
  - "kind": "thread" starts a new thread (needs "ref", "board", "title"); "post" replies, either to a thread
    started in this batch ("ref"), to an existing thread by exact title ("thread") or by number ("thread_id").
  - "at": "+H:MM" or "+90m" after start, or an absolute Irad Time "YYYY-MM-DD HH:MM".
    The forum accepts times from one hour ago up to four days ahead.
  - "user" must be one of the forum's sample members (freeforum/CHARACTERS.md); bodies use the forum's
    [b]bbcode[/b]; optional "quote_post_id" quotes a post by number.

Environment:
  FORUM_LIVE_KEY  the same as 'live_key' in freeirad-secrets.php (not needed with --state)
  LORE_DIR        lore checkout, used only to list members when checking offline (default: _lore)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forum import FCONFIG, IRT, Checker, Forum  # noqa: E402  (same client and checks as the daily job)

NAME_OK = re.compile(r"^[A-Za-z0-9_-]{6,40}$")


def parse_at(value: str, start: dt.datetime) -> dt.datetime:
    v = str(value).strip()
    m = re.fullmatch(r"\+(\d+):(\d{2})", v)
    if m:
        return start + dt.timedelta(hours=int(m.group(1)), minutes=int(m.group(2)))
    m = re.fullmatch(r"\+(\d+)m", v)
    if m:
        return start + dt.timedelta(minutes=int(m.group(1)))
    try:
        return dt.datetime.strptime(v, "%Y-%m-%d %H:%M").replace(tzinfo=IRT)
    except ValueError:
        raise ValueError(f'"at" must be "+H:MM", "+90m" or "YYYY-MM-DD HH:MM", not {value!r}')


def offline_members() -> set[str]:
    path = Path(os.environ.get("LORE_DIR", "_lore")) / "freeforum" / "CHARACTERS.md"
    if not path.exists():
        return set()
    return {m.group(1) for m in re.finditer(r"^### ([^\s(]+)", path.read_text(encoding="utf-8"), re.M)}


def build(batch: dict, state: dict | None, now: dt.datetime) -> tuple[list[dict], list[str], list[str]]:
    """Turn the batch file into API items. Returns (items, errors, warnings)."""
    errors, warnings, items = [], [], []
    if not NAME_OK.match(str(batch.get("batch", ""))):
        errors.append('"batch" must be 6-40 letters, digits, - or _')
    start_raw = str(batch.get("start", "now"))
    try:
        start = now if start_raw == "now" else dt.datetime.strptime(start_raw, "%Y-%m-%d %H:%M").replace(tzinfo=IRT)
    except ValueError:
        return [], ['"start" must be "now" or "YYYY-MM-DD HH:MM" (Irad Time)'], []
    raw_items = batch.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return [], errors + ['"items" must be a non-empty list'], []
    if len(raw_items) > 80:
        errors.append("at most 80 items per batch")

    if state:
        members = set(state["members"])
        boards = {b["name"]: b for b in state["boards"]}
        titles = {t["title"].casefold(): t for t in state["threads"]}
    else:
        members = offline_members()
        boards, titles = {}, {}
        warnings.append("checked offline: boards and existing thread titles not verified"
                        + ("" if members else "; members not verified (no LORE_DIR)"))
    checker = Checker(members or {i.get("user", "") for i in raw_items})
    refs: dict[str, dt.datetime] = {}
    lo, hi = now - dt.timedelta(minutes=50), now + dt.timedelta(days=4) - dt.timedelta(minutes=10)

    for n, it in enumerate(raw_items, 1):
        where = f"item {n}"
        kind, user, body = it.get("kind"), str(it.get("user", "")), str(it.get("body", "")).strip()
        try:
            at = parse_at(it.get("at", "+0:00"), start)
        except ValueError as e:
            errors.append(f"{where}: {e}"); continue
        if not lo <= at <= hi:
            errors.append(f"{where}: {at.astimezone(IRT):%Y-%m-%d %H:%M} IRT is outside what the forum accepts (now -1h to +4 days)")
        problem = checker.post_problem(user, body, at.astimezone(IRT).date())
        if problem:
            errors.append(f"{where} ({user}): {problem}")
        win = FCONFIG["diaspora_active_irt"].get(user)
        if win and not win[0] <= at.astimezone(IRT).hour < win[1]:
            warnings.append(f"{where}: {user} usually posts {win[0]:02d}:00-{win[1]:02d}:00 IRT (their evening abroad)")
        out = {"kind": kind, "user": user, "body": body, "publish_at": int(at.timestamp())}
        if it.get("quote_post_id"):
            out["quote_post_id"] = int(it["quote_post_id"])

        if kind == "thread":
            ref, board, title = str(it.get("ref", "")), str(it.get("board", "")), str(it.get("title", "")).strip()
            if not ref or ref in refs:
                errors.append(f"{where}: a thread needs a unique \"ref\"")
            refs[ref] = at
            if not 3 <= len(title) <= 150:
                errors.append(f"{where}: title must be 3-150 characters")
            if title.casefold() in titles:
                errors.append(f"{where}: a thread called {title!r} already exists (reply with \"thread\" instead)")
            if boards:
                b = boards.get(board)
                if not b or b["locked"]:
                    errors.append(f"{where}: no open board called {board!r}")
                elif b["staff_only"] and user not in ("FREEIRAD", "Kobozev_Kid"):
                    errors.append(f"{where}: {board} is staff-only (FREEIRAD or Kobozev_Kid)")
            out.update(thread_ref=ref, board=board, title=title, icon=str(it.get("icon", "")))
        elif kind == "post":
            if it.get("ref"):
                ref = str(it["ref"])
                if ref not in refs:
                    errors.append(f"{where}: \"ref\" {ref!r} isn't a thread started earlier in this batch")
                elif at <= refs[ref]:
                    errors.append(f"{where}: reply is timed before its thread starts")
                out["thread_ref"] = ref
            elif it.get("thread_id"):
                out["thread_id"] = int(it["thread_id"])
            elif it.get("thread"):
                t = titles.get(str(it["thread"]).casefold())
                if t:
                    out["thread_id"] = t["id"]
                    if t["locked"]:
                        errors.append(f"{where}: thread {it['thread']!r} is locked")
                elif state:
                    errors.append(f"{where}: no recent thread called {it['thread']!r} (the forum lists its 80 most active; use \"thread_id\")")
                else:
                    out["thread_id"] = 0
            else:
                errors.append(f"{where}: a post needs \"ref\", \"thread\" or \"thread_id\"")
        else:
            errors.append(f"{where}: \"kind\" must be thread or post")
        items.append(out)
    items.sort(key=lambda i: i["publish_at"])
    return items, errors, warnings


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="check and show the schedule, queue nothing")
    ap.add_argument("--state", type=Path, help="read the forum state from a JSON file instead of the forum")
    a = ap.parse_args()

    key = os.environ.get("FORUM_LIVE_KEY", "")
    forum = Forum(FCONFIG["forum_url"], key) if key else None
    if a.state:
        state = json.loads(a.state.read_text(encoding="utf-8"))
    elif forum:
        state = forum.state(int(time.time()) - 86400)
    elif a.dry_run:
        state = None
    else:
        raise SystemExit("Set FORUM_LIVE_KEY (the same as live_key in freeirad-secrets.php), or use --dry-run.")

    failed = False
    for f in a.files:
        batch = json.loads(f.read_text(encoding="utf-8"))
        now = dt.datetime.now(dt.timezone.utc)
        items, errors, warnings = build(batch, state, now)
        print(f"\n== {f.name}: batch {batch.get('batch')!r}, {len(items)} items")
        for w in warnings:
            print(f"::warning::{f.name}: {w}")
        for it in items:
            when = dt.datetime.fromtimestamp(it["publish_at"], IRT).strftime("%a %d %b %H:%M")
            what = f"NEW THREAD [{it['board']}] {it['title']}" if it["kind"] == "thread" else "reply"
            print(f"  {when} IRT  {it['user']:<20} {what}: {re.sub(r'\s+', ' ', it['body'])[:70]}")
        if errors:
            failed = True
            for e in errors:
                print(f"::error::{f.name}: {e}")
            continue
        if a.dry_run or not forum:
            print("  (dry run: nothing queued)")
            continue
        try:
            res = forum.queue(batch["batch"], items)
        except SystemExit as e:
            if "409" in str(e):
                print(f"::notice::{f.name}: batch {batch['batch']!r} was already queued, skipped")
                continue
            raise
        print(f"::notice::{f.name}: queued {res.get('queued')} of {len(items)}")
        for r in res.get("rejected", []):
            failed = True
            print(f"::error::{f.name}: forum rejected {r}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
