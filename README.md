# discord-archiver

Archive a Discord channel's full message history — text, attachments, embeds, links — to local files. Two modes:

- **Bot mode** (Python, `python/archive.py`) — uses a Discord bot token. Best for channels you own or admin; runs unattended, can be scheduled.
- **Browser mode** (JavaScript, `browser/scraper.js`) — pastes into your browser's DevTools console. No bot, no token, no setup. Works on DMs and any channel you can read.

Both modes produce the same kind of output: JSON for downstream tooling, markdown for reading, and a folder of downloaded attachments.

## Output

```
exports/
├── messages.json     # every message with timestamps, authors, embeds, attachments
├── changelog.md      # human-readable markdown: ### YYYY-MM-DD - author
└── attachments/      # downloaded images and files, date-prefixed
    ├── 2026-01-12_screenshot.png
    └── 2026-01-13_demo.mp4
```

## Bot mode (Python)

### 1. Create a Discord bot

1. Go to <https://discord.com/developers/applications> and click **New Application**.
2. Open the new app, go to **Bot** in the sidebar.
3. Click **Reset Token** and copy the token. You only see it once — store it immediately.
4. Under **Privileged Gateway Intents**, enable **Message Content Intent**.
5. Go to **OAuth2 → URL Generator**, tick `bot`, then under permissions tick **Read Messages/View Channels** and **Read Message History**. Copy the generated URL and open it to invite the bot to your server.

### 2. Get the channel ID

1. In Discord, open **Settings → Advanced** and enable **Developer Mode**.
2. Right-click the channel you want to archive → **Copy Channel ID**.

### 3. Install and configure

```bash
git clone https://github.com/esaruoho/discord-archiver.git
cd discord-archiver

python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r python/requirements.txt

cp .env.example .env
$EDITOR .env                      # paste your bot token + channel ID
```

`.env` is git-ignored — your token never leaves your machine.

### 4. Run

```bash
python python/archive.py
```

Or override config inline:

```bash
python python/archive.py \
  --channel 123456789012345678 \
  --output-dir ./exports/my-channel \
  --title "My Channel Archive" \
  --skip-pattern "^Posted to:" \
  --skip-pattern "^Failed:"
```

All CLI flags:

| Flag | Default | Description |
|---|---|---|
| `--token` | `$DISCORD_BOT_TOKEN` | Bot token |
| `--channel` | `$DISCORD_CHANNEL_ID` | Channel ID to archive |
| `--output-dir` | `./exports` | Where to write the export |
| `--title` | `Channel Archive` | Header at the top of `changelog.md` |
| `--skip-pattern REGEX` | _(none)_ | Drop messages matching this regex. Repeatable. |
| `--no-attachments` | off | Skip downloading attachments (URLs still recorded) |
| `--resume` | off | Only fetch messages newer than the last archived one. Reads existing `messages.json`, finds the highest message ID, asks Discord for everything after it. |
| `--max-retries` | `5` | Attachment download retries on HTTP 429 (rate-limited), 5xx, or network errors. 429s honor the `Retry-After` header; 5xx/network use exponential backoff (1s, 2s, 4s, 8s, 16s). |

### Recurring / incremental archive

First run does the full history. Every subsequent run with `--resume` only fetches messages newer than the last archived one and appends them to the existing JSON + markdown:

```bash
# First run — full archive
python python/archive.py --output-dir ./exports/my-channel

# Hours / days / weeks later — incremental
python python/archive.py --output-dir ./exports/my-channel --resume
```

Cron example (daily at 03:00):

```cron
0 3 * * *  cd ~/discord-archiver && venv/bin/python python/archive.py --output-dir ./exports/my-channel --resume >> ~/archive.log 2>&1
```

Notes:
- `messages.json` is the source of truth. `changelog.md` is regenerated from it on every run; any hand-edits will be overwritten.
- `--resume` with no existing `messages.json` falls back to a full archive (with a warning).
- Skipped messages (via `--skip-pattern`) aren't stored, so they won't be re-fetched on resume either — but their IDs are still seen by Discord, so the snowflake-based cursor advances past them correctly.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Privileged intent provided is not enabled` on startup | Message Content Intent not enabled in the developer portal | Application → Bot → Privileged Gateway Intents → toggle on |
| `Channel <id> not found.` | Bot isn't in the server, or doesn't have View Channel on this channel | Re-invite with the OAuth2 URL, or grant `View Channel` + `Read Message History` in channel settings |
| `Missing bot token` / `Missing channel ID` | `.env` not loaded or not in the working directory | Run from the repo root, or pass `--token` / `--channel` explicitly |
| Many `download_failed` entries with HTTP 403 | Discord CDN attachment URLs expired (they contain time-limited auth tokens) | Re-run from scratch — the bot fetches fresh URLs from the API on each run |
| Hangs or stalls mid-archive on a huge channel | Hitting Discord API rate limits — discord.py is sleeping until the bucket refills | Wait it out; the library handles 429s transparently. CDN downloads have their own retry via `--max-retries`. |
| `Resume requested but no existing messages.json found` | `--output-dir` doesn't contain a previous export | Run without `--resume` first, or point `--output-dir` at the existing export folder |

## Browser mode (JavaScript)

See [`browser/README.md`](browser/README.md). One-paragraph version: open Discord in your browser, navigate to the channel, F12, paste `browser/scraper.js`, wait. JSON + markdown auto-download.

## When to use which mode

| | Bot mode | Browser mode |
|---|---|---|
| Setup | Discord bot + token | None |
| Works on DMs | No | Yes |
| Works on channels without bot access | No | Yes |
| Unattended / scheduled | Yes | No |
| Original embed structure | Full | Best-effort from DOM |
| Resilient to Discord UI changes | Yes | No (depends on class names) |

If you control the server and want recurring archives → bot mode. Otherwise → browser mode.

## Schema

`messages.json`:

```json
{
  "exported_at": "2026-05-22T...",
  "channel_id": "123...",
  "total_messages": 981,
  "messages": [
    {
      "id": "...",
      "timestamp": "2026-01-12T14:23:01+00:00",
      "author": "alice",
      "author_id": "...",
      "content": "shipped v0.3",
      "attachments": [
        { "filename": "screenshot.png", "url": "https://cdn...",
          "local_path": "attachments/2026-01-12_screenshot.png",
          "content_type": "image/png", "size": 41234 }
      ],
      "embeds": [
        { "author": "", "title": "Release notes", "url": "https://...",
          "description": "...", "fields": [], "footer": "" }
      ]
    }
  ]
}
```

The browser mode produces a slightly different shape (no `attachments[]` records — uses `images`/`videos`/`files` arrays of URLs instead), since it can't introspect Discord's full message object.

## Not yet supported (TODO)

Things the current archiver silently ignores or doesn't handle. None of these are blockers for the common "archive a text channel" case — they're listed here so future work has a clear backlog and so users know what they're missing before they hit it.

- [ ] **Threads** — messages inside threads aren't followed. Each thread would need to be opened and archived separately. Affects both modes.
- [ ] **Forum channels** — same as threads: each post is its own thread, none are walked.
- [ ] **Voice channel text chats** — bot mode could read these with the right permissions, currently untested.
- [ ] **Reactions** — emoji reactions per message aren't recorded. discord.py exposes `message.reactions`; just needs to be added to the JSON shape.
- [ ] **Stickers** — `message.stickers` is ignored. Bot mode could pull URLs + sticker names.
- [ ] **Replies / message references** — `message.reference` (which message this is a reply to) is dropped. Useful for reconstructing conversation threads.
- [ ] **Pinned messages flag** — `message.pinned` boolean isn't recorded.
- [ ] **Edit history** — only the current version of a message is captured. Discord doesn't expose prior versions via API anyway, but `message.edited_at` could be recorded.
- [ ] **Tests** — no test suite. `sanitize_filename` and `rebuild_markdown_block` are pure functions that would be trivial to cover.
- [ ] **CI** — no GitHub Actions for lint / typecheck / smoke run.
- [ ] **Browser mode: thread + forum support** — currently scrapes only the main channel scroll.
- [ ] **Schema unification between modes** — bot mode produces `attachments[]` with local paths and metadata; browser mode produces separate `images[] / videos[] / files[]` URL arrays. Downstream consumers have to branch on mode.

## Privacy notes

- The bot token in `.env` grants read access to every channel the bot is invited to. Treat it like a password.
- Exported messages contain everything the bot or your session can see, including IDs and timestamps. Don't commit `exports/` to a public repo unless you've reviewed what's in it.
- Discord CDN attachment URLs contain auth tokens that expire after a few days. The archive downloads files locally so you don't lose them when the URLs go stale.

## License

MIT — see [`LICENSE`](LICENSE).
