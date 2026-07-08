"""
SQLite Memory Store — Persistent storage backend for cases, episodes, and skills.

Replaces JSONL file scanning with indexed SQLite queries. Provides:
- O(1) lookup by ID (vs O(n) JSONL scan)
- FTS5 full-text search on task descriptions
- Atomic writes (no corruption on interruption)
- Efficient pagination and filtering
- Cross-table queries (JOIN cases with rules_applied)

The existing JSONL interfaces are preserved — this is a drop-in backend.
Each memory module can optionally use SQLiteStore instead of file I/O.

Schema:
- cases: case_id, timestamp, task, app_type, outcome, failure_point, json_data
- episodes: rowid, timestamp, task, outcome, correction, json_data
- skills: skill_id, name, task, app_type, times_used, json_data
- sessions: session_id, user_id, start_time, end_time, json_data
- preferences: category, value, confidence, source, json_data

FTS5 virtual tables for full-text search:
- cases_fts: task, app_type, failure_point
- episodes_fts: task, correction
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from sage.closeable import CloseableMixin  # noqa: E402


class SQLiteStore(CloseableMixin):
    """
    Unified SQLite backend for all structured memory.

    Thread-safe via connection-per-thread pattern.
    Provides typed methods for cases, episodes, skills, sessions, preferences.
    """

    SCHEMA_VERSION = 1

    def __init__(self, db_path: str = "memory/sage.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._all_conns: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        self._init_schema()

    @property
    def _conn(self) -> sqlite3.Connection:
        """Get thread-local connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=10.0,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
            with self._lock:
                self._all_conns.append(conn)
        return self._local.conn

    def _init_schema(self):
        """Create tables if they don't exist."""
        conn = self._conn
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS cases (
                case_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                task TEXT NOT NULL,
                app_type TEXT DEFAULT '',
                outcome TEXT NOT NULL,
                failure_point TEXT,
                correction TEXT,
                json_data TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                task TEXT NOT NULL,
                outcome TEXT NOT NULL,
                correction TEXT,
                rule_id TEXT,
                json_data TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS skills (
                skill_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                task TEXT NOT NULL,
                app_type TEXT DEFAULT '',
                times_used INTEGER DEFAULT 0,
                verified INTEGER DEFAULT 1,
                json_data TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT DEFAULT 'default',
                start_time TEXT NOT NULL,
                end_time TEXT,
                tasks_completed INTEGER DEFAULT 0,
                corrections INTEGER DEFAULT 0,
                json_data TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS preferences (
                category TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                source TEXT DEFAULT 'explicit',
                times_confirmed INTEGER DEFAULT 1,
                learned TEXT,
                last_used TEXT,
                json_data TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_cases_outcome ON cases(outcome);
            CREATE INDEX IF NOT EXISTS idx_cases_task ON cases(task);
            CREATE INDEX IF NOT EXISTS idx_episodes_outcome ON episodes(outcome);
            CREATE INDEX IF NOT EXISTS idx_episodes_task ON episodes(task);
            CREATE INDEX IF NOT EXISTS idx_skills_app_type ON skills(app_type);
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        """)

        # FTS5 for full-text search (if not exists)
        try:
            conn.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS cases_fts USING fts5(
                    task, app_type, failure_point, correction,
                    content='cases',
                    content_rowid='rowid'
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
                    task, correction,
                    content='episodes',
                    content_rowid='id'
                );
            """)
        except sqlite3.OperationalError as e:
            logger.debug("FTS5 setup note: %s", e)

        conn.commit()

    # ─── Cases ───────────────────────────────────────────────────────────────

    def insert_case(self, case: dict) -> str:
        """Insert a case record. Returns case_id."""
        case_id = case["case_id"]
        self._conn.execute(
            """INSERT OR REPLACE INTO cases
               (case_id, timestamp, task, app_type, outcome, failure_point, correction, json_data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                case_id,
                case.get("timestamp", ""),
                case.get("task", ""),
                case.get("app_type", ""),
                case.get("outcome", ""),
                case.get("failure_point"),
                case.get("correction"),
                json.dumps(case),
            ),
        )
        # Update FTS
        try:
            self._conn.execute(
                "INSERT INTO cases_fts(rowid, task, app_type, failure_point, correction) "
                "VALUES (last_insert_rowid(), ?, ?, ?, ?)",
                (
                    case.get("task", ""),
                    case.get("app_type", ""),
                    case.get("failure_point", ""),
                    case.get("correction", ""),
                ),
            )
        except sqlite3.OperationalError:
            pass
        self._conn.commit()
        return case_id

    def get_case(self, case_id: str) -> Optional[dict]:
        """Get a case by ID. O(1)."""
        row = self._conn.execute(
            "SELECT json_data FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        return json.loads(row["json_data"]) if row else None

    def get_all_cases(self) -> list[dict]:
        """Get all cases."""
        rows = self._conn.execute(
            "SELECT json_data FROM cases ORDER BY timestamp"
        ).fetchall()
        return [json.loads(r["json_data"]) for r in rows]

    def get_recent_cases(self, n: int = 5) -> list[dict]:
        """Get N most recent cases."""
        rows = self._conn.execute(
            "SELECT json_data FROM cases ORDER BY timestamp DESC LIMIT ?", (n,)
        ).fetchall()
        return [json.loads(r["json_data"]) for r in reversed(rows)]

    def search_cases(self, query: str, limit: int = 5) -> list[dict]:
        """Full-text search on cases."""
        try:
            rows = self._conn.execute(
                "SELECT c.json_data FROM cases_fts f "
                "JOIN cases c ON c.rowid = f.rowid "
                "WHERE cases_fts MATCH ? LIMIT ?",
                (query, limit),
            ).fetchall()
            return [json.loads(r["json_data"]) for r in rows]
        except sqlite3.OperationalError:
            # FTS not available, fall back to LIKE
            rows = self._conn.execute(
                "SELECT json_data FROM cases WHERE task LIKE ? LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
            return [json.loads(r["json_data"]) for r in rows]

    def get_case_stats(self) -> dict:
        """Get case statistics."""
        row = self._conn.execute(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END) as success "
            "FROM cases"
        ).fetchone()
        total = row["total"] or 0
        success = row["success"] or 0
        return {"total": total, "success": success, "failed": total - success}

    # ─── Episodes ────────────────────────────────────────────────────────────

    def insert_episode(self, episode: dict) -> int:
        """Insert an episode. Returns row ID."""
        cur = self._conn.execute(
            """INSERT INTO episodes (timestamp, task, outcome, correction, rule_id, json_data)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                episode.get("timestamp", ""),
                episode.get("task", ""),
                episode.get("outcome", ""),
                episode.get("correction"),
                episode.get("rule_id"),
                json.dumps(episode),
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_recent_episodes(self, n: int = 10) -> list[dict]:
        """Get N most recent episodes."""
        rows = self._conn.execute(
            "SELECT json_data FROM episodes ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return [json.loads(r["json_data"]) for r in reversed(rows)]

    def get_episode_stats(self) -> dict:
        """Get episode statistics."""
        row = self._conn.execute(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END) as success, "
            "SUM(CASE WHEN correction IS NOT NULL THEN 1 ELSE 0 END) as corrections "
            "FROM episodes"
        ).fetchone()
        total = row["total"] or 0
        success = row["success"] or 0
        corrections = row["corrections"] or 0
        return {
            "total": total,
            "success": success,
            "failed": total - success,
            "corrections": corrections,
            "success_rate": success / total if total > 0 else 0.0,
        }

    def count_episodes(self) -> int:
        """Get total episode count."""
        row = self._conn.execute("SELECT COUNT(*) as c FROM episodes").fetchone()
        return row["c"] or 0

    # ─── Skills ──────────────────────────────────────────────────────────────

    def insert_skill(self, skill: dict) -> str:
        """Insert a skill. Returns skill_id."""
        skill_id = skill["skill_id"]
        self._conn.execute(
            """INSERT OR REPLACE INTO skills
               (skill_id, name, task, app_type, times_used, verified, json_data)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                skill_id,
                skill.get("name", ""),
                skill.get("task", ""),
                skill.get("app_type", ""),
                skill.get("times_used", 0),
                1 if skill.get("verified", True) else 0,
                json.dumps(skill),
            ),
        )
        self._conn.commit()
        return skill_id

    def get_all_skills(self) -> list[dict]:
        """Get all skills."""
        rows = self._conn.execute(
            "SELECT json_data FROM skills ORDER BY times_used DESC"
        ).fetchall()
        return [json.loads(r["json_data"]) for r in rows]

    def increment_skill_usage(self, skill_id: str):
        """Increment usage counter for a skill."""
        self._conn.execute(
            "UPDATE skills SET times_used = times_used + 1 WHERE skill_id = ?",
            (skill_id,),
        )
        self._conn.commit()

    # ─── Sessions ────────────────────────────────────────────────────────────

    def insert_session(self, session: dict) -> str:
        """Insert or update a session record."""
        session_id = session["session_id"]
        self._conn.execute(
            """INSERT OR REPLACE INTO sessions
               (session_id, user_id, start_time, end_time, tasks_completed, corrections, json_data)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                session.get("user_id", "default"),
                session.get("start_time", ""),
                session.get("end_time"),
                session.get("tasks_completed", 0),
                session.get("corrections", 0),
                json.dumps(session),
            ),
        )
        self._conn.commit()
        return session_id

    def get_recent_sessions(self, n: int = 5) -> list[dict]:
        """Get N most recent sessions."""
        rows = self._conn.execute(
            "SELECT json_data FROM sessions ORDER BY start_time DESC LIMIT ?", (n,)
        ).fetchall()
        return [json.loads(r["json_data"]) for r in rows]

    # ─── Preferences ─────────────────────────────────────────────────────────

    def upsert_preference(self, category: str, pref: dict):
        """Insert or update a preference."""
        self._conn.execute(
            """INSERT OR REPLACE INTO preferences
               (category, value, confidence, source, times_confirmed, learned, last_used, json_data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                category,
                pref.get("value", ""),
                pref.get("confidence", 0.5),
                pref.get("source", "explicit"),
                pref.get("times_confirmed", 1),
                pref.get("learned", ""),
                pref.get("last_used", ""),
                json.dumps(pref),
            ),
        )
        self._conn.commit()

    def get_all_preferences(self) -> dict[str, dict]:
        """Get all preferences as {category: pref_dict}."""
        rows = self._conn.execute(
            "SELECT category, json_data FROM preferences WHERE confidence >= 0.3"
        ).fetchall()
        return {r["category"]: json.loads(r["json_data"]) for r in rows}

    # ─── Utility ─────────────────────────────────────────────────────────────

    def get_db_stats(self) -> dict:
        """Get database statistics."""
        stats = {}
        for table in ("cases", "episodes", "skills", "sessions", "preferences"):
            row = self._conn.execute(f"SELECT COUNT(*) as c FROM {table}").fetchone()
            stats[table] = row["c"] or 0
        # DB file size
        try:
            stats["db_size_bytes"] = self.db_path.stat().st_size
        except OSError:
            stats["db_size_bytes"] = 0
        return stats

    def close(self):
        """Close ALL database connections across all threads."""
        with self._lock:
            for conn in self._all_conns:
                try:
                    conn.close()
                except Exception:
                    pass
            self._all_conns.clear()
        if hasattr(self._local, "conn"):
            self._local.conn = None



if __name__ == "__main__":
    store = SQLiteStore("/tmp/test_sage.db")
    print(f"DB stats: {store.get_db_stats()}")

    # Insert a case
    store.insert_case(
        {
            "case_id": "C001",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task": "Deploy Node.js web app",
            "app_type": "node",
            "outcome": "failed",
            "failure_point": "security_group",
            "steps": [],
        }
    )

    print(f"After insert: {store.get_db_stats()}")
    print(f"Case stats: {store.get_case_stats()}")
    print(f"Search 'node': {store.search_cases('node')}")
    store.close()
