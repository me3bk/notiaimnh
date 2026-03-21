import logging
from datetime import datetime, timezone

import httpx
import asyncio

logger = logging.getLogger("aymannoti")

TIKTOK_COLOR = 0x69C9D0    # TikTok brand teal
INSTAGRAM_COLOR = 0xE1306C  # Instagram brand pink


class Notifier:
    def __init__(self, bot_name: str = "Aymannoti"):
        self.bot_name = bot_name
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=15)
        return self._client

    async def send(
        self,
        webhook_url: str,
        username: str,
        post: dict,
        group_name: str,
        platform: str = "tiktok",
    ):
        """Send a Discord webhook notification for a new post."""
        if platform == "instagram":
            msg_parts = [
                "@everyone",
                f"New Instagram post from @{username}",
                f"@{username} just posted on Instagram!",
                "",
                post["url"],
            ]
        else:
            msg_parts = [
                "@everyone",
                f"New TikTok from @{username}",
                f"@{username} just posted a new video!",
                "",
                post["url"],
            ]

        payload = {
            "username": self.bot_name,
            "content": "\n".join(msg_parts),
        }

        client = await self._get_client()
        resp = await client.post(webhook_url, json=payload)

        if resp.status_code == 429:
            retry_after = resp.json().get("retry_after", 5)
            logger.warning(f"Discord rate-limited, retrying in {retry_after}s")
            await asyncio.sleep(retry_after)
            resp = await client.post(webhook_url, json=payload)

        resp.raise_for_status()

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.close()
