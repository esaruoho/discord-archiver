"""Discord channel archiver.

Archives a single Discord channel's full message history to:
  - <output>/messages.json     machine-readable export
  - <output>/changelog.md      human-readable markdown
  - <output>/attachments/      downloaded images and files (date-prefixed)

Configuration is read from environment variables (or a .env file) and
overridable via CLI flags. No credentials live in this file.
"""

import argparse
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
    return p.parse_args()


def sanitize_filename(name: str) -> str:
    return re.sub(r"[ :/\\]+", "_", name)


def render_embed_md(embed: discord.Embed) -> str:
    parts: list[str] = []
    if embed.author and embed.author.name:
        parts.append(f"**{embed.author.name}**")
    if embed.title:
        parts.append(f"[{embed.title}]({embed.url})" if embed.url else f"**{embed.title}**")
    elif embed.url:
        parts.append(f"[Embedded Link]({embed.url})")
    if embed.description:
        parts.append(embed.description)
    for field in embed.fields:
        parts.append(f"**{field.name}:** {field.value}")
    if embed.footer and embed.footer.text:
        parts.append(f"_{embed.footer.text}_")
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


class Archiver(discord.Client):
    def __init__(self, *, args: argparse.Namespace, intents: discord.Intents):
        super().__init__(intents=intents)
        self.args = args
        self.skip_patterns = [re.compile(p, re.IGNORECASE) for p in args.skip_pattern]
        self.output_dir = Path(args.output_dir).expanduser().resolve()
        self.attachments_dir = self.output_dir / "attachments"

    async def on_ready(self) -> None:
        try:
            await self._run()
        finally:
            await self.close()

    async def _run(self) -> None:
        print(f"Logged in as {self.user}")
        channel = self.get_channel(self.args.channel) or await self.fetch_channel(self.args.channel)
        if channel is None:
            print(f"Channel {self.args.channel} not found.", file=sys.stderr)
            return

        self.output_dir.mkdir(parents=True, exist_ok=True)
        if not self.args.no_attachments:
            self.attachments_dir.mkdir(parents=True, exist_ok=True)

        messages_json: list[dict] = []
        markdown_blocks: list[str] = []

        async with aiohttp.ClientSession() as session:
            async for message in channel.history(limit=None, oldest_first=True):
                content = (message.content or "").strip()
                if any(p.match(content) for p in self.skip_patterns):
                    print(f"  skip: {content[:80]}")
                    continue

                date = message.created_at.strftime("%Y-%m-%d")
                author = message.author.name

                attachment_records: list[dict] = []
                attachment_md: list[str] = []
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
                        try:
                            async with session.get(att.url) as resp:
                                if resp.status == 200:
                                    local_path.write_bytes(await resp.read())
                                    print(f"  saved: {safe}")
                                else:
                                    print(f"  failed ({resp.status}): {att.url}", file=sys.stderr)
                                    record["download_failed"] = True
                        except Exception as exc:
                            print(f"  error: {att.url}: {exc}", file=sys.stderr)
                            record["download_failed"] = True
                    attachment_records.append(record)
                    attachment_md.append(f"![]({record['local_path']})")

                embed_records = [embed_to_dict(e) for e in message.embeds]
                embed_md = [render_embed_md(e) for e in message.embeds]
                embed_md = [e for e in embed_md if e]

                messages_json.append({
                    "id": str(message.id),
                    "timestamp": message.created_at.isoformat(),
                    "author": author,
                    "author_id": str(message.author.id),
                    "content": content,
                    "attachments": attachment_records,
                    "embeds": embed_records,
                })

                block = [f"### {date} - {author}", "", content or "_(no text)_"]
                block.extend(attachment_md)
                block.extend(embed_md)
                markdown_blocks.append("\n\n".join(part for part in block if part))

        (self.output_dir / "messages.json").write_text(
            json.dumps(
                {
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                    "channel_id": str(self.args.channel),
                    "total_messages": len(messages_json),
                    "messages": messages_json,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        (self.output_dir / "changelog.md").write_text(
            f"# {self.args.title}\n\n" + "\n\n---\n\n".join(markdown_blocks) + "\n",
            encoding="utf-8",
        )

        print(f"\nDone. {len(messages_json)} messages → {self.output_dir}")


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
