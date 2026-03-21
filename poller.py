import asyncio
import logging
import time

import yt_dlp

logger = logging.getLogger("aymannoti")

# Accounts that exist but have 0 videos trigger this message
_ZERO_POSTS_HINT = "does not have any videos"


class Poller:
    def __init__(self, cookies_file: str = "", max_retries: int = 3):
        self._base_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "playlistend": 5,
            "socket_timeout": 20,
        }
        if cookies_file:
            self._base_opts["cookiefile"] = cookies_file
        self._max_retries = max_retries

    async def fetch_feed(self, username: str) -> list[dict]:
        """Fetch recent posts for a TikTok user via yt-dlp with retry."""
        return await asyncio.to_thread(self._extract_with_retry, username)

    # ── retry wrapper ────────────────────────────────────────────

    def _extract_with_retry(self, username: str) -> list[dict]:
        last_err = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return self._extract(username)
            except yt_dlp.utils.DownloadError as e:
                msg = str(e)
                # Account valid but 0 posts — not a real failure
                if _ZERO_POSTS_HINT in msg:
                    return []
                # Permanent errors — no point retrying
                if (
                    "Unable to extract" in msg
                    or "does not exist" in msg
                    or "404" in msg
                    or "account is private" in msg.lower()
                    or "This account is private" in msg
                ):
                    raise
                # Transient error — retry with backoff
                last_err = e
                wait = 2 ** attempt
                logger.warning(
                    f"yt-dlp attempt {attempt}/{self._max_retries} failed for @{username}, "
                    f"retrying in {wait}s: {msg[:120]}"
                )
                time.sleep(wait)
            except Exception as e:
                last_err = e
                wait = 2 ** attempt
                logger.warning(
                    f"Attempt {attempt}/{self._max_retries} failed for @{username}, "
                    f"retrying in {wait}s: {e}"
                )
                time.sleep(wait)
        raise last_err  # type: ignore[misc]

    # ── core extraction ──────────────────────────────────────────

    def _extract(self, username: str) -> list[dict]:
        clean = username.lstrip("@")
        url = f"https://www.tiktok.com/@{clean}"
        posts = []
        with yt_dlp.YoutubeDL(self._base_opts) as ydl:
            result = ydl.extract_info(url, download=False)
            if not result:
                return posts
            for entry in result.get("entries") or []:
                if not entry:
                    continue
                vid = str(entry.get("id", ""))
                if not vid:
                    continue
                posts.append(
                    {
                        "id": vid,
                        "title": entry.get("title", ""),
                        "url": f"https://www.tiktok.com/@{clean}/video/{vid}",
                        "description": (
                            entry.get("description")
                            or entry.get("title")
                            or ""
                        )[:300],
                        "published": entry.get("timestamp")
                        or entry.get("upload_date", ""),
                        "thumbnail": self._get_thumbnail(entry),
                    }
                )
        return posts

    @staticmethod
    def _get_thumbnail(entry: dict) -> str | None:
        thumbs = entry.get("thumbnails")
        if thumbs and isinstance(thumbs, list):
            return thumbs[0].get("url")
        return entry.get("thumbnail")
