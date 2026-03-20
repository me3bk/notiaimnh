import sqlite3
from datetime import datetime, timezone


class Database:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _create_tables(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_posts (
                username TEXT NOT NULL,
                post_id TEXT NOT NULL,
                post_url TEXT,
                seen_at TEXT NOT NULL,
                PRIMARY KEY (username, post_id)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                accounts_checked INTEGER DEFAULT 0,
                notifications_sent INTEGER DEFAULT 0,
                errors INTEGER DEFAULT 0,
                duration_seconds REAL DEFAULT 0,
                details TEXT DEFAULT ''
            )
        """)
        # Add duration_seconds column if missing (upgrade existing DBs)
        try:
            self.conn.execute("ALTER TABLE bot_log ADD COLUMN duration_seconds REAL DEFAULT 0")
        except Exception:
            pass  # Column already exists
        self.conn.commit()

    # ── Seen Posts ───────────────────────────────────────────────

    def is_seen(self, username: str, post_id: str) -> bool:
        cursor = self.conn.execute(
            "SELECT 1 FROM seen_posts WHERE username = ? AND post_id = ?",
            (username, post_id),
        )
        return cursor.fetchone() is not None

    def has_been_checked(self, username: str) -> bool:
        """Check if a username has ever been polled before."""
        cursor = self.conn.execute(
            "SELECT 1 FROM seen_posts WHERE username = ? LIMIT 1",
            (username,),
        )
        return cursor.fetchone() is not None

    def mark_seen(self, username: str, post_id: str, post_url: str):
        self.conn.execute(
            "INSERT OR IGNORE INTO seen_posts (username, post_id, post_url, seen_at) VALUES (?, ?, ?, ?)",
            (username, post_id, post_url, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def get_stats(self) -> dict:
        cur = self.conn.execute("SELECT COUNT(DISTINCT username) FROM seen_posts")
        users_tracked = cur.fetchone()[0]
        cur = self.conn.execute("SELECT COUNT(*) FROM seen_posts")
        total_posts = cur.fetchone()[0]
        cur = self.conn.execute("SELECT MAX(seen_at) FROM seen_posts")
        last_activity = cur.fetchone()[0]
        return {
            "users_tracked": users_tracked,
            "total_posts": total_posts,
            "last_activity": last_activity,
        }

    def get_user_stats(self, username: str) -> dict:
        cur = self.conn.execute(
            "SELECT COUNT(*), MAX(seen_at) FROM seen_posts WHERE username = ?",
            (username,),
        )
        row = cur.fetchone()
        return {"posts_seen": row[0], "last_seen": row[1]}

    # ── Bot Log ─────────────────────────────────────────────────

    def add_log(
        self,
        event: str,
        level: str = "info",
        accounts_checked: int = 0,
        notifications_sent: int = 0,
        errors: int = 0,
        duration_seconds: float = 0,
        details: str = "",
    ):
        self.conn.execute(
            "INSERT INTO bot_log (timestamp, event, level, accounts_checked, notifications_sent, errors, duration_seconds, details) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                event,
                level,
                accounts_checked,
                notifications_sent,
                errors,
                duration_seconds,
                details,
            ),
        )
        self.conn.commit()

    def get_logs(self, limit: int = 100) -> list[dict]:
        cur = self.conn.execute(
            "SELECT id, timestamp, event, level, accounts_checked, notifications_sent, errors, duration_seconds, details "
            "FROM bot_log ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [
            {
                "id": r[0],
                "timestamp": r[1],
                "event": r[2],
                "level": r[3],
                "accounts_checked": r[4],
                "notifications_sent": r[5],
                "errors": r[6],
                "duration": r[7] or 0,
                "details": r[8],
            }
            for r in cur.fetchall()
        ]

    def get_log_summary(self) -> dict:
        """Get a summary of recent bot activity."""
        cur = self.conn.execute(
            "SELECT COUNT(*), SUM(notifications_sent), SUM(errors), AVG(duration_seconds) "
            "FROM bot_log WHERE event = 'cycle_complete'"
        )
        row = cur.fetchone()
        total_cycles = row[0] or 0
        total_notifs = row[1] or 0
        total_errors = row[2] or 0
        avg_duration = round(row[3] or 0, 1)

        cur = self.conn.execute(
            "SELECT timestamp, accounts_checked, notifications_sent, errors, duration_seconds "
            "FROM bot_log WHERE event = 'cycle_complete' ORDER BY id DESC LIMIT 1"
        )
        last = cur.fetchone()
        return {
            "total_cycles": total_cycles,
            "total_notifications": total_notifs,
            "total_errors": total_errors,
            "avg_duration": avg_duration,
            "last_cycle": {
                "timestamp": last[0],
                "accounts_checked": last[1],
                "notifications_sent": last[2],
                "errors": last[3],
                "duration": round(last[4] or 0, 1),
            } if last else None,
        }

    def clear_logs(self):
        self.conn.execute("DELETE FROM bot_log")
        self.conn.commit()

    def close(self):
        self.conn.close()
