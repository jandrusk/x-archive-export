#!/usr/bin/env python3
"""Export posts from an X (Twitter) data archive to plain text.

The official archive is JavaScript, not JSON. Tweet files live under data/
and start with an assignment such as:

    window.YTD.tweets.part0 = [ ... ]

This script accepts an unzipped archive directory or a .zip, finds every
tweet/tweets part file, and writes a chronological plaintext dump.

Usage:
    python3 x_archive_export.py /path/to/archive -o posts.txt
    python3 x_archive_export.py twitter-YYYY-MM-DD-hash.zip
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


TWEET_NAME_RE = re.compile(
    r"^(?:tweets?|tweet)(?:[-_]part\d+)?\.js$",
    re.IGNORECASE,
)
CREATED_AT_FMT = "%a %b %d %H:%M:%S %z %Y"


class ArchiveError(Exception):
    pass


def parse_ytd_js(text: str, source: str) -> list[Any]:
    """Strip the window.YTD.*.partN = prefix and parse the JSON array."""
    raw = text.lstrip("\ufeff").strip()
    if not raw:
        return []
    eq = raw.find("=")
    if eq == -1:
        raise ArchiveError(f"{source}: missing YTD assignment")
    payload = raw[eq + 1 :].strip()
    if payload.endswith(";"):
        payload = payload[:-1].rstrip()
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ArchiveError(f"{source}: invalid JSON after assignment ({exc})") from exc
    if not isinstance(data, list):
        raise ArchiveError(f"{source}: expected a JSON array, got {type(data).__name__}")
    return data


def unwrap_tweet(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    tweet = item.get("tweet", item)
    return tweet if isinstance(tweet, dict) else None


def parse_created_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, CREATED_AT_FMT)
    except ValueError:
        return None


def tweet_id(tweet: dict[str, Any]) -> str:
    return str(tweet.get("id_str") or tweet.get("id") or "")


def tweet_text(tweet: dict[str, Any]) -> str:
    return str(tweet.get("full_text") or tweet.get("text") or "")


def is_retweet(tweet: dict[str, Any], text: str) -> bool:
    if tweet.get("retweeted_status"):
        return True
    return text.startswith("RT @")


def is_reply(tweet: dict[str, Any]) -> bool:
    return bool(
        tweet.get("in_reply_to_status_id_str")
        or tweet.get("in_reply_to_status_id")
        or tweet.get("in_reply_to_screen_name")
    )


def expand_urls(text: str, tweet: dict[str, Any]) -> str:
    """Replace t.co (and other entity) URLs with their expanded form."""
    replacements: list[tuple[str, str]] = []
    entities = tweet.get("entities") or {}
    ext = tweet.get("extended_entities") or {}
    for bucket in (
        entities.get("urls") or [],
        entities.get("media") or [],
        ext.get("media") or [],
    ):
        for ent in bucket:
            if not isinstance(ent, dict):
                continue
            short = ent.get("url")
            long = ent.get("expanded_url") or ent.get("media_url_https") or ent.get("media_url")
            if short and long and short != long:
                replacements.append((str(short), str(long)))
    # Longest first so overlapping prefixes do not collide.
    seen: set[str] = set()
    for short, long in sorted(replacements, key=lambda p: len(p[0]), reverse=True):
        if short in seen:
            continue
        seen.add(short)
        text = text.replace(short, long)
    return text


def media_urls(tweet: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    entities = tweet.get("entities") or {}
    ext = tweet.get("extended_entities") or {}
    for bucket in (entities.get("media") or [], ext.get("media") or []):
        for ent in bucket:
            if not isinstance(ent, dict):
                continue
            url = ent.get("media_url_https") or ent.get("media_url") or ent.get("expanded_url")
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def format_post(tweet: dict[str, Any]) -> str:
    tid = tweet_id(tweet)
    created = parse_created_at(tweet.get("created_at"))
    if created is not None:
        when = created.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    else:
        when = str(tweet.get("created_at") or "unknown-date")

    text = expand_urls(tweet_text(tweet), tweet)
    flags: list[str] = []
    if is_reply(tweet):
        who = tweet.get("in_reply_to_screen_name") or "unknown"
        flags.append(f"reply to @{who}")
        rid = tweet.get("in_reply_to_status_id_str") or tweet.get("in_reply_to_status_id")
        if rid:
            flags[-1] += f" ({rid})"
    if is_retweet(tweet, tweet_text(tweet)):
        flags.append("retweet")

    lines = [f"=== {when} | {tid} ==="]
    if tid:
        lines.append(f"https://x.com/i/status/{tid}")
    if flags:
        lines.append("[" + "; ".join(flags) + "]")
    lines.append(text)
    for url in media_urls(tweet):
        lines.append(f"[media] {url}")
    return "\n".join(lines)


def iter_archive_files(root: Path) -> Iterator[tuple[str, str]]:
    """Yield (name, text) for tweet JS files in an unzipped archive."""
    candidates: list[Path] = []
    for base in (root / "data", root):
        if not base.is_dir():
            continue
        for path in sorted(base.iterdir()):
            if path.is_file() and TWEET_NAME_RE.match(path.name):
                candidates.append(path)
    if not candidates:
        # Nested takeout: archive/data/tweets.js one level down.
        for path in sorted(root.rglob("*.js")):
            if TWEET_NAME_RE.match(path.name) and path.parent.name in {"data", root.name}:
                candidates.append(path)
    if not candidates:
        raise ArchiveError(
            f"No tweet JS files found under {root}. "
            "Expected data/tweets.js, data/tweet.js, or tweets-partN.js."
        )
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        yield path.name, path.read_text(encoding="utf-8")


def iter_zip_files(zip_path: Path) -> Iterator[tuple[str, str]]:
    with zipfile.ZipFile(zip_path) as zf:
        names = [
            name
            for name in zf.namelist()
            if TWEET_NAME_RE.match(Path(name).name)
            and not name.endswith("/")
        ]
        if not names:
            raise ArchiveError(
                f"No tweet JS files in {zip_path}. "
                "Expected data/tweets.js (or tweet.js / part files)."
            )
        for name in sorted(names, key=lambda n: Path(n).name.lower()):
            with zf.open(name) as fh:
                text = io.TextIOWrapper(fh, encoding="utf-8").read()
            yield Path(name).name, text


def load_tweets(source: Path) -> list[dict[str, Any]]:
    if not source.exists():
        raise ArchiveError(f"Path not found: {source}")

    if source.is_file() and source.suffix.lower() == ".zip":
        files = iter_zip_files(source)
    elif source.is_file() and source.suffix.lower() == ".js":
        files = [(source.name, source.read_text(encoding="utf-8"))]
    elif source.is_dir():
        files = iter_archive_files(source)
    else:
        raise ArchiveError(f"Not an archive directory, zip, or .js file: {source}")

    tweets: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for name, text in files:
        for item in parse_ytd_js(text, name):
            tweet = unwrap_tweet(item)
            if tweet is None:
                continue
            tid = tweet_id(tweet)
            if tid and tid in seen_ids:
                continue
            if tid:
                seen_ids.add(tid)
            tweets.append(tweet)
    tweets.sort(
        key=lambda t: (
            parse_created_at(t.get("created_at"))
            or datetime.min.replace(tzinfo=timezone.utc),
            tweet_id(t),
        )
    )
    return tweets


def filter_tweets(
    tweets: Iterable[dict[str, Any]],
    *,
    include_replies: bool,
    include_retweets: bool,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tweet in tweets:
        text = tweet_text(tweet)
        if not include_retweets and is_retweet(tweet, text):
            continue
        if not include_replies and is_reply(tweet):
            continue
        out.append(tweet)
    return out


def write_text(tweets: list[dict[str, Any]], dest: Path) -> None:
    blocks = [format_post(t) for t in tweets]
    body = "\n\n".join(blocks)
    if body:
        body += "\n"
    dest.write_text(body, encoding="utf-8")


def write_jsonl(tweets: list[dict[str, Any]], dest: Path) -> None:
    with dest.open("w", encoding="utf-8") as fh:
        for tweet in tweets:
            record = {
                "id": tweet_id(tweet),
                "created_at": tweet.get("created_at"),
                "text": expand_urls(tweet_text(tweet), tweet),
                "reply": is_reply(tweet),
                "retweet": is_retweet(tweet, tweet_text(tweet)),
                "url": f"https://x.com/i/status/{tweet_id(tweet)}" if tweet_id(tweet) else None,
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Export posts from an X (Twitter) data archive to plain text."
    )
    p.add_argument(
        "archive",
        type=Path,
        help="Unzipped archive directory, .zip, or a tweets.js file",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("x_posts.txt"),
        help="Output path (default: x_posts.txt)",
    )
    p.add_argument(
        "--no-replies",
        action="store_true",
        help="Skip replies",
    )
    p.add_argument(
        "--no-retweets",
        action="store_true",
        help="Skip retweets",
    )
    p.add_argument(
        "--jsonl",
        action="store_true",
        help="Also write <output>.jsonl with id/date/text",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        tweets = load_tweets(args.archive.expanduser())
    except ArchiveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    filtered = filter_tweets(
        tweets,
        include_replies=not args.no_replies,
        include_retweets=not args.no_retweets,
    )
    write_text(filtered, args.output)
    if args.jsonl:
        jsonl_path = args.output.with_suffix(args.output.suffix + ".jsonl")
        if args.output.suffix.lower() == ".txt":
            jsonl_path = args.output.with_suffix(".jsonl")
        write_jsonl(filtered, jsonl_path)
        extra = f", {jsonl_path}"
    else:
        extra = ""

    skipped = len(tweets) - len(filtered)
    print(
        f"Wrote {len(filtered)} post(s) to {args.output}{extra}"
        + (f" ({skipped} skipped by filters)" if skipped else "")
        + f"; {len(tweets)} total in archive."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
