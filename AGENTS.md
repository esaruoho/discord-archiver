# AGENTS.md

Machine-readable orientation for AI coding agents (Claude Code, Cursor, etc.) working on this repo. Humans, see [`README.md`](README.md).

## What this repo is

A whitelabel Discord channel archiver. Two independent modes that produce overlapping output:

- `python/archive.py` — discord.py bot, run from the CLI, full API access, can be scheduled
- `browser/scraper.js` — DOM scraper, pasted into a browser DevTools console, no auth setup

Both write JSON + markdown + downloaded attachments. There is no shared library between the two modes — they are deliberately independent so each can be reasoned about, debugged, and shipped on its own.

## File layout

```
.env.example          template for DISCORD_BOT_TOKEN + DISCORD_CHANNEL_ID
.gitignore            keeps .env, exports/, attachments/, *.json, *.md out of git
LICENSE               MIT
README.md             end-user docs (install, run, schema, TODO)
AGENTS.md             this file
python/
  requirements.txt    discord.py, aiohttp, python-dotenv
  archive.py          ~220 lines, single-file, no internal modules
browser/
  README.md           end-user docs for browser mode
  scraper.js          ~360 lines, IIFE, runs in DevTools console
```

## Where to edit what

| Task | File |
|---|---|
| Add a CLI flag to bot mode | `python/archive.py` → `parse_args()` |
| Change bot-mode output format | `python/archive.py` → `_run()` (last 30 lines) |
| Add a new field to bot-mode JSON | `python/archive.py` → the `messages_json.append({...})` block |
| Change bot-mode markdown rendering | `python/archive.py` → `render_embed_md()` and the `block = [...]` assembly |
| Add a new DOM selector for browser mode | `browser/scraper.js` → `extractVisible()` |
| Change browser-mode embed structure | `browser/scraper.js` → `extractEmbeds()` |
| Update the noise filter | both: `SKIP_PATTERNS` at top of each script |

## Conventions

- **No project-name leakage.** This repo is whitelabel. Do not introduce references to specific Discord servers, channels, projects, or downstream tools (e.g. ingest scripts, knowledge graphs). Examples in code and docs use neutral placeholders.
- **Config via env or CLI, never hardcoded.** Tokens, channel IDs, output paths, titles, skip patterns — all configurable. The only thing in `.env.example` should be placeholders.
- **Python is single-file.** Don't split `archive.py` into modules unless the file exceeds ~400 lines or there's a strong reason. The single-file shape is part of the "easy to read in one sitting" promise.
- **Browser script is one IIFE.** Same reason: one paste, one execution. Don't import or split into multiple files.
- **Markdown output mirrors the `### YYYY-MM-DD - author` shape across both modes.** Downstream tools should be able to glob either output.

## Before pushing changes

There is no CI yet. Manual checklist:

1. `python3 -c "import ast; ast.parse(open('python/archive.py').read())"` — syntax check
2. `node -c browser/scraper.js` — if you have Node available; otherwise paste into a browser console and confirm it parses
3. Grep for project-name leakage:
   ```bash
   grep -riE "paketti|renoise|ray.browser|bbs|cloudcity|your-username" . \
     --exclude-dir=venv --exclude-dir=.git
   ```
   Should return zero matches.
4. If you changed `archive.py`, dry-run it against a real channel with `--no-attachments --output-dir /tmp/test-export` and confirm the JSON + markdown look right.
5. If you changed `scraper.js`, open Discord in a browser, paste it into a small channel's DevTools, confirm JSON + markdown download.

## TODO backlog

The README has a [TODO section](README.md#not-yet-supported-todo) listing unimplemented features (threads, forum channels, reactions, replies, resume, rate-limit handling, tests, CI, schema unification). Pick from there if you're looking for something to work on.

## Non-goals

- **Not a Discord client.** This archives, it does not send, edit, or react.
- **Not a real-time bot.** Bot mode is run-once, exit. If you want a long-running bot that ingests as messages arrive, that's a different tool.
- **Not a database.** JSON + markdown on disk are the output. No SQLite, no Postgres, no search index. Downstream tooling can index the JSON if it wants.
- **Not a multi-channel batch tool.** One run = one channel. Wrap with a shell loop if you need many.
