import asyncio
import logging
import time

import yt_dlp

logger = logging.getLogger("aymannoti")

# Permanent errors — no point retrying, the account is gone/private
_PERMANENT_ERRORS = (
    "does not exist",
    "404",
    "this account is private",
    "sorry, this page",
    "not available",
    "login_required",
    "checkpoint_required",
    "no media",
)

# Rate-limit hints — use longer backoff
_RATE_LIMIT_HINTS = ("rate", "429", "too many", "please wait", "temporarily")


def _detect_post_type(url: str) -> str:
    """Detect Instagram post type from its URL path."""
    if "/reel/" in url:
        return "reel"
    if "/tv/" in url:
        return "igtv"
    if "/stories/" in url:
        return "story"
    return "post"  # /p/ or unknown → treat as post


class InstagramPoller:
    def __init__(
        self,
        cookies_file: str = "",
        username: str = "",
        password: str = "",
        max_retries: int = 3,
    ):
        self._cookies_file = cookies_file
        self._username = username
        self._password = password
        self._max_retries = max_retries
        self._base_opts = self._build_opts(cookies_file, username, password)
        if not cookies_file and not username:
            logger.warning(
                "InstagramPoller: no cookies_file or username set. "
                "Instagram heavily rate-limits unauthenticated requests — "
                "run: python manage.py instagram setup-cookies --browser chrome"
            )

    def _build_opts(self, cookies_file: str, username: str = "", password: str = "") -> dict:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "playlistend": 5,
            "socket_timeout": 20,
        }
        if cookies_file:
            opts["cookiefile"] = cookies_file
        if username:
            opts["username"] = username
        if password:
            opts["password"] = password
        return opts

    def update_cookies(self, cookies_file: str, username: str = "", password: str = "") -> None:
        """Hot-reload auth config when values change between cycles."""
        changed = (
            cookies_file != self._cookies_file
            or username != self._username
            or password != self._password
        )
        if changed:
            self._cookies_file = cookies_file
            self._username = username
            self._password = password
            self._base_opts = self._build_opts(cookies_file, username, password)
            logger.info(
                f"[Instagram] Auth config updated: "
                f"cookies={'set' if cookies_file else 'none'}, "
                f"username={'set' if username else 'none'}"
            )

    async def fetch_feed(self, username: str) -> list[dict]:
        """Fetch recent posts/reels for an Instagram user via yt-dlp with retry."""
        return await asyncio.to_thread(self._extract_with_retry, username)

    # ── retry wrapper ─────────────────────────────────────────────

    def _extract_with_retry(self, username: str) -> list[dict]:
        last_err = None
        for attempt in range(1, self._max_retries + 1):
            try:
                # Primary: full profile feed (posts + reels + igtv)
                posts = self._extract_profile(username)
                # Fallback: if profile returns nothing, try reels-specific URL
                if not posts:
                    logger.debug(f"[Instagram] @{username}: profile empty, trying /reels/ fallback")
                    posts = self._extract_reels(username)
                return posts
            except yt_dlp.utils.DownloadError as e:
                msg = str(e).lower()
                if any(k in msg for k in _PERMANENT_ERRORS):
                    raise
                last_err = e
                # Rate-limited: use longer backoff (4^n) vs regular transient (2^n)
                is_rate = any(k in msg for k in _RATE_LIMIT_HINTS)
                wait = (4 ** attempt) if is_rate else (2 ** attempt)
                logger.warning(
                    f"[Instagram] yt-dlp attempt {attempt}/{self._max_retries} failed for "
                    f"@{username}, retrying in {wait}s: {str(e)[:120]}"
                )
                time.sleep(wait)
            except Exception as e:
                last_err = e
                wait = 2 ** attempt
                logger.warning(
                    f"[Instagram] Attempt {attempt}/{self._max_retries} failed for "
                    f"@{username}, retrying in {wait}s: {e}"
                )
                time.sleep(wait)
        raise last_err  # type: ignore[misc]

    # ── extraction methods ────────────────────────────────────────

    def _extract_profile(self, username: str) -> list[dict]:
        """Primary: fetch from the user profile URL (all post types)."""
        clean = username.lstrip("@")
        return self._run_extraction(f"https://www.instagram.com/{clean}/", clean)

    def _extract_reels(self, username: str) -> list[dict]:
        """Fallback: fetch from the reels-specific URL."""
        clean = username.lstrip("@")
        return self._run_extraction(f"https://www.instagram.com/{clean}/reels/", clean)

    def _run_extraction(self, url: str, clean_username: str) -> list[dict]:
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
                # webpage_url carries the canonical URL including /reel/ or /p/
                post_url = (
                    entry.get("webpage_url")
                    or entry.get("url")
                    or f"https://www.instagram.com/p/{vid}/"
                )
                post_type = _detect_post_type(post_url)
                posts.append(
                    {
                        "id": vid,
                        "title": entry.get("title", ""),
                        "url": post_url,
                        "post_type": post_type,
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
