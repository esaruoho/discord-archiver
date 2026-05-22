# Browser-console scraper

Zero-install Discord channel archiver. Runs in your browser's DevTools console using your existing Discord session — no bot, no token, no permissions to set up.

## When to use this mode vs. the Python bot mode

| | Browser mode | Bot mode (`python/archive.py`) |
|---|---|---|
| Setup | None | Create a bot app, invite to server, copy token |
| Auth | Your own session cookie | Bot token in `.env` |
| Works on DMs | Yes | No (bots can't access DMs) |
| Works on channels without bot access | Yes (anything you can read) | No |
| Original embed structure | Best-effort from DOM | Full Discord embed object |
| Unattended / scheduled | No (browser tab must stay open) | Yes |
| Resilience to Discord UI changes | DOM-fragile | API-stable |

Use the browser mode for one-off DMs, channels where you can't add a bot, or quick archival. Use the bot mode when you control the server and want clean, scheduled exports.

## Usage

1. Open Discord **in your browser** (not the desktop app) and navigate to the channel or DM you want to archive.
2. Open DevTools: **Cmd+Option+J** on macOS, **F12** on Windows/Linux.
3. Open `scraper.js` in this folder, copy the entire contents, paste into the Console tab, press Enter.
4. Watch it scroll to the top of the channel (this can take a while for long histories — leave the tab alone).
5. When done, your browser auto-downloads:
   - `discord-export-<date>.json` — every message with timestamps, authors, embeds, links
   - `discord-export-<date>.md` — human-readable markdown changelog
   - A prompt asking whether to also download every image / video / file attachment

## Output schema

```json
{
  "stats": {
    "totalMessages": 981,
    "uniqueAuthors": ["alice", "bob"],
    "totalImages": 42,
    "totalVideos": 3,
    "totalFiles": 7,
    "dateRange": "2026-01-12T... → 2026-05-22T..."
  },
  "messages": [
    {
      "timestamp": "2026-01-12T14:23:01.000Z",
      "author": "alice",
      "content": "shipped v0.3",
      "links": ["https://example.com"],
      "embedLinks": [],
      "embedText": ["Release notes", "..."],
      "embeds": [
        { "author": "", "title": "Release notes", "url": "https://...",
          "description": "...", "fields": [], "footer": "" }
      ],
      "images": ["https://cdn.discordapp.com/..."],
      "videos": [],
      "files": []
    }
  ]
}
```

## Configuration

Open `scraper.js` and edit the `SKIP_PATTERNS` array near the top to drop noise rows (cross-posting bot announcements, webhook status pings, etc.):

```js
const SKIP_PATTERNS = [
  /^Posted to:/i,
  /^Failed:/i,
  // Add your own
];
```

## Troubleshooting

- **Doesn't find the scroller**: Discord changed their class names. Run this in the console to find the right element:
  ```js
  document.querySelectorAll('*').forEach(el => {
    if (el.scrollHeight > el.clientHeight + 100 && el.clientHeight > 200) {
      console.log(el.scrollHeight, el.tagName, el.className.slice(0, 80), el);
    }
  });
  ```
  Update the selector chain at the top of `scraper.js` with the new class name.
- **Long channel, tab gets heavy**: Increase the `2500` (ms) delay in the scroll loop to `3500` or `4000`.
- **Missing messages**: Threads aren't part of the main channel scroll — open each thread separately and re-run.
- **Browser blocks the attachment downloads**: Allow multiple downloads from `discord.com` in your browser's site permissions.

## Notes

- Discord CDN URLs for file attachments contain auth tokens that expire after a few days. The full-mirror download prompt exists for this reason — grab the files immediately if you want to keep them.
- Custom emoji, avatars, and clan badges are filtered out before the image list is built. Junk images smaller than 10KB are skipped at download time as a safety net.
- The scraper deduplicates messages by DOM ID and backfills authors for Discord's "grouped" consecutive-author messages.
