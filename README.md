# x-archive-export

Export posts from an official X (Twitter) data archive to plain text.

The archive is JavaScript, not JSON. Tweet files live under `data/` and start with an assignment such as:

```js
window.YTD.tweets.part0 = [ ... ]
```

This tool accepts an unzipped archive directory, a `.zip`, or a single `tweets.js` / `tweet.js` file. It finds every tweet part file, expands `t.co` URLs, and writes posts oldest-first.

## Usage

Request an archive: X → Settings → Your account → Download an archive of your data.

```bash
python3 x_archive_export.py /path/to/unzipped-archive -o posts.txt
python3 x_archive_export.py twitter-YYYY-MM-DD-hash.zip -o posts.txt
python3 x_archive_export.py archive.zip -o posts.txt --no-replies --no-retweets --jsonl
```

Requires Python 3.9+. Stdlib only.

## Output

```
=== 2024-01-15 10:30:00 UTC | 1234567890123456789 ===
https://x.com/i/status/1234567890123456789
[reply to @user (id)]
post text with expanded URLs
[media] https://...
```

`--jsonl` writes a sidecar with `id`, `created_at`, and `text`.

Do not commit your personal archive or `posts.txt` to this repo.
