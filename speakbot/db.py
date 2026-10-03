"""SQLite baza: imtihonlar (speaking/writing), savollar, urinishlar, javoblar, writing ishlari."""
import json
from datetime import datetime, timedelta, timezone

import aiosqlite

import config
from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS exams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    is_active INTEGER DEFAULT 0,
    kind TEXT DEFAULT 'speaking',
    part TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    text TEXT NOT NULL,
    photo_file_id TEXT,
    prep_sec INTEGER DEFAULT 30,
    answer_sec INTEGER DEFAULT 60
);
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    full_name TEXT,
    username TEXT,
    joined_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    exam_id INTEGER NOT NULL,
    status TEXT DEFAULT 'in_progress',
    score INTEGER,
    raw_score REAL,
    level TEXT,
    result_json TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    transcript TEXT,
    duration_sec REAL,
    UNIQUE(attempt_id, question_id)
);
CREATE TABLE IF NOT EXISTS admins (
    telegram_id INTEGER PRIMARY KEY,
    added_by INTEGER,
    added_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS premium (
    telegram_id INTEGER PRIMARY KEY,
    until TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS premium_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    days INTEGER,
    amount TEXT,
    granted_by INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS writing_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    exam_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    words INTEGER,
    status TEXT DEFAULT 'pending',
    score INTEGER,
    raw_score REAL,
    level TEXT,
    result_json TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

# Eski bazalarni yangilash: ustun bor bo'lsa - xato beradi va o'tkazib yuboriladi
_MIGRATIONS = [
    "ALTER TABLE answers ADD COLUMN duration_sec REAL",
    "ALTER TABLE exams ADD COLUMN kind TEXT DEFAULT 'speaking'",
    "ALTER TABLE exams ADD COLUMN part TEXT",
    "ALTER TABLE attempts ADD COLUMN raw_score REAL",
]


def _conn():
    return aiosqlite.connect(DB_PATH)


async def init_db():
    async with _conn() as db:
        await db.executescript(SCHEMA)
        for sql in _MIGRATIONS:
            try:
                await db.execute(sql)
            except aiosqlite.OperationalError:
                pass
        # Yangi format: writing'da 1.1 va 1.2 alohida emas - ikkalasi "1-qism"
        await db.execute("UPDATE exams SET part = '1' WHERE kind = 'writing' AND part IN ('1.1', '1.2')")
        await db.commit()


async def _fetchall(sql, params=()):
    async with _conn() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def _fetchone(sql, params=()):
    rows = await _fetchall(sql, params)
    return rows[0] if rows else None


async def _execute(sql, params=()) -> int:
    async with _conn() as db:
        cur = await db.execute(sql, params)
        await db.commit()
        return cur.lastrowid


# ---------- Foydalanuvchilar ----------

async def save_user(telegram_id: int, full_name: str, username: str | None):
    await _execute(
        """INSERT INTO users (telegram_id, full_name, username) VALUES (?, ?, ?)
           ON CONFLICT(telegram_id) DO UPDATE SET full_name = excluded.full_name, username = excluded.username""",
        (telegram_id, full_name, username),
    )


async def get_user(telegram_id: int):
    return await _fetchone("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))


# ---------- Imtihonlar (speaking to'plamlari va writing mavzulari) ----------

async def create_exam(title: str, kind: str = "speaking", part: str | None = None) -> int:
    return await _execute("INSERT INTO exams (title, kind, part) VALUES (?, ?, ?)", (title, kind, part))


async def get_exam(exam_id: int):
    return await _fetchone("SELECT * FROM exams WHERE id = ?", (exam_id,))


async def list_exams():
    return await _fetchall(
        """SELECT e.*, (SELECT COUNT(*) FROM questions q WHERE q.exam_id = e.id) AS q_count
           FROM exams e ORDER BY COALESCE(e.kind, 'speaking'), e.part, e.id DESC"""
    )


async def active_part_counts(kind: str) -> dict:
    """{part: nechta ochiq imtihon} - o'quvchiga faqat ichida imtihon bor turlar ko'rsatiladi."""
    rows = await _fetchall(
        """SELECT e.part, COUNT(*) AS cnt FROM exams e
           WHERE e.is_active = 1 AND COALESCE(e.kind, 'speaking') = ?
             AND EXISTS (SELECT 1 FROM questions q WHERE q.exam_id = e.id)
           GROUP BY e.part""",
        (kind,),
    )
    return {r["part"]: r["cnt"] for r in rows}


async def list_active_exams(kind: str, part: str | None):
    part_sql = "e.part = ?" if part else "e.part IS NULL"
    params = (kind, part) if part else (kind,)
    return await _fetchall(
        f"""SELECT e.* FROM exams e
            WHERE e.is_active = 1 AND COALESCE(e.kind, 'speaking') = ? AND {part_sql}
              AND EXISTS (SELECT 1 FROM questions q WHERE q.exam_id = e.id)
            ORDER BY e.id""",
        params,
    )


async def set_exam_active(exam_id: int, active: bool):
    await _execute("UPDATE exams SET is_active = ? WHERE id = ?", (int(active), exam_id))


async def delete_exam(exam_id: int):
    await _execute("DELETE FROM questions WHERE exam_id = ?", (exam_id,))
    await _execute("DELETE FROM exams WHERE id = ?", (exam_id,))


# ---------- Savollar ----------

async def add_question(exam_id: int, text: str, photo_file_id: str | None, prep_sec: int, answer_sec: int) -> int:
    row = await _fetchone("SELECT COALESCE(MAX(position), 0) AS m FROM questions WHERE exam_id = ?", (exam_id,))
    return await _execute(
        """INSERT INTO questions (exam_id, position, text, photo_file_id, prep_sec, answer_sec)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (exam_id, row["m"] + 1, text, photo_file_id, prep_sec, answer_sec),
    )


async def get_questions(exam_id: int):
    return await _fetchall("SELECT * FROM questions WHERE exam_id = ? ORDER BY position", (exam_id,))


async def get_question(question_id: int):
    return await _fetchone("SELECT * FROM questions WHERE id = ?", (question_id,))


async def delete_question(question_id: int):
    await _execute("DELETE FROM questions WHERE id = ?", (question_id,))


async def set_writing_task(exam_id: int, text: str, photo_file_id: str | None = None):
    """Writing mavzusida bitta topshiriq matni bo'ladi - eskisi almashtiriladi."""
    await _execute("DELETE FROM questions WHERE exam_id = ?", (exam_id,))
    await add_question(exam_id, text, photo_file_id, 0, 0)


# ---------- Speaking urinishlari ----------

async def create_attempt(telegram_id: int, exam_id: int) -> int:
    return await _execute("INSERT INTO attempts (telegram_id, exam_id) VALUES (?, ?)", (telegram_id, exam_id))


async def get_attempt(attempt_id: int):
    return await _fetchone("SELECT * FROM attempts WHERE id = ?", (attempt_id,))


async def save_answer(attempt_id: int, question_id: int, transcript: str, duration_sec: float = 0):
    await _execute(
        """INSERT INTO answers (attempt_id, question_id, transcript, duration_sec) VALUES (?, ?, ?, ?)
           ON CONFLICT(attempt_id, question_id) DO UPDATE SET
               transcript = excluded.transcript, duration_sec = excluded.duration_sec""",
        (attempt_id, question_id, transcript, duration_sec),
    )


async def get_answers(attempt_id: int):
    return await _fetchall(
        """SELECT a.transcript, a.duration_sec, q.answer_sec, q.text AS question, q.position,
                  q.photo_file_id IS NOT NULL AS has_photo
           FROM answers a JOIN questions q ON q.id = a.question_id
           WHERE a.attempt_id = ? ORDER BY q.position""",
        (attempt_id,),
    )


async def finish_attempt(attempt_id: int, score: int, level: str, result: dict, raw: float | None = None):
    await _execute(
        "UPDATE attempts SET status = 'done', score = ?, raw_score = ?, level = ?, result_json = ? WHERE id = ?",
        (score, raw, level, json.dumps(result, ensure_ascii=False), attempt_id),
    )


# ---------- Writing ishlari ----------

async def create_writing_submission(telegram_id: int, exam_id: int, text: str, words: int) -> int:
    return await _execute(
        "INSERT INTO writing_submissions (telegram_id, exam_id, text, words) VALUES (?, ?, ?, ?)",
        (telegram_id, exam_id, text, words),
    )


async def finish_writing_submission(submission_id: int, score: int, level: str, raw: float, result: dict):
    await _execute(
        """UPDATE writing_submissions SET status = 'done', score = ?, raw_score = ?, level = ?, result_json = ?
           WHERE id = ?""",
        (score, raw, level, json.dumps(result, ensure_ascii=False), submission_id),
    )


# ---------- Natijalar (admin uchun) ----------

async def recent_results(limit: int = 20):
    return await _fetchall(
        """SELECT * FROM (
               SELECT 'speaking' AS kind, a.id, a.score, a.level, a.result_json, a.created_at, u.full_name, e.title, e.part
               FROM attempts a
               LEFT JOIN users u ON u.telegram_id = a.telegram_id
               LEFT JOIN exams e ON e.id = a.exam_id
               WHERE a.status = 'done'
               UNION ALL
               SELECT 'writing' AS kind, w.id, w.score, w.level, w.result_json, w.created_at, u.full_name, e.title, e.part
               FROM writing_submissions w
               LEFT JOIN users u ON u.telegram_id = w.telegram_id
               LEFT JOIN exams e ON e.id = w.exam_id
               WHERE w.status = 'done'
           ) ORDER BY created_at DESC, id DESC LIMIT ?""",
        (limit,),
    )


# ---------- Adminlar ----------

async def load_admins():
    """Bazadagi adminlarni xotiraga yuklaydi (config.is_admin ularni ko'radi)."""
    rows = await _fetchall("SELECT telegram_id FROM admins")
    config.EXTRA_ADMIN_IDS = {r["telegram_id"] for r in rows}


async def list_admins():
    return await _fetchall(
        """SELECT a.telegram_id, a.added_at, u.full_name, u.username FROM admins a
           LEFT JOIN users u ON u.telegram_id = a.telegram_id ORDER BY a.added_at"""
    )


async def add_admin(telegram_id: int, added_by: int):
    await _execute("INSERT OR IGNORE INTO admins (telegram_id, added_by) VALUES (?, ?)", (telegram_id, added_by))
    await load_admins()


async def remove_admin(telegram_id: int):
    await _execute("DELETE FROM admins WHERE telegram_id = ?", (telegram_id,))
    await load_admins()


async def find_user(query: str):
    """Telegram ID (raqam) yoki @username bo'yicha foydalanuvchini topadi."""
    q = (query or "").strip()
    if q.lstrip("-").isdigit():
        return await get_user(int(q)) or {"telegram_id": int(q), "full_name": None, "username": None}
    q = q.lstrip("@")
    if not q:
        return None
    return await _fetchone("SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (q,))


# ---------- Premium ----------

_FMT = "%Y-%m-%d %H:%M:%S"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def get_premium_until(telegram_id: int) -> str | None:
    """Premium hali tugamagan bo'lsa - tugash vaqti (UTC, matn), aks holda None."""
    row = await _fetchone("SELECT until FROM premium WHERE telegram_id = ?", (telegram_id,))
    if row and row["until"] > _now().strftime(_FMT):
        return row["until"]
    return None


async def grant_premium(telegram_id: int, days: int, amount: str, granted_by: int) -> str:
    """Premiumni `days` kunga uzaytiradi (faol bo'lsa - tugash sanasiga qo'shiladi). Yangi tugash vaqtini qaytaradi."""
    current = await get_premium_until(telegram_id)
    base = datetime.strptime(current, _FMT) if current else _now()
    until = (base + timedelta(days=days)).strftime(_FMT)
    await _execute(
        """INSERT INTO premium (telegram_id, until) VALUES (?, ?)
           ON CONFLICT(telegram_id) DO UPDATE SET until = excluded.until, updated_at = datetime('now')""",
        (telegram_id, until),
    )
    await _execute(
        "INSERT INTO premium_log (telegram_id, action, days, amount, granted_by) VALUES (?, 'grant', ?, ?, ?)",
        (telegram_id, days, amount, granted_by),
    )
    return until


async def revoke_premium(telegram_id: int, revoked_by: int):
    await _execute("DELETE FROM premium WHERE telegram_id = ?", (telegram_id,))
    await _execute(
        "INSERT INTO premium_log (telegram_id, action, granted_by) VALUES (?, 'revoke', ?)", (telegram_id, revoked_by)
    )


async def list_active_premium(limit: int = 15):
    return await _fetchall(
        """SELECT p.telegram_id, p.until, u.full_name, u.username FROM premium p
           LEFT JOIN users u ON u.telegram_id = p.telegram_id
           WHERE p.until > ? ORDER BY p.until LIMIT ?""",
        (_now().strftime(_FMT), limit),
    )


async def count_active_premium() -> int:
    row = await _fetchone("SELECT COUNT(*) AS c FROM premium WHERE until > ?", (_now().strftime(_FMT),))
    return row["c"]


async def count_checks_month(telegram_id: int) -> int:
    """Shu oy (O'zbekiston vaqti, UTC+5) yakunlangan speaking + writing tekshiruvlari soni."""
    row = await _fetchone(
        """SELECT
             (SELECT COUNT(*) FROM attempts WHERE telegram_id = ?1 AND status = 'done'
                AND strftime('%Y-%m', created_at, '+5 hours') = strftime('%Y-%m', 'now', '+5 hours'))
           + (SELECT COUNT(*) FROM writing_submissions WHERE telegram_id = ?1 AND status = 'done'
                AND strftime('%Y-%m', created_at, '+5 hours') = strftime('%Y-%m', 'now', '+5 hours')) AS c""",
        (telegram_id,),
    )
    return row["c"]


async def get_stats() -> dict:
    row = await _fetchone(
        """SELECT
             (SELECT COUNT(*) FROM users) AS users,
             (SELECT COUNT(*) FROM users WHERE date(joined_at, '+5 hours') = date('now', '+5 hours')) AS new_today,
             (SELECT COUNT(*) FROM attempts WHERE status = 'done') AS speaking,
             (SELECT COUNT(*) FROM writing_submissions WHERE status = 'done') AS writing,
             (SELECT COUNT(*) FROM attempts WHERE status = 'done'
                AND date(created_at, '+5 hours') = date('now', '+5 hours'))
           + (SELECT COUNT(*) FROM writing_submissions WHERE status = 'done'
                AND date(created_at, '+5 hours') = date('now', '+5 hours')) AS today"""
    )
    row["premium"] = await count_active_premium()
    return row


# ---------- Sozlamalar ----------

async def get_setting(key: str, default: str = "") -> str:
    row = await _fetchone("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row and row["value"] else default


async def set_setting(key: str, value: str):
    await _execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# ---------- Foydalanuvchilar ro'yxati (admin uchun) ----------

async def count_users() -> int:
    return (await _fetchone("SELECT COUNT(*) AS c FROM users"))["c"]


async def list_users(offset: int = 0, limit: int = 8):
    """Eng yangi qo'shilganlar birinchi; premium holati bilan."""
    return await _fetchall(
        """SELECT u.telegram_id, u.full_name, u.username, u.joined_at,
                  (p.until IS NOT NULL AND p.until > ?) AS is_premium
           FROM users u LEFT JOIN premium p ON p.telegram_id = u.telegram_id
           ORDER BY u.joined_at DESC, u.telegram_id DESC LIMIT ? OFFSET ?""",
        (_now().strftime(_FMT), limit, offset),
    )


# ---------- Hamma testlarni o'chirish ----------

async def delete_all_exams() -> int:
    """Barcha imtihon/mavzularni va ularning savollarini o'chiradi (natijalar tarixi saqlanadi). Soni qaytadi."""
    n = (await _fetchone("SELECT COUNT(*) AS c FROM exams"))["c"]
    await _execute("DELETE FROM questions")
    await _execute("DELETE FROM exams")
    return n
