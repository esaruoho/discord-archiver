"""Discord channel archiver.

Archives a single Discord channel's full message history to:
  - <output>/messages.json     machine-readable export
  - <output>/changelog.md      human-readable markdown
  - <output>/attachments/      downloaded images and files (date-prefixed)

Configuration is read from environment variables (or a .env file) and
overridable via CLI flags. No credentials live in this file.
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import discord
from dotenv import load_dotenv


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Archive a Discord channel to JSON + markdown + attachments."
    )
    p.add_argument(
        "--token",
        default=os.environ.get("DISCORD_BOT_TOKEN"),
        help="Discord bot token (default: $DISCORD_BOT_TOKEN)",
    )
    p.add_argument(
        "--channel",
        type=int,
        default=int(os.environ.get("DISCORD_CHANNEL_ID", 0)) or None,
        help="Channel ID to archive (default: $DISCORD_CHANNEL_ID)",
    )
    p.add_argument(
        "--output-dir",
        default=os.environ.get("OUTPUT_DIR", "./exports"),
        help="Where to write export files (default: ./exports)",
    )
    p.add_argument(
        "--title",
        default=os.environ.get("EXPORT_TITLE", "Channel Archive"),
        help="Title for the markdown changelog header",
    )
    p.add_argument(
        "--skip-pattern",
        action="append",
        default=[],
        metavar="REGEX",
        help="Regex; messages whose content matches are dropped. May be repeated. "
             'Example: --skip-pattern "^Posted to:" --skip-pattern "^Failed:"',
    )
    p.add_argument(
        "--no-attachments",
        action="store_true",
        help="Skip downloading attachments (URLs are still recorded in JSON).",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Resume from an existing <output-dir>/messages.json: only fetch "
             "messages newer than the last archived one. Rebuilds changelog.md "
             "from the combined message list.",
    )
    p.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Max retries per attachment download on 429/5xx/network errors (default: 5).",
    )
    return p.parse_args()


def sanitize_filename(name: str) -> str:
    return re.sub(r"[ :/\\]+", "_", name)


def render_embed_md_from_dict(embed: dict) -> str:
    parts: list[str] = []
    if embed.get("author"):
        parts.append(f"**{embed['author']}**")
    if embed.get("title"):
        url = embed.get("url")
        parts.append(f"[{embed['title']}]({url})" if url else f"**{embed['title']}**")
    elif embed.get("url"):
        parts.append(f"[Embedded Link]({embed['url']})")
    if embed.get("description"):
        parts.append(embed["description"])
    for field in embed.get("fields", []):
        parts.append(f"**{field['name']}:** {field['value']}")
    if embed.get("footer"):
        parts.append(f"_{embed['footer']}_")
    return "\n".join(parts)


def embed_to_dict(embed: discord.Embed) -> dict:
    return {
        "author": embed.author.name if embed.author else "",
        "title": embed.title or "",
        "url": embed.url or "",
        "description": embed.description or "",
        "fields": [{"name": f.name, "value": f.value} for f in embed.fields],
        "footer": embed.footer.text if embed.footer else "",
    }


def rebuild_markdown_block(record: dict) -> str:
    """Reconstruct a markdown block from a stored JSON message record."""
    date = record["timestamp"][:10] if record.get("timestamp") else "(no date)"
    block: list[str] = [f"### {date} - {record['author']}", "", record.get("content") or "_(no text)_"]
    for att in record.get("attachments", []):
        block.append(f"![]({att['local_path']})")
    for emb in record.get("embeds", []):
        rendered = render_embed_md_from_dict(emb)
        if rendered:
            block.append(rendered)
    return "\n\n".join(part for part in block if part)


async def download_attachment(
    session: aiohttp.ClientSession,
    url: str,
    dest_path: Path,
    max_retries: int,
) -> tuple[bool, str | None]:
    """Download with retry on 429 (honoring Retry-After) and exponential backoff on 5xx/network errors."""
    for attempt in range(max_retries + 1):
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    dest_path.write_bytes(await resp.read())
                    return True, None
                if resp.status == 429:
                    retry_after = float(resp.headers.get("Retry-After", "1"))
                    print(f"  rate-limited (429), sleeping {retry_after}s...")
                    await asyncio.sleep(retry_after)
                    continue
                if 500 <= resp.status < 600 and attempt < max_retries:
                    backoff = 2 ** attempt
                    print(f"  server error {resp.status}, retry in {backoff}s...")
                    await asyncio.sleep(backoff)
                    continue
                return False, f"HTTP {resp.status}"
        except aiohttp.ClientError as exc:
            if attempt < max_retries:
                backoff = 2 ** attempt
                print(f"  network error: {exc}, retry in {backoff}s...")
                await asyncio.sleep(backoff)
                continue
            return False, f"network: {exc}"
    return False, "max retries exceeded"


class Archiver(discord.Client):
    def __init__(self, *, args: argparse.Namespace, intents: discord.Intents):
        super().__init__(intents=intents)
        self.args = args
        self.skip_patterns = [re.compile(p, re.IGNORECASE) for p in args.skip_pattern]
        self.output_dir = Path(args.output_dir).expanduser().resolve()
        self.attachments_dir = self.output_dir / "attachments"
        self.messages_json_path = self.output_dir / "messages.json"

    async def on_ready(self) -> None:
        try:
            await self._run()
        finally:
            await self.close()

    def _load_existing(self) -> tuple[list[dict], int | None]:
        """Return (existing_records, last_message_id_int) for resume mode."""
        if not self.messages_json_path.exists():
            return [], None
        try:
            data = json.loads(self.messages_json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"Could not parse existing messages.json: {exc}", file=sys.stderr)
            return [], None
        records = data.get("messages", [])
        ids = [int(r["id"]) for r in records if r.get("id")]
        return records, max(ids) if ids else None

    async def _process_message(
        self,
        message: discord.Message,
        session: aiohttp.ClientSession,
    ) -> dict | None:
        content = (message.content or "").strip()
        if any(p.match(content) for p in self.skip_patterns):
            print(f"  skip: {content[:80]}")
            return None

        date = message.created_at.strftime("%Y-%m-%d")
        author = message.author.name

        attachment_records: list[dict] = []
        for att in message.attachments:
            safe = sanitize_filename(f"{date}_{att.filename}")
            local_path = self.attachments_dir / safe
            record = {
                "filename": att.filename,
                "url": att.url,
                "local_path": str(local_path.relative_to(self.output_dir)),
                "content_type": att.content_type,
                "size": att.size,
            }
            if not self.args.no_attachments:
                ok, err = await download_attachment(
                    session, att.url, local_path, self.args.max_retries
                )
                if ok:
                    print(f"  saved: {safe}")
                else:
                    print(f"  failed: {att.url}: {err}", file=sys.stderr)
                    record["download_failed"] = True
                    record["download_error"] = err
            attachment_records.append(record)

        return {
            "id": str(message.id),
            "timestamp": message.created_at.isoformat(),
            "author": author,
            "author_id": str(message.author.id),
            "content": content,
            "attachments": attachment_records,
            "embeds": [embed_to_dict(e) for e in message.embeds],
        }

    async def _run(self) -> None:
        print(f"Logged in as {self.user}")
        channel = self.get_channel(self.args.channel) or await self.fetch_channel(self.args.channel)
        if channel is None:
            print(f"Channel {self.args.channel} not found.", file=sys.stderr)
            return

        self.output_dir.mkdir(parents=True, exist_ok=True)
        if not self.args.no_attachments:
            self.attachments_dir.mkdir(parents=True, exist_ok=True)

        existing_records: list[dict] = []
        after_obj: discord.Object | None = None
        if self.args.resume:
            existing_records, last_id = self._load_existing()
            if last_id is not None:
                after_obj = discord.Object(id=last_id)
                print(f"Resuming after message ID {last_id} ({len(existing_records)} existing records)")
            else:
                print("Resume requested but no existing messages.json found — doing full archive.")

        new_records: list[dict] = []
        history_kwargs = {"limit": None, "oldest_first": True}
        if after_obj is not None:
            history_kwargs["after"] = after_obj

        async with aiohttp.ClientSession() as session:
            async for message in channel.history(**history_kwargs):
                record = await self._process_message(message, session)
                if record is not None:
                    new_records.append(record)

        all_records = existing_records + new_records

        self.messages_json_path.write_text(
            json.dumps(
                {
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                    "channel_id": str(self.args.channel),
                    "total_messages": len(all_records),
                    "messages": all_records,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        markdown_blocks = [rebuild_markdown_block(r) for r in all_records]
        (self.output_dir / "changelog.md").write_text(
            f"# {self.args.title}\n\n" + "\n\n---\n\n".join(markdown_blocks) + "\n",
            encoding="utf-8",
        )

        print(
            f"\nDone. {len(all_records)} messages total "
            f"({len(new_records)} new, {len(existing_records)} carried over) "
            f"→ {self.output_dir}"
        )


def main() -> int:
    load_dotenv()
    args = parse_args()
    if not args.token:
        print("Missing bot token. Set DISCORD_BOT_TOKEN in .env or pass --token.", file=sys.stderr)
        return 2
    if not args.channel:
        print("Missing channel ID. Set DISCORD_CHANNEL_ID in .env or pass --channel.", file=sys.stderr)
        return 2

    intents = discord.Intents.default()
    intents.messages = True
    intents.message_content = True
    client = Archiver(args=args, intents=intents)
    client.run(args.token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
