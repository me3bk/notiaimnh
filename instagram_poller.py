import asyncio
import logging
import os
import time

import instaloader

logger = logging.getLogger("aymannoti")

# Permanent errors — no point retrying
_PERMANENT_EXCEPTIONS = (
    instaloader.exceptions.ProfileNotExistsException,
    instaloader.exceptions.PrivateProfileNotFollowedException,
    instaloader.exceptions.LoginRequiredException,
)
_PERMANENT_MSG_HINTS = ("does not exist", "not found", "404", "private")


def _detect_post_type(post: instaloader.Post) -> str:
    """Detect Instagram post type from instaloader Post object."""
    try:
        if hasattr(post, "product_type") and post.product_type == "clips":
            return "reel"
    except Exception:
        pass
    if post.is_video:
        return "reel"
    if post.typename == "GraphSidecar":
        return "post"
    return "post"


def _make_loader(session_file: str = "", username: str = "", password: str = "") -> instaloader.Instaloader:
    """Create and authenticate an instaloader instance."""
    L = instaloader.Instaloader(
        quiet=True,
        max_connection_attempts=1,
        request_timeout=20,
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        post_metadata_txt_pattern="",
    )
    if session_file and os.path.exists(session_file):
        try:
            L.load_session_from_file(username or "", session_file)
            logger.debug("[Instagram] Loaded session from file")
            return L
        except Exception as e:
            logger.warning(f"[Instagram] Failed to load session file: {e}")
    if username and password:
        try:
            L.login(username, password)
            if session_file:
                L.save_session_to_file(session_file)
                logger.info(f"[Instagram] Logged in and saved session to {session_file}")
            return L
        except Exception as e:
            logger.warning(f"[Instagram] Login failed: {e}")
    return L


class InstagramPoller:
    def __init__(
        self,
        cookies_file: str = "",
        username: str = "",
        password: str = "",
        max_retries: int = 3,
    ):
        # cookies_file is reused as the instaloader session file path
        self._cookies_file = cookies_file
        self._username = username
        self._password = password
        self._max_retries = max_retries
        self._loader: instaloader.Instaloader | None = None
        if not cookies_file and not username:
            logger.warning(
                "InstagramPoller: no session_file or username set. "
                "Instagram requires authentication — "
                "set instagram.username + password in config.yaml"
            )

    def _get_loader(self) -> instaloader.Instaloader:
        """Get or create a cached authenticated instaloader instance."""
        if self._loader is None:
            self._loader = _make_loader(self._cookies_file, self._username, self._password)
        return self._loader

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
            self._loader = None  # force re-auth on next use
            logger.info(
                f"[Instagram] Auth config updated: "
                f"session={'set' if cookies_file else 'none'}, "
                f"username={'set' if username else 'none'}"
            )

    async def fetch_feed(self, username: str) -> list[dict]:
        """Fetch recent posts for an Instagram user via instaloader with retry."""
        return await asyncio.to_thread(self._extract_with_retry, username)

    # ── retry wrapper ─────────────────────────────────────────────

    def _extract_with_retry(self, username: str) -> list[dict]:
        last_err: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return self._extract_profile(username)
            except _PERMANENT_EXCEPTIONS:
                raise
            except Exception as e:
                msg = str(e).lower()
                if any(k in msg for k in _PERMANENT_MSG_HINTS):
                    raise
                last_err = e
                wait = 2 ** attempt
                logger.warning(
                    f"[Instagram] Attempt {attempt}/{self._max_retries} failed for "
                    f"@{username}, retrying in {wait}s: {e}"
                )
                time.sleep(wait)
        raise last_err  # type: ignore[misc]

    # ── extraction ────────────────────────────────────────────────

    def _extract_profile(self, username: str) -> list[dict]:
        clean = username.lstrip("@")
        L = self._get_loader()
        profile = instaloader.Profile.from_username(L.context, clean)
        posts = []
        for post in profile.get_posts():
            if len(posts) >= 5:
                break
            shortcode = post.shortcode
            post_url = f"https://www.instagram.com/p/{shortcode}/"
            posts.append(
                {
                    "id": shortcode,
                    "title": (post.caption or "")[:100],
                    "url": post_url,
                    "post_type": _detect_post_type(post),
                    "description": (post.caption or "")[:300],
                    "published": int(post.date_utc.timestamp()),
                    "thumbnail": post.url,
                }
            )
        return posts
