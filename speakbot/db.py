"""SQLite baza: imtihonlar (speaking/writing), savollar, urinishlar, javoblar, writing ishlari."""
import json
import os
import time
from datetime import datetime, timedelta, timezone

import aiosqlite

import config
from config import DB_PATH, TURSO_TOKEN, TURSO_URL
from turso import TursoClient, TursoError

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
    answer_sec INTEGER DEFAULT 60,
    meta TEXT
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
    plan TEXT DEFAULT 'pro',
    reminded_for TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS premium_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    plan TEXT,
    days INTEGER,
    amount TEXT,
    granted_by INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS bot_lock (
    id INTEGER PRIMARY KEY,
    instance TEXT,
    service TEXT,
    heartbeat TEXT
);
CREATE TABLE IF NOT EXISTS fsm_state (
    key TEXT PRIMARY KEY,
    state TEXT,
    data TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
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
    "ALTER TABLE premium ADD COLUMN plan TEXT DEFAULT 'pro'",
    "ALTER TABLE premium ADD COLUMN reminded_for TEXT",
    "ALTER TABLE premium_log ADD COLUMN plan TEXT",
    "ALTER TABLE users ADD COLUMN blocked INTEGER DEFAULT 0",
    "ALTER TABLE questions ADD COLUMN meta TEXT",  # writing topshirig'ining maydonlari (JSON)
    "ALTER TABLE users ADD COLUMN menu_ver INTEGER DEFAULT 0",  # foydalanuvchidagi pastki menyu versiyasi
]


# Odam o'qiy oladigan ko'rinishlar (VIEW): Turso panelida (yoki SQLite'da) shu nomlar bilan ochiladi -
# ism, test nomi, ball va Toshkent vaqti bilan. Jadvallarning o'zi o'zgarmaydi; har ishga tushishda yangilanadi.
VIEWS = {
    "v_foydalanuvchilar": """
SELECT u.telegram_id,
       u.full_name AS ism,
       CASE WHEN u.username IS NOT NULL AND u.username != '' THEN '@' || u.username END AS username,
       datetime(u.joined_at, '+5 hours') AS qoshilgan_vaqt,
       CASE WHEN p.until > datetime('now') THEN COALESCE(p.plan, 'pro') ELSE 'free' END AS tarif,
       CASE WHEN p.until > datetime('now') THEN datetime(p.until, '+5 hours') END AS tarif_tugashi,
       CASE WHEN COALESCE(u.blocked, 0) = 1 THEN 'ha' ELSE 'yoq' END AS bloklangan,
       (SELECT COUNT(*) FROM attempts a WHERE a.telegram_id = u.telegram_id AND a.status = 'done') AS konusma_tekshiruvlari,
       (SELECT COUNT(*) FROM writing_submissions w WHERE w.telegram_id = u.telegram_id AND w.status = 'done') AS yazma_tekshiruvlari
FROM users u LEFT JOIN premium p ON p.telegram_id = u.telegram_id
ORDER BY u.joined_at DESC""",
    "v_natijalar": """
SELECT * FROM (
    SELECT 'Konuşma' AS bolim, datetime(a.created_at, '+5 hours') AS vaqt, u.full_name AS oquvchi,
           a.telegram_id, e.title AS test, e.part AS qism, a.raw_score AS ekspert_bahosi,
           json_extract(a.result_json, '$.max_raw') AS maksimal_ball, a.score AS standart_ball_75, a.level AS daraja,
           a.id AS urinish_id
    FROM attempts a LEFT JOIN users u ON u.telegram_id = a.telegram_id LEFT JOIN exams e ON e.id = a.exam_id
    WHERE a.status = 'done'
    UNION ALL
    SELECT 'Yazma', datetime(w.created_at, '+5 hours'), u.full_name, w.telegram_id, e.title, e.part, w.raw_score,
           json_extract(w.result_json, '$.max_raw'), w.score, w.level, w.id
    FROM writing_submissions w LEFT JOIN users u ON u.telegram_id = w.telegram_id LEFT JOIN exams e ON e.id = w.exam_id
    WHERE w.status = 'done'
) ORDER BY vaqt DESC""",
    "v_yazma_ishlari": """
SELECT datetime(w.created_at, '+5 hours') AS vaqt, u.full_name AS oquvchi, w.telegram_id, e.title AS mavzu,
       e.part AS qism, w.words AS sozlar_soni, w.raw_score AS ekspert_bahosi, w.score AS standart_ball_75,
       w.level AS daraja, CASE w.status WHEN 'done' THEN 'tekshirildi' ELSE 'tekshirilmadi' END AS holat,
       w.text AS matn
FROM writing_submissions w LEFT JOIN users u ON u.telegram_id = w.telegram_id LEFT JOIN exams e ON e.id = w.exam_id
ORDER BY w.id DESC""",
    "v_testlar": """
SELECT e.id, e.title AS nomi,
       CASE COALESCE(e.kind, 'speaking') WHEN 'writing' THEN 'Yazma' ELSE 'Konuşma' END AS bolim,
       e.part AS qism, CASE WHEN e.is_active = 1 THEN 'ochiq' ELSE 'yopiq' END AS holat,
       (SELECT COUNT(*) FROM questions q WHERE q.exam_id = e.id) AS savollar_soni,
       datetime(e.created_at, '+5 hours') AS yaratilgan
FROM exams e ORDER BY e.id DESC""",
    "v_savollar": """
SELECT e.title AS test, CASE COALESCE(e.kind, 'speaking') WHEN 'writing' THEN 'Yazma' ELSE 'Konuşma' END AS bolim,
       e.part AS qism, q.position AS tartib, q.text AS matn,
       CASE WHEN q.photo_file_id IS NOT NULL THEN 'bor' ELSE 'yoq' END AS rasm,
       q.prep_sec AS tayyorlanish_s, q.answer_sec AS javob_s, q.id AS savol_id
FROM questions q LEFT JOIN exams e ON e.id = q.exam_id
ORDER BY q.exam_id, q.position""",
    "v_premium_tarixi": """
SELECT datetime(l.created_at, '+5 hours') AS vaqt, u.full_name AS foydalanuvchi, l.telegram_id,
       CASE l.action WHEN 'grant' THEN 'berildi' WHEN 'revoke' THEN 'bekor qilindi' ELSE l.action END AS amal,
       l.plan AS tarif, l.days AS kun, l.amount AS tolov_izohi, a.full_name AS admin
FROM premium_log l LEFT JOIN users u ON u.telegram_id = l.telegram_id LEFT JOIN users a ON a.telegram_id = l.granted_by
ORDER BY l.id DESC""",
}


_db: aiosqlite.Connection | None = None
_settings: dict[str, str] = {}

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_questions_exam ON questions(exam_id, position)",
    "CREATE INDEX IF NOT EXISTS ix_exams_active ON exams(is_active, kind, part)",
    "CREATE INDEX IF NOT EXISTS ix_attempts_user ON attempts(telegram_id, status, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_attempts_status ON attempts(status, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_answers_attempt ON answers(attempt_id)",
    "CREATE INDEX IF NOT EXISTS ix_writing_user ON writing_submissions(telegram_id, status, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_writing_status ON writing_submissions(status, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_premium_until ON premium(until)",
    "CREATE INDEX IF NOT EXISTS ix_users_joined ON users(joined_at)",
    "CREATE INDEX IF NOT EXISTS ix_log_user ON premium_log(telegram_id, created_at)",
]


async def _get() -> aiosqlite.Connection:
    """Bitta doimiy ulanish (har so'rovda yangisini ochish juda sekin edi) + WAL rejimi."""
    global _db
    if _db is None:
        parent = os.path.dirname(os.path.abspath(DB_PATH))
        os.makedirs(parent, exist_ok=True)
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA synchronous=NORMAL")
        await _db.execute("PRAGMA busy_timeout=5000")
        await _db.execute("PRAGMA temp_store=MEMORY")
    return _db


# Turso sozlangan bo'lsa - ma'lumotlar Turso'da (Render qayta ishga tushganda ham saqlanadi),
# aks holda lokal SQLite fayl.
_turso: TursoClient | None = TursoClient(TURSO_URL, TURSO_TOKEN) if TURSO_URL else None


def backend_name() -> str:
    return "Turso" if _turso else f"SQLite ({DB_PATH})"


async def close_db():
    global _db
    if _db is not None:
        await _db.close()
        _db = None


# «Yopish / ochish» tugmasi olib tashlandi - avval yopiq qolgan testlar ham o'quvchilarga ko'rinsin
_ALL_OPEN = "UPDATE exams SET is_active = 1 WHERE is_active IS NULL OR is_active != 1"


async def init_db():
    if _turso:
        statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]
        await _turso.batch([(sql, ()) for sql in statements])
        for sql in _MIGRATIONS:
            try:
                await _turso.execute(sql)
            except TursoError:
                pass  # ustun allaqachon bor
        await _turso.batch(
            [("UPDATE exams SET part = '1' WHERE kind = 'writing' AND part IN ('1.1', '1.2')", ()), (_ALL_OPEN, ())]
            + [(sql, ()) for sql in _INDEXES]
            + [(sql, ()) for sql in _view_statements()]
        )
        await load_settings()
        return
    db = await _get()
    await db.executescript(SCHEMA)
    for sql in _MIGRATIONS:
        try:
            await db.execute(sql)
        except aiosqlite.OperationalError:
            pass
    # Yangi format: writing'da 1.1 va 1.2 alohida emas - ikkalasi "1-qism"
    await db.execute("UPDATE exams SET part = '1' WHERE kind = 'writing' AND part IN ('1.1', '1.2')")
    await db.execute(_ALL_OPEN)
    for sql in _INDEXES:
        await db.execute(sql)
    for sql in _view_statements():
        await db.execute(sql)
    await db.commit()
    await load_settings()


def _view_statements() -> list[str]:
    out = []
    for name, body in VIEWS.items():
        out += [f"DROP VIEW IF EXISTS {name}", f"CREATE VIEW {name} AS {body.strip()}"]
    return out


async def _fetchall(sql, params=()):
    if _turso:
        return (await _turso.execute(sql, params)).rows
    db = await _get()
    async with db.execute(sql, params) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def _fetchone(sql, params=()):
    rows = await _fetchall(sql, params)
    return rows[0] if rows else None


async def _execute(sql, params=()) -> int:
    if _turso:
        return (await _turso.execute(sql, params)).lastrowid
    db = await _get()
    cur = await db.execute(sql, params)
    await db.commit()
    return cur.lastrowid


# ---------- Foydalanuvchilar ----------

_saved_users: dict[int, tuple] = {}  # oxirgi yozilgan ism/username - o'zgarmagan bo'lsa bazaga qayta borilmaydi


async def _execute_many(statements: list[tuple[str, tuple]]):
    """Bir nechta yozuv - Turso'ga BITTA so'rovda (har so'rov ~0.1-0.3 s)."""
    if _turso:
        await _turso.batch(statements)
        return
    db = await _get()
    for sql, params in statements:
        await db.execute(sql, params)
    await db.commit()


async def save_user(telegram_id: int, full_name: str, username: str | None):
    now = time.time()
    last = _saved_users.get(telegram_id)
    if last and last[:2] == (full_name, username) and now - last[2] < 1800:
        return
    statements = []
    if username:  # Telegram'da username boshqa odamga o'tgan bo'lishi mumkin - qidiruv adashmasin
        statements.append(("UPDATE users SET username = NULL WHERE LOWER(username) = LOWER(?) AND telegram_id != ?",
                           (username, telegram_id)))
    statements.append((
        """INSERT INTO users (telegram_id, full_name, username) VALUES (?, ?, ?)
           ON CONFLICT(telegram_id) DO UPDATE SET full_name = excluded.full_name, username = excluded.username""",
        (telegram_id, full_name, username),
    ))
    await _execute_many(statements)
    _saved_users[telegram_id] = (full_name, username, now)


async def ping() -> float:
    """Bazaga bitta so'rov necha soniyada borib kelishi (/status uchun)."""
    t = time.perf_counter()
    await _fetchone("SELECT 1 AS x")
    return time.perf_counter() - t


async def get_menu_version(telegram_id: int) -> int:
    row = await _fetchone("SELECT menu_ver FROM users WHERE telegram_id = ?", (telegram_id,))
    return int((row or {}).get("menu_ver") or 0)


async def set_menu_version(telegram_id: int, version: int):
    await _execute("UPDATE users SET menu_ver = ? WHERE telegram_id = ?", (version, telegram_id))


async def get_user(telegram_id: int):
    return await _fetchone("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))


# ---------- Imtihonlar (speaking to'plamlari va writing mavzulari) ----------

async def create_exam(title: str, kind: str = "speaking", part: str | None = None) -> int:
    # Testlar doim ochiq: o'quvchilar savoli (yazmada - to'liq topshirig'i) bor har qanday testni ko'radi.
    return await _execute("INSERT INTO exams (title, kind, part, is_active) VALUES (?, ?, ?, 1)", (title, kind, part))


async def get_exam(exam_id: int):
    return await _fetchone("SELECT * FROM exams WHERE id = ?", (exam_id,))


async def list_exams():
    return await _fetchall(
        """SELECT e.*, (SELECT COUNT(*) FROM questions q WHERE q.exam_id = e.id) AS q_count,
                  (SELECT q.text FROM questions q WHERE q.exam_id = e.id ORDER BY q.position, q.id LIMIT 1) AS task_text,
                  (SELECT q.meta FROM questions q WHERE q.exam_id = e.id ORDER BY q.position, q.id LIMIT 1) AS task_meta
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


async def active_exams_with_task(kind: str):
    """Barcha ochiq (savoli bor) testlar va ularning birinchi savoli / topshirig'i - bitta so'rovda
    (Turso'da har bir so'rov alohida tarmoq murojaati - menyular tez ochilishi uchun)."""
    return await _fetchall(
        """SELECT e.*, q.text AS task_text, q.meta AS task_meta
           FROM exams e
           JOIN questions q ON q.id = (SELECT q2.id FROM questions q2 WHERE q2.exam_id = e.id
                                       ORDER BY q2.position, q2.id LIMIT 1)
           WHERE e.is_active = 1 AND COALESCE(e.kind, 'speaking') = ?
           ORDER BY e.id""",
        (kind,),
    )


async def set_exam_active(exam_id: int, active: bool):
    await _execute("UPDATE exams SET is_active = ? WHERE id = ?", (int(active), exam_id))


async def delete_exam(exam_id: int):
    await _execute("DELETE FROM questions WHERE exam_id = ?", (exam_id,))
    await _execute("DELETE FROM exams WHERE id = ?", (exam_id,))


# ---------- Savollar ----------

async def add_question(exam_id: int, text: str, photo_file_id: str | None, prep_sec: int, answer_sec: int,
                       meta: str | None = None) -> int:
    row = await _fetchone("SELECT COALESCE(MAX(position), 0) AS m FROM questions WHERE exam_id = ?", (exam_id,))
    return await _execute(
        """INSERT INTO questions (exam_id, position, text, photo_file_id, prep_sec, answer_sec, meta)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (exam_id, row["m"] + 1, text, photo_file_id, prep_sec, answer_sec, meta),
    )


async def get_questions(exam_id: int):
    return await _fetchall("SELECT * FROM questions WHERE exam_id = ? ORDER BY position", (exam_id,))


async def get_question(question_id: int):
    return await _fetchone("SELECT * FROM questions WHERE id = ?", (question_id,))


async def delete_question(question_id: int):
    await _execute("DELETE FROM questions WHERE id = ?", (question_id,))


_QUESTION_FIELDS = {"text", "photo_file_id", "prep_sec", "answer_sec"}


async def update_question(question_id: int, **fields):
    """Savolning matni / rasmi / vaqtlarini o'zgartirish (faqat berilgan maydonlar)."""
    cols = [c for c in fields if c in _QUESTION_FIELDS]
    if cols:
        await _execute(f"UPDATE questions SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                       tuple(fields[c] for c in cols) + (question_id,))


async def set_exam_times(exam_id: int, prep_sec: int | None = None, answer_sec: int | None = None):
    """Testdagi BARCHA savollar uchun bir xil vaqt."""
    if prep_sec is not None:
        await _execute("UPDATE questions SET prep_sec = ? WHERE exam_id = ?", (prep_sec, exam_id))
    if answer_sec is not None:
        await _execute("UPDATE questions SET answer_sec = ? WHERE exam_id = ?", (answer_sec, exam_id))


async def move_question(question_id: int, delta: int) -> bool:
    """Savolni tartibda bir pog'ona yuqoriga (-1) yoki pastga (+1) surish."""
    q = await get_question(question_id)
    if not q:
        return False
    qs = await get_questions(q["exam_id"])
    i = next(i for i, x in enumerate(qs) if x["id"] == question_id)
    j = i + delta
    if not 0 <= j < len(qs):
        return False
    order = [x["id"] for x in qs]
    order[i], order[j] = order[j], order[i]
    await _execute_many([("UPDATE questions SET position = ? WHERE id = ?", (n, qid)) for n, qid in enumerate(order, 1)])
    return True


async def get_writing_task(exam_id: int):
    """Writing mavzusining topshirig'i (bitta qator) yoki None."""
    return await _fetchone("SELECT * FROM questions WHERE exam_id = ? ORDER BY position, id LIMIT 1", (exam_id,))


async def save_writing_task(exam_id: int, text: str, meta: str | None):
    """Writing mavzusida bitta topshiriq bo'ladi: bor bo'lsa - matni yangilanadi (rasm saqlanib qoladi)."""
    row = await get_writing_task(exam_id)
    if not row:
        await add_question(exam_id, text, None, 0, 0, meta)
        return
    await _execute("UPDATE questions SET text = ?, meta = ? WHERE id = ?", (text, meta, row["id"]))
    await _execute("DELETE FROM questions WHERE exam_id = ? AND id != ?", (exam_id, row["id"]))


async def set_writing_photo(exam_id: int, photo_file_id: str | None):
    if not await get_writing_task(exam_id):
        await add_question(exam_id, "", None, 0, 0, "{}")
    await _execute("UPDATE questions SET photo_file_id = ? WHERE exam_id = ?", (photo_file_id, exam_id))


# ---------- Speaking urinishlari ----------

async def create_attempt(telegram_id: int, exam_id: int) -> int:
    return await _execute("INSERT INTO attempts (telegram_id, exam_id) VALUES (?, ?)", (telegram_id, exam_id))


async def reusable_attempt(telegram_id: int, exam_id: int, hours: int = 6) -> int | None:
    """Shu o'quvchining shu imtihondagi yaqinda boshlangan, tugallanmagan urinishi (javoblari qayta yoziladi)."""
    row = await _fetchone(
        """SELECT id FROM attempts WHERE telegram_id = ? AND exam_id = ? AND status = 'in_progress'
             AND created_at >= datetime('now', ?) ORDER BY id DESC LIMIT 1""",
        (telegram_id, exam_id, f"-{int(hours)} hours"),
    )
    return row["id"] if row else None


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
    # aynan shu ish oldin tekshirilmay qolgan bo'lsa (AI xatosi) - o'sha qator qayta ishlatiladi, nusxa ko'paymaydi
    row = await _fetchone(
        "SELECT id FROM writing_submissions WHERE telegram_id = ? AND exam_id = ? AND text = ? AND status = 'pending' "
        "ORDER BY id DESC LIMIT 1",
        (telegram_id, exam_id, text),
    )
    if row:
        return row["id"]
    return await _execute(
        "INSERT INTO writing_submissions (telegram_id, exam_id, text, words) VALUES (?, ?, ?, ?)",
        (telegram_id, exam_id, text, words),
    )


async def find_recent_writing(telegram_id: int, exam_id: int, text: str, minutes: int = 30):
    """Aynan shu matn yaqinda tekshirilganmi (qayta yuborilganda yangidan baholamaslik uchun)."""
    return await _fetchone(
        """SELECT * FROM writing_submissions
           WHERE telegram_id = ? AND exam_id = ? AND text = ? AND status = 'done'
             AND created_at >= datetime('now', ?)
           ORDER BY id DESC LIMIT 1""",
        (telegram_id, exam_id, text, f"-{int(minutes)} minutes"),
    )


async def finish_writing_submission(submission_id: int, score: int, level: str, raw: float, result: dict):
    await _execute(
        """UPDATE writing_submissions SET status = 'done', score = ?, raw_score = ?, level = ?, result_json = ?
           WHERE id = ?""",
        (score, raw, level, json.dumps(result, ensure_ascii=False), submission_id),
    )


# ---------- Natijalar (admin uchun) ----------

async def recent_results(limit: int = 20, offset: int = 0):
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
           ) ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?""",
        (limit, offset),
    )


async def results_report(days: int | None = None):
    """PDF hisobot uchun: barcha tugallangan natijalar (o'quvchi ma'lumoti bilan), eng yangisi oldin."""
    since = f"AND {{t}}.created_at >= datetime('now', '-{int(days)} days')" if days else ""
    return await _fetchall(
        f"""SELECT * FROM (
               SELECT 'speaking' AS kind, a.id, a.telegram_id, a.score, a.level, a.result_json, a.created_at,
                      u.full_name, u.username, e.title, e.part
               FROM attempts a
               LEFT JOIN users u ON u.telegram_id = a.telegram_id
               LEFT JOIN exams e ON e.id = a.exam_id
               WHERE a.status = 'done' {since.format(t='a')}
               UNION ALL
               SELECT 'writing' AS kind, w.id, w.telegram_id, w.score, w.level, w.result_json, w.created_at,
                      u.full_name, u.username, e.title, e.part
               FROM writing_submissions w
               LEFT JOIN users u ON u.telegram_id = w.telegram_id
               LEFT JOIN exams e ON e.id = w.exam_id
               WHERE w.status = 'done' {since.format(t='w')}
           ) ORDER BY created_at DESC, id DESC"""
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
    """Telegram ID (raqam), @username, t.me havola yoki ism bo'yicha foydalanuvchini topadi.
    "known" - odam botda bormi (ID bo'yicha topilmasa ham ID'ning o'zi qaytariladi, known=False)."""
    q = (query or "").strip()
    if q.lstrip("-").isdigit():
        user = await get_user(int(q))
        return {**user, "known": True} if user else {"telegram_id": int(q), "full_name": None, "username": None, "known": False}
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if q.lower().startswith(prefix):
            q = q[len(prefix):]
    q = q.strip().lstrip("@").strip("/")
    if not q:
        return None
    user = await _fetchone("SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (q,))
    if not user:  # ism bo'yicha (kirill, turkcha harflar ham) - faqat bitta odam mos kelsa
        needle = q.casefold()
        same = [u for u in await _fetchall("SELECT * FROM users WHERE full_name IS NOT NULL")
                if needle in u["full_name"].casefold()]
        user = same[0] if len(same) == 1 else None
    return {**user, "known": True} if user else None


# ---------- Premium ----------

_FMT = "%Y-%m-%d %H:%M:%S"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def get_premium(telegram_id: int) -> dict | None:
    """Faol premium: {"until": ..., "plan": "standard" | "pro"} yoki None (tugagan / yo'q)."""
    row = await _fetchone("SELECT until, plan FROM premium WHERE telegram_id = ?", (telegram_id,))
    if row and row["until"] > _now().strftime(_FMT):
        return {"until": row["until"], "plan": row["plan"] or "pro"}
    return None


async def get_premium_until(telegram_id: int) -> str | None:
    p = await get_premium(telegram_id)
    return p["until"] if p else None


async def grant_premium(telegram_id: int, days: int, amount: str, granted_by: int, plan: str = "pro") -> str:
    """Premiumni `days` kunga uzaytiradi (faol bo'lsa - tugash sanasiga qo'shiladi), tarifni `plan` ga o'rnatadi.
    Yangi tugash vaqtini qaytaradi."""
    current = await get_premium_until(telegram_id)
    base = datetime.strptime(current, _FMT) if current else _now()
    until = (base + timedelta(days=days)).strftime(_FMT)
    await _execute(
        """INSERT INTO premium (telegram_id, until, plan, reminded_for) VALUES (?, ?, ?, NULL)
           ON CONFLICT(telegram_id) DO UPDATE SET until = excluded.until, plan = excluded.plan,
               reminded_for = NULL, updated_at = datetime('now')""",
        (telegram_id, until, plan),
    )
    await _execute(
        "INSERT INTO premium_log (telegram_id, action, plan, days, amount, granted_by) VALUES (?, 'grant', ?, ?, ?, ?)",
        (telegram_id, plan, days, amount, granted_by),
    )
    return until


async def revoke_premium(telegram_id: int, revoked_by: int):
    await _execute("DELETE FROM premium WHERE telegram_id = ?", (telegram_id,))
    await _execute(
        "INSERT INTO premium_log (telegram_id, action, granted_by) VALUES (?, 'revoke', ?)", (telegram_id, revoked_by)
    )


async def list_active_premium(limit: int = 15, plan: str | None = None):
    sql = """SELECT p.telegram_id, p.until, p.plan, u.full_name, u.username FROM premium p
             LEFT JOIN users u ON u.telegram_id = p.telegram_id
             WHERE p.until > ?"""
    params: list = [_now().strftime(_FMT)]
    if plan:
        sql += " AND p.plan = ?"
        params.append(plan)
    sql += " ORDER BY p.until LIMIT ?"
    params.append(limit)
    return await _fetchall(sql, tuple(params))


async def list_expiring_premium(days: int = 7, limit: int = 15):
    """Yaqin kunlarda tugaydigan faol obunalar."""
    now = _now()
    return await _fetchall(
        """SELECT p.telegram_id, p.until, p.plan, u.full_name, u.username FROM premium p
           LEFT JOIN users u ON u.telegram_id = p.telegram_id
           WHERE p.until > ? AND p.until <= ? ORDER BY p.until LIMIT ?""",
        (now.strftime(_FMT), (now + timedelta(days=days)).strftime(_FMT), limit),
    )


async def due_reminders(days: int = 3):
    """Tugashiga `days` kun qolgan va hali eslatma yuborilmagan obunalar."""
    now = _now()
    return await _fetchall(
        """SELECT telegram_id, until, plan FROM premium
           WHERE until > ? AND until <= ? AND COALESCE(reminded_for, '') != until""",
        (now.strftime(_FMT), (now + timedelta(days=days)).strftime(_FMT)),
    )


async def mark_reminded(telegram_id: int, until: str):
    await _execute("UPDATE premium SET reminded_for = ? WHERE telegram_id = ?", (until, telegram_id))


async def count_active_premium(plan: str | None = None) -> int:
    sql, params = "SELECT COUNT(*) AS c FROM premium WHERE until > ?", [_now().strftime(_FMT)]
    if plan:
        sql += " AND plan = ?"
        params.append(plan)
    return (await _fetchone(sql, tuple(params)))["c"]


async def premium_history(telegram_id: int, limit: int = 5):
    return await _fetchall(
        "SELECT action, plan, days, amount, created_at FROM premium_log WHERE telegram_id = ? ORDER BY id DESC LIMIT ?",
        (telegram_id, limit),
    )


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
             (SELECT COUNT(*) FROM users WHERE full_name IS NOT NULL) AS users,
             (SELECT COUNT(*) FROM users WHERE full_name IS NOT NULL
                AND date(joined_at, '+5 hours') = date('now', '+5 hours')) AS new_today,
             (SELECT COUNT(*) FROM users WHERE full_name IS NOT NULL AND joined_at >= datetime('now', '-7 days')) AS new_week,
             (SELECT COUNT(*) FROM users WHERE COALESCE(blocked, 0) = 1) AS blocked,
             (SELECT COUNT(*) FROM attempts WHERE status = 'done') AS speaking,
             (SELECT COUNT(*) FROM writing_submissions WHERE status = 'done') AS writing,
             (SELECT COUNT(*) FROM attempts WHERE status = 'done'
                AND date(created_at, '+5 hours') = date('now', '+5 hours'))
           + (SELECT COUNT(*) FROM writing_submissions WHERE status = 'done'
                AND date(created_at, '+5 hours') = date('now', '+5 hours')) AS today,
             (SELECT COUNT(*) FROM attempts WHERE status = 'done' AND created_at >= datetime('now', '-7 days'))
           + (SELECT COUNT(*) FROM writing_submissions WHERE status = 'done' AND created_at >= datetime('now', '-7 days')) AS week,
             (SELECT COUNT(*) FROM attempts WHERE status = 'done' AND created_at >= datetime('now', '-30 days'))
           + (SELECT COUNT(*) FROM writing_submissions WHERE status = 'done' AND created_at >= datetime('now', '-30 days')) AS month,
             (SELECT COUNT(DISTINCT telegram_id) FROM (
                 SELECT telegram_id FROM attempts WHERE status = 'done' AND created_at >= datetime('now', '-7 days')
                 UNION SELECT telegram_id FROM writing_submissions WHERE status = 'done' AND created_at >= datetime('now', '-7 days'))) AS active_week,
             (SELECT COUNT(*) FROM premium_log WHERE action = 'grant' AND created_at >= datetime('now', '-30 days')) AS grants_month"""
    )
    row["standard"] = await count_active_premium("standard")
    row["pro"] = await count_active_premium("pro")
    row["premium"] = row["standard"] + row["pro"]
    return row


# ---------- Sozlamalar (xotirada keshlanadi - har safar bazaga bormaydi) ----------

async def load_settings():
    global _settings
    rows = await _fetchall("SELECT key, value FROM settings")
    _settings = {r["key"]: r["value"] or "" for r in rows}


def setting(key: str, default: str = "") -> str:
    """Keshdan sinxron o'qish (init_db / set_setting keshni yangilab turadi)."""
    return _settings.get(key) or default


async def get_setting(key: str, default: str = "") -> str:
    return setting(key, default)


async def set_setting(key: str, value: str):
    await _execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    _settings[key] = value


# ---------- Bloklangan foydalanuvchilar ----------

async def load_blocked():
    rows = await _fetchall("SELECT telegram_id FROM users WHERE COALESCE(blocked, 0) = 1")
    config.BLOCKED_IDS = {r["telegram_id"] for r in rows}


async def set_blocked(telegram_id: int, blocked: bool):
    # botga hali yozmagan odam ham bloklanishi mumkin - qator bo'lmasa yaratiladi
    await _execute(
        """INSERT INTO users (telegram_id, blocked) VALUES (?, ?)
           ON CONFLICT(telegram_id) DO UPDATE SET blocked = excluded.blocked""",
        (telegram_id, int(blocked)),
    )
    await load_blocked()


# ---------- Ommaviy xabar uchun auditoriya ----------

async def broadcast_ids(audience: str) -> list[int]:
    """audience: all | free | standard | pro | premium"""
    now = _now().strftime(_FMT)
    base = ("SELECT u.telegram_id FROM users u LEFT JOIN premium p ON p.telegram_id = u.telegram_id AND p.until > ? "
            "WHERE COALESCE(u.blocked, 0) = 0 AND u.full_name IS NOT NULL")  # botga yozmagan (faqat bloklangan) - yo'q
    cond = {
        "all": "",
        "free": " AND p.telegram_id IS NULL",
        "standard": " AND p.plan = 'standard'",
        "pro": " AND p.plan = 'pro'",
        "premium": " AND p.telegram_id IS NOT NULL",
    }[audience]
    return [r["telegram_id"] for r in await _fetchall(base + cond, (now,))]


# ---------- Foydalanuvchi natijalari ----------

async def user_results(telegram_id: int, limit: int = 5):
    return await _fetchall(
        """SELECT * FROM (
               SELECT 'speaking' AS kind, a.score, a.raw_score, a.level, a.created_at, e.title
               FROM attempts a LEFT JOIN exams e ON e.id = a.exam_id
               WHERE a.telegram_id = ?1 AND a.status = 'done'
               UNION ALL
               SELECT 'writing' AS kind, w.score, w.raw_score, w.level, w.created_at, e.title
               FROM writing_submissions w LEFT JOIN exams e ON e.id = w.exam_id
               WHERE w.telegram_id = ?1 AND w.status = 'done'
           ) ORDER BY created_at DESC LIMIT ?2""",
        (telegram_id, limit),
    )


_MY_RESULTS = """SELECT * FROM (
       SELECT 'speaking' AS kind, a.id, a.score, a.level, a.result_json, a.created_at, e.title, e.part
       FROM attempts a LEFT JOIN exams e ON e.id = a.exam_id
       WHERE a.telegram_id = ?1 AND a.status = 'done'
       UNION ALL
       SELECT 'writing' AS kind, w.id, w.score, w.level, w.result_json, w.created_at, e.title, e.part
       FROM writing_submissions w LEFT JOIN exams e ON e.id = w.exam_id
       WHERE w.telegram_id = ?1 AND w.status = 'done'
   )"""


async def my_results(telegram_id: int, limit: int, offset: int = 0):
    """O'quvchining o'z natijalari (eng yangisi oldin) - «📊 Natijalarim» uchun."""
    return await _fetchall(_MY_RESULTS + " ORDER BY created_at DESC, id DESC LIMIT ?2 OFFSET ?3",
                           (telegram_id, limit, offset))


async def my_result_counts(telegram_id: int) -> dict:
    rows = await _fetchall(f"SELECT kind, COUNT(*) AS c FROM ({_MY_RESULTS}) GROUP BY kind", (telegram_id,))
    return {r["kind"]: r["c"] for r in rows}


async def my_result(telegram_id: int, kind: str, result_id: int):
    """Bitta natija - faqat egasiga (boshqa odamning natijasini ochib bo'lmaydi)."""
    return await _fetchone(_MY_RESULTS + " WHERE kind = ?2 AND id = ?3", (telegram_id, kind, result_id))


# ---------- Imtihonni qayta nomlash ----------

async def rename_exam(exam_id: int, title: str):
    await _execute("UPDATE exams SET title = ? WHERE id = ?", (title, exam_id))


# ---------- Foydalanuvchilar ro'yxati (admin uchun) ----------

async def count_users() -> int:
    return (await _fetchone("SELECT COUNT(*) AS c FROM users WHERE full_name IS NOT NULL"))["c"]


async def list_users(offset: int = 0, limit: int = 8):
    """Eng yangi qo'shilganlar birinchi; premium holati bilan."""
    return await _fetchall(
        """SELECT u.telegram_id, u.full_name, u.username, u.joined_at,
                  (p.until IS NOT NULL AND p.until > ?) AS is_premium, p.plan AS plan, COALESCE(u.blocked, 0) AS blocked
           FROM users u LEFT JOIN premium p ON p.telegram_id = u.telegram_id
           WHERE u.full_name IS NOT NULL OR COALESCE(u.blocked, 0) = 1
           ORDER BY u.joined_at DESC, u.telegram_id DESC LIMIT ? OFFSET ?""",
        (_now().strftime(_FMT), limit, offset),
    )


# ---------- Hamma testlarni o'chirish ----------

# ---------- Bot holati (FSM): ko'p bosqichli amallar qayta ishga tushganda ham davom etadi ----------

async def fsm_get(key: str):
    return await _fetchone("SELECT state, data, updated_at FROM fsm_state WHERE key = ?", (key,))


async def fsm_set(key: str, state: str | None, data: str):
    if state is None and not data:
        await _execute("DELETE FROM fsm_state WHERE key = ?", (key,))
        return
    await _execute(
        """INSERT INTO fsm_state (key, state, data, updated_at) VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET state = excluded.state, data = excluded.data, updated_at = excluded.updated_at""",
        (key, state, data),
    )


# ---------- Yagona faol nusxa: Telegram'dan xabarlarni faqat bitta bot nusxasi oladi ----------

async def lock_take(instance: str, service: str):
    await _execute(
        """INSERT INTO bot_lock (id, instance, service, heartbeat) VALUES (1, ?, ?, datetime('now'))
           ON CONFLICT(id) DO UPDATE SET instance = excluded.instance, service = excluded.service,
                                         heartbeat = excluded.heartbeat""",
        (instance, service),
    )


async def lock_get():
    return await _fetchone(
        "SELECT instance, service, heartbeat, (julianday('now') - julianday(heartbeat)) * 86400 AS age FROM bot_lock WHERE id = 1"
    )


async def lock_beat(instance: str):
    await _execute("UPDATE bot_lock SET heartbeat = datetime('now') WHERE id = 1 AND instance = ?", (instance,))


async def lock_release(instance: str):
    await _execute("UPDATE bot_lock SET heartbeat = '2000-01-01 00:00:00' WHERE id = 1 AND instance = ?", (instance,))
