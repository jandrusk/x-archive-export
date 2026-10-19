#!/usr/bin/env python3
"""Synthetic-archive checks for x_archive_export.py (stdlib only)."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import x_archive_export as xae


def _write_js(path: Path, assignment: str, posts: list) -> None:
    path.write_text(f"{assignment} {json.dumps(posts)}\n", encoding="utf-8")


class ExportTests(unittest.TestCase):
    def test_dir_zip_and_filters(self) -> None:
        posts = [
            {
                "tweet": {
                    "id_str": "111",
                    "created_at": "Mon Jan 15 10:30:00 +0000 2024",
                    "full_text": "Hello world https://t.co/abc123",
                    "entities": {
                        "urls": [
                            {
                                "url": "https://t.co/abc123",
                                "expanded_url": "https://example.com/article",
                            }
                        ]
                    },
                }
            },
            {
                "tweet": {
                    "id_str": "222",
                    "created_at": "Tue Jan 16 11:00:00 +0000 2024",
                    "full_text": "RT @someone: recycled take",
                    "retweeted": True,
                }
            },
            {
                "tweet": {
                    "id_str": "333",
                    "created_at": "Wed Jan 17 12:00:00 +0000 2024",
                    "full_text": "@bob agreed",
                    "in_reply_to_screen_name": "bob",
                    "in_reply_to_status_id_str": "999",
                }
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "archive" / "data"
            data.mkdir(parents=True)
            _write_js(data / "tweets.js", "window.YTD.tweets.part0 =", posts[:2])
            _write_js(data / "tweets-part1.js", "window.YTD.tweets.part1 =", posts[2:])

            zip_path = root / "archive.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                for p in data.iterdir():
                    zf.write(p, arcname=f"data/{p.name}")

            old = root / "old" / "data"
            old.mkdir(parents=True)
            _write_js(
                old / "tweet.js",
                "window.YTD.tweet.part0 =",
                [
                    {
                        "tweet": {
                            "id_str": "444",
                            "created_at": "Thu Jan 18 09:00:00 +0000 2024",
                            "text": "legacy format",
                        }
                    }
                ],
            )

            from_dir = xae.load_tweets(root / "archive")
            from_zip = xae.load_tweets(zip_path)
            from_old = xae.load_tweets(root / "old")

            self.assertEqual([xae.tweet_id(t) for t in from_dir], ["111", "222", "333"])
            self.assertEqual(
                [xae.tweet_id(t) for t in from_dir],
                [xae.tweet_id(t) for t in from_zip],
            )
            self.assertEqual([xae.tweet_id(t) for t in from_old], ["444"])

            rendered = xae.format_post(from_dir[0])
            self.assertIn("https://example.com/article", rendered)
            self.assertNotIn("https://t.co/abc123", rendered)

            filtered = xae.filter_tweets(
                from_dir, include_replies=False, include_retweets=False
            )
            self.assertEqual([xae.tweet_id(t) for t in filtered], ["111"])

            out = root / "posts.txt"
            self.assertEqual(xae.main([str(root / "archive"), "-o", str(out)]), 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("Hello world", text)
            self.assertIn("recycled take", text)
            self.assertIn("legacy format", xae.format_post(from_old[0]))


if __name__ == "__main__":
    unittest.main()
