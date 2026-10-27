"""SQLite baza: imtihonlar (speaking/writing), savollar, urinishlar, javoblar, writing ishlari."""
import json
import re
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
CREATE TABLE IF NOT EXISTS mocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number INTEGER NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    plan TEXT,
    file_id TEXT,
    file_type TEXT,
    status TEXT DEFAULT 'pending',
    decided_by INTEGER,
    decided_plan TEXT,
    decided_at TEXT,
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
    # O'quvchining shaxsiy testi: owner_id - kim yaratgan (NULL - admin testi), expires_at - qachon o'chadi
    "ALTER TABLE exams ADD COLUMN owner_id INTEGER",
    "ALTER TABLE exams ADD COLUMN expires_at TEXT",
    "ALTER TABLE admins ADD COLUMN perms TEXT",  # adminning ochiq bo'limlari (NULL - hammasi), roles.py
    "ALTER TABLE exams ADD COLUMN mock_id INTEGER",  # qaysi mockning qismi (NULL - eski, alohida test)
    # obunaning 30 kunlik limit davrlari shu vaqtdan sanaladi (NULL - eski yozuv: tugash sanasidan orqaga)
    "ALTER TABLE premium ADD COLUMN period_start TEXT",
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
    "CREATE INDEX IF NOT EXISTS ix_exams_mock ON exams(mock_id)",
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
_ALL_OPEN = "UPDATE exams SET is_active = 1 WHERE (is_active IS NULL OR is_active != 1) AND owner_id IS NULL"


async def init_db():
    _touch_catalog("UPDATE exams")  # ishga tushishda testlar o'zgarishi mumkin (migratsiyalar)
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


# Testlar katalogi versiyasi: exams / questions jadvaliga har yozuvda oshadi - bot.py dagi o'quvchi katalogi
# keshi shu orqali eskirganini biladi (har menyu ochilishida bazaga borilmaydi).
CATALOG_VERSION = 0
_CATALOG_TABLES = re.compile(r"\b(exams|questions|mocks)\b", re.IGNORECASE)


def _touch_catalog(sql: str):
    global CATALOG_VERSION
    if not sql.lstrip()[:6].upper().startswith("SELECT") and _CATALOG_TABLES.search(sql):
        CATALOG_VERSION += 1


async def _execute(sql, params=()) -> int:
    _touch_catalog(sql)
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
    for sql, _ in statements:
        _touch_catalog(sql)
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


PERSONAL_TTL_HOURS = 2  # o'quvchi yaratgan test shuncha soatdan keyin o'chadi (natijalari qoladi)


async def create_personal_exam(owner_id: int, title: str, kind: str, part: str | None) -> int:
    return await _execute(
        f"""INSERT INTO exams (title, kind, part, is_active, owner_id, expires_at)
            VALUES (?, ?, ?, 1, ?, datetime('now', '+{PERSONAL_TTL_HOURS} hours'))""",
        (title, kind, part, owner_id),
    )


async def my_personal_exams(owner_id: int, kind: str | None = None):
    """O'quvchining hali o'chmagan shaxsiy testlari (qolgan daqiqalar bilan)."""
    kind_sql = "AND COALESCE(e.kind, 'speaking') = ?" if kind else ""
    return await _fetchall(
        f"""SELECT e.*, CAST((julianday(e.expires_at) - julianday('now')) * 1440 AS INTEGER) AS minutes_left,
                   (SELECT COUNT(*) FROM questions q WHERE q.exam_id = e.id) AS q_count
            FROM exams e
            WHERE e.owner_id = ? AND e.expires_at IS NOT NULL AND e.expires_at > datetime('now') {kind_sql}
            ORDER BY e.id""",
        (owner_id, kind) if kind else (owner_id,),
    )


async def purge_expired_personal() -> int:
    """Muddati o'tgan shaxsiy testlar: savollari (matn, rasm) o'chiriladi, test o'quvchiga yopiladi.
    Test qatori nomi bilan qoladi - natijalar tarixida «qaysi test» ko'rinib tursin. Hozir topshirilayotgan
    (oxirgi 30 daqiqada boshlangan) test kutib turiladi."""
    rows = await _fetchall(
        """SELECT e.id FROM exams e
           WHERE e.owner_id IS NOT NULL AND e.expires_at IS NOT NULL AND e.expires_at <= datetime('now')
             AND NOT EXISTS (SELECT 1 FROM attempts a WHERE a.exam_id = e.id AND a.status = 'in_progress'
                             AND a.created_at > datetime('now', '-30 minutes'))"""
    )
    ids = [r["id"] for r in rows]
    if ids:
        marks = ",".join("?" * len(ids))
        await _execute_many([
            (f"DELETE FROM questions WHERE exam_id IN ({marks})", tuple(ids)),
            (f"UPDATE exams SET is_active = 0, expires_at = NULL WHERE id IN ({marks})", tuple(ids)),
        ])
    return len(ids)


async def archive_personal(exam_id: int):
    """O'quvchi shaxsiy testini o'zi o'chirdi: savollari o'chadi, nomi natijalar tarixi uchun qoladi."""
    await _execute_many([
        ("DELETE FROM questions WHERE exam_id = ?", (exam_id,)),
        ("UPDATE exams SET is_active = 0, expires_at = NULL WHERE id = ?", (exam_id,)),
    ])


def exam_open_for(exam: dict | None, user_id: int) -> bool:
    """O'quvchi bu testni ocha oladimi: admin testi ochiq bo'lsa - hamma; shaxsiy test - faqat egasi va
    muddati tugamagan bo'lsa."""
    if not exam or not exam.get("is_active"):
        return False
    if exam.get("owner_id") is None:
        return True
    if exam["owner_id"] != user_id or not exam.get("expires_at"):
        return False
    return exam["expires_at"] > datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


async def get_exam(exam_id: int):
    return await _fetchone("SELECT * FROM exams WHERE id = ?", (exam_id,))


async def list_exams():
    return await _fetchall(
        """SELECT e.*, (SELECT COUNT(*) FROM questions q WHERE q.exam_id = e.id) AS q_count,
                  (SELECT q.text FROM questions q WHERE q.exam_id = e.id ORDER BY q.position, q.id LIMIT 1) AS task_text,
                  (SELECT q.meta FROM questions q WHERE q.exam_id = e.id ORDER BY q.position, q.id LIMIT 1) AS task_meta
           FROM exams e WHERE e.owner_id IS NULL ORDER BY COALESCE(e.kind, 'speaking'), e.part, e.id DESC"""
    )


def like_variants(q: str) -> list[str]:
    """LIKE uchun naqshlar: SQLite faqat lotin harflarida katta-kichikni farqlamaydi - turkcha/kirill uchun
    bir necha yozilish shakli qo'shiladi."""
    q = q.replace("%", "").replace("_", "").strip()
    forms = dict.fromkeys([q, q.lower(), q.upper(), q.capitalize(), q.title()])
    return [f"%{f}%" for f in forms if f]


async def list_exams_page(offset: int = 0, limit: int = 10, query: str | None = None) -> tuple[list[dict], int]:
    """Admin testlari: eng yangisi birinchi, faqat bitta sahifa (ko'p test bo'lsa ham baza yuklanmaydi).
    query - nom bo'yicha qidiruv. (qatorlar, jami soni) qaytadi."""
    where, params = "e.owner_id IS NULL AND e.mock_id IS NULL", []
    if query:
        pats = like_variants(query)
        where += " AND (" + " OR ".join("e.title LIKE ?" for _ in pats) + ")"
        params += pats
    total = (await _fetchone(f"SELECT COUNT(*) AS c FROM exams e WHERE {where}", tuple(params)))["c"]
    rows = await _fetchall(
        f"""SELECT e.*, (SELECT COUNT(*) FROM questions q WHERE q.exam_id = e.id) AS q_count,
                   (SELECT q.text FROM questions q WHERE q.exam_id = e.id ORDER BY q.position, q.id LIMIT 1) AS task_text,
                   (SELECT q.meta FROM questions q WHERE q.exam_id = e.id ORDER BY q.position, q.id LIMIT 1) AS task_meta
            FROM exams e WHERE {where} ORDER BY e.id DESC LIMIT ? OFFSET ?""",
        tuple(params) + (limit, offset),
    )
    return rows, total


async def search_users(query: str, limit: int = 10) -> list[dict]:
    """ID, @username yoki ism (bir qismi) bo'yicha - ko'pi bilan `limit` ta (butun jadval o'qilmaydi)."""
    q = (query or "").strip()
    if q.lstrip("-").isdigit():
        user = await get_user(int(q))
        return [user] if user else []
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if q.lower().startswith(prefix):
            q = q[len(prefix):]
    q = q.strip().lstrip("@").strip("/")
    if not q:
        return []
    exact = await _fetchall("SELECT * FROM users WHERE LOWER(username) = LOWER(?) LIMIT 1", (q,))
    if exact:
        return exact
    pats = like_variants(q)
    cond = " OR ".join(["full_name LIKE ?"] * len(pats) + ["username LIKE ?"] * len(pats))
    return await _fetchall(f"SELECT * FROM users WHERE full_name IS NOT NULL AND ({cond}) "
                           f"ORDER BY joined_at DESC LIMIT ?", tuple(pats + pats) + (limit,))


async def active_part_counts(kind: str) -> dict:
    """{part: nechta ochiq imtihon} - o'quvchiga faqat ichida imtihon bor turlar ko'rsatiladi."""
    rows = await _fetchall(
        """SELECT e.part, COUNT(*) AS cnt FROM exams e
           WHERE e.is_active = 1 AND e.owner_id IS NULL AND COALESCE(e.kind, 'speaking') = ?
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
            WHERE e.is_active = 1 AND e.owner_id IS NULL AND COALESCE(e.kind, 'speaking') = ? AND {part_sql}
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
           WHERE e.is_active = 1 AND e.owner_id IS NULL AND COALESCE(e.kind, 'speaking') = ?
           ORDER BY e.id""",
        (kind,),
    )


async def set_exam_active(exam_id: int, active: bool):
    await _execute("UPDATE exams SET is_active = ? WHERE id = ?", (int(active), exam_id))


async def delete_exam(exam_id: int):
    await _execute("DELETE FROM questions WHERE exam_id = ?", (exam_id,))
    await _execute("DELETE FROM exams WHERE id = ?", (exam_id,))


# ---------- O'quvchining yakunlangan ishlari (limit, ✅ belgilari, qayta ishlash taqiqi) - bitta so'rovda ----------
# Har tugmada limit, ochilgan mocklar va ishlangan qismlar kerak: avval bular 3-5 ta alohida so'rov edi.
# Endi o'quvchining hamma yakunlangan ishi bitta so'rovda olinib, xotirada turadi; yangi ish yakunlanganda
# (finish_attempt / finish_writing_submission) kesh tozalanadi.
_done_cache: dict[int, tuple[float, list[dict]]] = {}
DONE_TTL = 600


async def user_done(telegram_id: int) -> list[dict]:
    """[{kind: speaking | writing | own_speaking | own_writing, mock_id, part, created_at, own}] - eng yangisi oxirida."""
    cached = _done_cache.get(telegram_id)
    if cached and time.time() - cached[0] < DONE_TTL:
        return cached[1]
    rows = await _fetchall(
        """SELECT 'speaking' AS kind, e.mock_id AS mock_id, e.part AS part, t.created_at AS created_at,
                  CASE WHEN e.owner_id = t.telegram_id THEN 1 ELSE 0 END AS own
           FROM attempts t LEFT JOIN exams e ON e.id = t.exam_id
           WHERE t.telegram_id = ? AND t.status = 'done'
           UNION ALL
           SELECT 'writing', e.mock_id, COALESCE(json_extract(t.result_json, '$.part'), e.part), t.created_at,
                  CASE WHEN e.owner_id = t.telegram_id THEN 1 ELSE 0 END
           FROM writing_submissions t LEFT JOIN exams e ON e.id = t.exam_id
           WHERE t.telegram_id = ? AND t.status = 'done'
           UNION ALL
           SELECT 'own_' || COALESCE(x.kind, 'speaking'), NULL, NULL, x.created_at, 1
           FROM exams x WHERE x.owner_id = ?""",
        (telegram_id, telegram_id, telegram_id),
    )
    _done_cache[telegram_id] = (time.time(), rows)
    return rows


def forget_user(telegram_id: int):
    _done_cache.pop(telegram_id, None)
    _premium_cache.pop(telegram_id, None)


async def fetch_many(queries: list[tuple[str, tuple]]) -> list[list[dict]]:
    """Bir nechta o'qish so'rovi - Turso'ga BITTA tarmoq murojaatida (har murojaat ~0.2 s)."""
    if _turso:
        return [r.rows for r in await _turso.batch(queries)]
    return [await _fetchall(sql, params) for sql, params in queries]


# ---------- Mocklar: har biri 4 ta Konuşma qismi (1.1, 1.2, 2, 3) + 1 ta to'liq Yazma ----------

MOCK_SPEAKING_PARTS = ("1.1", "1.2", "2", "3")


async def create_mock(title: str | None = None) -> int:
    """Yangi mock: tartib raqami avtomatik, ichida 5 ta bo'sh test (savollari keyin qo'shiladi)."""
    n = (await _fetchone("SELECT COALESCE(MAX(number), 0) + 1 AS n FROM mocks"))["n"]
    mock_id = await _execute("INSERT INTO mocks (number, title) VALUES (?, ?)", (n, title or f"Mock {n}"))
    sql = "INSERT INTO exams (title, kind, part, is_active, mock_id) VALUES (?, ?, ?, 1, ?)"
    await _execute_many(
        [(sql, (f"Mock {n} • Part {p}", "speaking", p, mock_id)) for p in MOCK_SPEAKING_PARTS]
        + [(sql, (f"Mock {n} • Yazma", "writing", "full", mock_id))]
    )
    return mock_id


async def get_mock(mock_id: int):
    return await _fetchone("SELECT * FROM mocks WHERE id = ?", (mock_id,))


async def count_mocks() -> int:
    return (await _fetchone("SELECT COUNT(*) AS c FROM mocks"))["c"]


async def mock_exams(mock_id: int):
    """Mockning testlari (savollar soni va birinchi savol / topshiriq bilan) - bitta so'rovda."""
    return await _fetchall(
        """SELECT e.*, (SELECT COUNT(*) FROM questions q WHERE q.exam_id = e.id) AS q_count,
                  (SELECT q.text FROM questions q WHERE q.exam_id = e.id ORDER BY q.position, q.id LIMIT 1) AS task_text,
                  (SELECT q.meta FROM questions q WHERE q.exam_id = e.id ORDER BY q.position, q.id LIMIT 1) AS task_meta
           FROM exams e WHERE e.mock_id = ? ORDER BY COALESCE(e.kind, 'speaking') DESC, e.part, e.id""",
        (mock_id,),
    )


async def all_mocks_with_exams():
    """Hamma mocklar va ularning testlari (o'quvchi katalogi uchun, xotirada keshlanadi)."""
    return await _fetchall(
        """SELECT m.id AS mock_id, m.number, m.title AS mock_title, e.id, e.kind, e.part, e.title,
                  (SELECT COUNT(*) FROM questions q WHERE q.exam_id = e.id) AS q_count,
                  (SELECT q.text FROM questions q WHERE q.exam_id = e.id ORDER BY q.position, q.id LIMIT 1) AS task_text,
                  (SELECT q.meta FROM questions q WHERE q.exam_id = e.id ORDER BY q.position, q.id LIMIT 1) AS task_meta
           FROM mocks m LEFT JOIN exams e ON e.mock_id = m.id AND e.is_active = 1
           ORDER BY m.number, m.id, e.id"""
    )


async def list_mocks_page(offset: int = 0, limit: int = 10, query: str | None = None) -> tuple[list[dict], int]:
    """Admin uchun: mocklar tartib raqami bo'yicha, bitta sahifa. query - raqam yoki nom."""
    where, params = "1 = 1", []
    q = (query or "").strip()
    if q:
        digits = "".join(ch for ch in q if ch.isdigit())
        if digits and len(digits) <= 6:
            where, params = "m.number = ?", [int(digits)]
        else:
            pats = like_variants(q)
            where = "(" + " OR ".join("m.title LIKE ?" for _ in pats) + ")"
            params = pats
    count_rows, rows = await fetch_many([(f"SELECT COUNT(*) AS c FROM mocks m WHERE {where}", tuple(params)), (
        f"""SELECT m.*,
                   (SELECT COUNT(*) FROM exams e WHERE e.mock_id = m.id AND e.kind = 'speaking'
                      AND EXISTS (SELECT 1 FROM questions q WHERE q.exam_id = e.id)) AS sp_ready,
                   (SELECT COUNT(*) FROM exams e WHERE e.mock_id = m.id AND e.kind = 'writing'
                      AND EXISTS (SELECT 1 FROM questions q WHERE q.exam_id = e.id)) AS wr_ready
            FROM mocks m WHERE {where} ORDER BY m.number, m.id LIMIT ? OFFSET ?""",
        tuple(params) + (limit, offset))])
    return rows, count_rows[0]["c"]


async def delete_mock(mock_id: int):
    """Mock va uning savollari o'chadi; o'quvchilar natijalari (urinishlar) saqlanib qoladi."""
    await _execute_many([
        ("DELETE FROM questions WHERE exam_id IN (SELECT id FROM exams WHERE mock_id = ?)", (mock_id,)),
        ("DELETE FROM exams WHERE mock_id = ?", (mock_id,)),
        ("DELETE FROM mocks WHERE id = ?", (mock_id,)),
    ])
    _done_cache.clear()


TRIAL_KEY = "trial_mock"  # settings: sinov mock id'si (admin tanlaydi)
_trial_cache: tuple | None = None


async def trial_mock_id() -> int | None:
    """🎁 Sinov mock - bepul foydalanuvchilar faqat shuni ko'radi. Admin tanlagani (bor bo'lsa), aks holda
    savoli bor eng kichik raqamli mock. Mocklar yoki sozlama o'zgarmaguncha xotiradan olinadi."""
    global _trial_cache
    chosen = setting(TRIAL_KEY)
    key = (CATALOG_VERSION, chosen)
    if _trial_cache and _trial_cache[0] == key:
        return _trial_cache[1]
    row = await _fetchone(
        """SELECT (SELECT id FROM mocks WHERE id = ?) AS chosen,
                  (SELECT m.id FROM mocks m
                    WHERE EXISTS (SELECT 1 FROM exams e JOIN questions q ON q.exam_id = e.id
                                   WHERE e.mock_id = m.id AND e.is_active = 1)
                    ORDER BY m.number, m.id LIMIT 1) AS first""",
        (int(chosen) if chosen.isdigit() else -1,),
    )
    mock_id = (row or {}).get("chosen") or (row or {}).get("first")
    _trial_cache = (key, mock_id)
    return mock_id


_order_cache: tuple | None = None


async def mock_order() -> list[int]:
    """Savoli bor mocklar ro'yxat tartibida (raqami bo'yicha) - tarifda «birinchi N ta mock» shu tartibdan.
    Mocklar o'zgarmaguncha xotiradan olinadi."""
    global _order_cache
    if _order_cache and _order_cache[0] == CATALOG_VERSION:
        return _order_cache[1]
    version = CATALOG_VERSION
    rows = await _fetchall(
        """SELECT m.id FROM mocks m
           WHERE EXISTS (SELECT 1 FROM exams e JOIN questions q ON q.exam_id = e.id
                          WHERE e.mock_id = m.id AND e.is_active = 1)
           ORDER BY m.number, m.id""")
    _order_cache = (version, [r["id"] for r in rows])
    return _order_cache[1]


async def shuffle_mocks(upto: int) -> list[tuple[str, int, int]]:
    """Ro'yxatdagi birinchi `upto` ta mockni o'zaro aralashtiradi (tartib raqamlari almashadi): ichidagi savollar
    va natijalar o'z mockida qoladi, faqat ro'yxatdagi o'rni o'zgaradi. Standart nom («Mock 3», «Mock 3 • Part 1.1»)
    yangi raqamga moslanadi; admin qo'ygan nom o'zgarmaydi. [(eski nom, eski raqam, yangi raqam)] qaytaradi."""
    import random
    mocks = await _fetchall("SELECT * FROM mocks ORDER BY number, id LIMIT ?", (int(upto),))
    if len(mocks) < 2:
        return []
    if not setting(TRIAL_KEY):  # sinov mock tanlanmagan - aralashtirishda almashib ketmasin, hozirgisini qotiramiz
        trial = await trial_mock_id()
        if trial:
            await set_setting(TRIAL_KEY, str(trial))
    numbers = [m["number"] for m in mocks]
    new = numbers[:]
    while new == numbers:  # kamida bitta mock o'rnini o'zgartirsin
        random.shuffle(new)
    statements, moves = [], []
    for m, num in zip(mocks, new):
        old = m["number"]
        if old == num:
            continue
        title = f"Mock {num}" if m["title"] == f"Mock {old}" else m["title"]
        statements += [
            ("UPDATE mocks SET number = ?, title = ? WHERE id = ?", (num, title, m["id"])),
            ("UPDATE exams SET title = ? || substr(title, ?) WHERE mock_id = ? AND title LIKE ?",
             (f"Mock {num} •", len(f"Mock {old} •") + 1, m["id"], f"Mock {old} •%")),
        ]
        moves.append((m["title"], old, num))
    await _execute_many(statements)
    return moves


async def rename_mock(mock_id: int, title: str):
    await _execute("UPDATE mocks SET title = ? WHERE id = ?", (title, mock_id))


async def mock_done_parts(telegram_id: int, mock_id: int) -> list[tuple[str, str]]:
    """O'quvchi bu mockda yakunlagan qismlar: [(kind, part)]. Yazmada - ishlangan rejim (full / 1 / 2)."""
    rows = await _fetchall(
        """SELECT 'speaking' AS kind, e.part AS part FROM attempts a JOIN exams e ON e.id = a.exam_id
           WHERE a.telegram_id = ? AND a.status = 'done' AND e.mock_id = ?
           UNION
           SELECT 'writing', COALESCE(json_extract(w.result_json, '$.part'), e.part)
           FROM writing_submissions w JOIN exams e ON e.id = w.exam_id
           WHERE w.telegram_id = ? AND w.status = 'done' AND e.mock_id = ?""",
        (telegram_id, mock_id, telegram_id, mock_id),
    )
    return [(r["kind"], r["part"]) for r in rows]


async def mocks_done(telegram_id: int, kind: str | None = None) -> set[int]:
    """O'quvchi kamida bir qismini ishlagan mocklar (ro'yxatda ✅ belgisi uchun); kind - faqat shu bo'lim."""
    sp = """SELECT DISTINCT e.mock_id FROM attempts a JOIN exams e ON e.id = a.exam_id
            WHERE a.telegram_id = ? AND a.status = 'done' AND e.mock_id IS NOT NULL"""
    wr = """SELECT DISTINCT e.mock_id FROM writing_submissions w JOIN exams e ON e.id = w.exam_id
            WHERE w.telegram_id = ? AND w.status = 'done' AND e.mock_id IS NOT NULL"""
    if kind == "speaking":
        rows = await _fetchall(sp, (telegram_id,))
    elif kind == "writing":
        rows = await _fetchall(wr, (telegram_id,))
    else:
        rows = await _fetchall(f"{sp} UNION {wr}", (telegram_id, telegram_id))
    return {r["mock_id"] for r in rows}


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
    _done_cache.clear()


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
    _done_cache.clear()


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


async def users_for_api(plan: str | None, since: str | None, limit: int, offset: int, q: str | None = None):
    """GET /api/users uchun: ro'yxatdan o'tgan foydalanuvchilar, tarifi va topshirgan testlari soni.
    Har bir qatorda `total` - filtr bo'yicha jami soni (sahifalash uchun)."""
    now = _now().strftime(_FMT)
    where, params = [], [now, now]
    if plan:
        where.append("plan = ?")
        params.append(plan)
    if since:
        where.append("joined_at >= ?")
        params.append(since)
    if q and q.strip():  # Boss sayti: ism, @username yoki ID bo'yicha qidiruv
        q = q.strip().lstrip("@")
        pats = like_variants(q)
        ors = ["full_name LIKE ?"] * len(pats) + ["username LIKE ?"] * len(pats)
        params += pats + pats
        if q.isdigit():
            ors.append("telegram_id = ?")
            params.append(int(q))
        where.append("(" + " OR ".join(ors) + ")")
    cond = ("WHERE " + " AND ".join(where)) if where else ""
    return await _fetchall(
        f"""SELECT *, COUNT(*) OVER () AS total FROM (
               SELECT u.telegram_id, u.full_name, u.username, u.joined_at, COALESCE(u.blocked, 0) AS blocked,
                      CASE WHEN p.until > ? THEN COALESCE(p.plan, 'pro') ELSE 'free' END AS plan,
                      CASE WHEN p.until > ? THEN p.until END AS plan_until,
                      (SELECT COUNT(*) FROM attempts a WHERE a.telegram_id = u.telegram_id AND a.status = 'done') AS speaking_count,
                      (SELECT COUNT(*) FROM writing_submissions w WHERE w.telegram_id = u.telegram_id AND w.status = 'done') AS writing_count,
                      NULLIF(MAX(COALESCE((SELECT MAX(a.created_at) FROM attempts a WHERE a.telegram_id = u.telegram_id), ''),
                                 COALESCE((SELECT MAX(w.created_at) FROM writing_submissions w WHERE w.telegram_id = u.telegram_id), '')),
                             '') AS last_activity
               FROM users u LEFT JOIN premium p ON p.telegram_id = u.telegram_id
               WHERE u.full_name IS NOT NULL
           ) {cond}
           ORDER BY joined_at DESC, telegram_id LIMIT ? OFFSET ?""",
        tuple(params) + (limit, offset),
    )


async def list_payments(limit: int = 50):
    """Boss sayti: oxirgi to'lov cheklari (kim yubordi, qaysi tarif, holati, kim hal qildi)."""
    return await _fetchall(
        """SELECT p.id, p.telegram_id, p.plan, p.status, p.decided_plan, p.created_at, p.decided_at,
                  u.full_name, u.username, a.full_name AS admin_name, a.username AS admin_username
           FROM payments p LEFT JOIN users u ON u.telegram_id = p.telegram_id
                LEFT JOIN users a ON a.telegram_id = p.decided_by
           ORDER BY p.id DESC LIMIT ?""",
        (limit,),
    )


async def daily_activity(days: int = 14):
    """Boss sayti: kunlik tekshiruvlar (Toshkent sanasi bo'yicha) - Konuşma va Yazma alohida."""
    rows = await _fetchall(
        f"""SELECT d, SUM(s) AS speaking, SUM(w) AS writing FROM (
              SELECT date(created_at, '+5 hours') AS d, 1 AS s, 0 AS w FROM attempts
              WHERE status = 'done' AND created_at >= datetime('now', '-{int(days)} days')
              UNION ALL
              SELECT date(created_at, '+5 hours'), 0, 1 FROM writing_submissions
              WHERE status = 'done' AND created_at >= datetime('now', '-{int(days)} days'))
            GROUP BY d ORDER BY d"""
    )
    return rows


async def pending_payments(limit: int = 10):
    return await _fetchall(
        """SELECT p.*, u.full_name, u.username FROM payments p LEFT JOIN users u ON u.telegram_id = p.telegram_id
           WHERE p.status = 'pending' ORDER BY p.id LIMIT ?""", (limit,))


async def admin_activity(days: int = 30) -> dict:
    """{admin_id: {"grants": n, "checks": m}} - kim nechta obuna berdi va nechta chekni ko'rib chiqdi."""
    grants, checks = await fetch_many([
        (f"""SELECT granted_by AS uid, COUNT(*) AS c FROM premium_log WHERE action = 'grant'
             AND created_at >= datetime('now', '-{int(days)} days') GROUP BY granted_by""", ()),
        (f"""SELECT decided_by AS uid, COUNT(*) AS c FROM payments WHERE decided_by IS NOT NULL
             AND decided_at >= datetime('now', '-{int(days)} days') GROUP BY decided_by""", ()),
    ])
    out: dict = {}
    for r in grants:
        out.setdefault(r["uid"], {"grants": 0, "checks": 0})["grants"] = r["c"]
    for r in checks:
        out.setdefault(r["uid"], {"grants": 0, "checks": 0})["checks"] = r["c"]
    return out


async def count_pending_payments() -> int:
    return (await _fetchone("SELECT COUNT(*) AS c FROM payments WHERE status = 'pending'"))["c"]


# ---------- Adminlar ----------

async def load_admins():
    """Bazadagi adminlarni xotiraga yuklaydi (config.is_admin ularni ko'radi)."""
    rows = await _fetchall("SELECT telegram_id, perms FROM admins")
    config.EXTRA_ADMIN_IDS = {r["telegram_id"] for r in rows}
    config.ADMIN_PERMS = {r["telegram_id"]: {p for p in r["perms"].split(",") if p}
                          for r in rows if r.get("perms") is not None}


async def set_admin_perms(telegram_id: int, perms: str | None):
    """perms: "tests,users,..." yoki None (hamma bo'lim)."""
    await _execute("UPDATE admins SET perms = ? WHERE telegram_id = ?", (perms, telegram_id))
    await load_admins()


async def list_admins():
    return await _fetchall(
        """SELECT a.telegram_id, a.added_at, a.perms, u.full_name, u.username FROM admins a
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
    found = await search_users(q, limit=2)  # ism bo'yicha - faqat bitta odam mos kelsa
    user = found[0] if len(found) == 1 else None
    return {**user, "known": True} if user else None


# ---------- Premium ----------

_FMT = "%Y-%m-%d %H:%M:%S"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Tarif har tugma bosilganda kerak - xotirada saqlanadi (bazaga har safar ~0.2 s ketardi). Faqat shu yerdagi
# grant_premium / revoke_premium o'zgartiradi va keshni yangilaydi; zaxira muddat - PREMIUM_TTL.
_premium_cache: dict[int, tuple[float, dict | None]] = {}
PREMIUM_TTL = 600


async def get_premium(telegram_id: int) -> dict | None:
    """Faol premium: {"until", "plan": "standard" | "pro" | "vip", "period_start"} yoki None (tugagan / yo'q).
    period_start - limit davrlari shu vaqtdan 30 kundan sanaladi (eski yozuvlarda None)."""
    cached = _premium_cache.get(telegram_id)
    if cached and time.time() - cached[0] < PREMIUM_TTL:
        row = cached[1]
    else:
        row = await _fetchone("SELECT until, plan, period_start FROM premium WHERE telegram_id = ?", (telegram_id,))
        _premium_cache[telegram_id] = (time.time(), row)
    if row and row["until"] > _now().strftime(_FMT):
        return {"until": row["until"], "plan": row["plan"] or "pro", "period_start": row.get("period_start")}
    return None


async def get_premium_until(telegram_id: int) -> str | None:
    p = await get_premium(telegram_id)
    return p["until"] if p else None


PERIOD = timedelta(days=30)  # plans.PERIOD_DAYS (plans db'ni import qiladi - aylana importdan qochish)


def legacy_period_start(until: str) -> str:
    """period_start yozilmagan (eski) obuna: joriy davr boshi tugash sanasidan orqaga 30 kundan sanaladi."""
    start, now = datetime.strptime(until, _FMT) - PERIOD, _now()
    while start > now:
        start -= PERIOD
    return start.strftime(_FMT)


async def grant_premium(telegram_id: int, days: int, amount: str, granted_by: int, plan: str = "pro",
                        restart: bool = False) -> str:
    """Obuna beradi, tarifni `plan` ga o'rnatadi; yangi tugash vaqtini qaytaradi.
    restart=False: faol obuna `days` kunga uzaytiriladi (tugash sanasiga qo'shiladi), limit davrlari o'zgarmaydi.
    restart=True (limit tugagan): obuna HOZIRDAN yangidan boshlanadi - yangi 30 kunlik davr va to'liq limit;
    eski obunaning qolgan kunlari kuyadi."""
    _premium_cache.pop(telegram_id, None)
    current = await get_premium(telegram_id)
    now = _now()
    if current and not restart:
        base = datetime.strptime(current["until"], _FMT)
        start = current.get("period_start") or legacy_period_start(current["until"])
    else:
        base, start = now, now.strftime(_FMT)
    until = (base + timedelta(days=days)).strftime(_FMT)
    await _execute_many([
        ("""INSERT INTO premium (telegram_id, until, plan, reminded_for, period_start) VALUES (?, ?, ?, NULL, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET until = excluded.until, plan = excluded.plan,
                reminded_for = NULL, period_start = excluded.period_start, updated_at = datetime('now')""",
         (telegram_id, until, plan, start)),
        ("INSERT INTO premium_log (telegram_id, action, plan, days, amount, granted_by) VALUES (?, 'grant', ?, ?, ?, ?)",
         (telegram_id, plan, days, amount, granted_by)),
    ])
    _premium_cache[telegram_id] = (time.time(), {"until": until, "plan": plan, "period_start": start})
    return until


async def revoke_premium(telegram_id: int, revoked_by: int):
    await _execute("DELETE FROM premium WHERE telegram_id = ?", (telegram_id,))
    _premium_cache[telegram_id] = (time.time(), None)
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


async def grants_since(days: int = 30):
    """Boss hisoboti uchun: so'nggi N kunda berilgan obunalar (kim bergan bilan)."""
    return await _fetchall(
        f"""SELECT l.id, l.telegram_id, l.plan, l.days, l.amount, l.granted_by, l.created_at,
                   a.full_name AS admin_name, a.username AS admin_username,
                   u.full_name AS buyer_name, u.username AS buyer_username, p.until AS until
            FROM premium_log l LEFT JOIN users a ON a.telegram_id = l.granted_by
                 LEFT JOIN users u ON u.telegram_id = l.telegram_id
                 LEFT JOIN premium p ON p.telegram_id = l.telegram_id
            WHERE l.action = 'grant' AND l.created_at >= datetime('now', '-{int(days)} days')
            ORDER BY l.id DESC"""
    )


async def count_exams_by_kind() -> dict:
    """Eski (mockka kirmagan) admin testlari soni: {"speaking": n, "writing": m}."""
    rows = await _fetchall("SELECT COALESCE(kind, 'speaking') AS kind, COUNT(*) AS c FROM exams "
                           "WHERE owner_id IS NULL AND mock_id IS NULL GROUP BY 1")
    return {r["kind"]: r["c"] for r in rows}


# ---------- To'lov cheklari (o'quvchi yuboradi, admin tasdiqlaydi) ----------

async def create_payment(telegram_id: int, plan: str, file_id: str, file_type: str) -> int:
    return await _execute("INSERT INTO payments (telegram_id, plan, file_id, file_type) VALUES (?, ?, ?, ?)",
                          (telegram_id, plan, file_id, file_type))


async def get_payment(payment_id: int):
    return await _fetchone("SELECT * FROM payments WHERE id = ?", (payment_id,))


async def pending_payment(telegram_id: int):
    """Hali ko'rib chiqilmagan (oxirgi 3 kundagi) chek."""
    return await _fetchone("SELECT * FROM payments WHERE telegram_id = ? AND status = 'pending' "
                           "AND created_at > datetime('now', '-3 days') ORDER BY id DESC LIMIT 1", (telegram_id,))


async def decide_payment(payment_id: int, status: str, admin_id: int, plan: str | None = None) -> bool:
    """Chekni hal qiladi; False - uni boshqa admin allaqachon hal qilgan (bitta chek ikki marta tasdiqlanmasin)."""
    sql = ("UPDATE payments SET status = ?, decided_by = ?, decided_plan = ?, decided_at = datetime('now') "
           "WHERE id = ? AND status = 'pending'")
    params = (status, admin_id, plan, payment_id)
    if _turso:
        return (await _turso.execute(sql, params)).rowcount > 0
    conn = await _get()
    cur = await conn.execute(sql, params)
    await conn.commit()
    return cur.rowcount > 0


async def premium_history(telegram_id: int, limit: int = 5):
    return await _fetchall(
        "SELECT action, plan, days, amount, created_at FROM premium_log WHERE telegram_id = ? ORDER BY id DESC LIMIT ?",
        (telegram_id, limit),
    )


async def count_checks(telegram_id: int, kind: str, since: str | None = None) -> int:
    """Sarflangan limit (kind: speaking | writing) - MOCK bo'yicha: davrda ochilgan har bir mock 1 ta (uning
    qismlari, full mock va qayta ishlashlar qo'shimcha olinmaydi). Mockga kirmagan eski testlarda - har
    tekshiruv 1 ta; o'zi yaratgan (eski) shaxsiy testlar - yaratilganda 1 ta.
    since berilsa (UTC "YYYY-MM-DD HH:MM:SS") - shu vaqtdan beri, aks holda shu kalendar oyda (UTC+5)."""
    table = "attempts" if kind == "speaking" else "writing_submissions"
    extra = (since,) if since else ()
    row = await _fetchone(
        f"""SELECT
              (SELECT COUNT(DISTINCT CASE WHEN e.mock_id IS NOT NULL THEN 'm' || e.mock_id ELSE 'x' || t.id END)
                 FROM {table} t LEFT JOIN exams e ON e.id = t.exam_id
                WHERE t.telegram_id = ? AND t.status = 'done'
                  AND (e.owner_id IS NULL OR e.owner_id != t.telegram_id) AND {_period('t.created_at', since)})
            + (SELECT COUNT(*) FROM exams x
                WHERE x.owner_id = ? AND COALESCE(x.kind, 'speaking') = ? AND {_period('x.created_at', since)}) AS c""",
        (telegram_id, *extra, telegram_id, kind, *extra),
    )
    return row["c"]


def _period(col: str, since: str | None) -> str:
    return (f"{col} >= ?" if since else
            f"strftime('%Y-%m', {col}, '+5 hours') = strftime('%Y-%m', 'now', '+5 hours')")


async def mock_used(telegram_id: int, kind: str, mock_id: int, since: str | None = None) -> bool:
    """Bu mock shu davrda allaqachon ochilganmi (limit olingan) - qayta ishlash / boshqa qismi bepul."""
    table = "attempts" if kind == "speaking" else "writing_submissions"
    row = await _fetchone(
        f"""SELECT 1 AS ok FROM {table} t JOIN exams e ON e.id = t.exam_id
            WHERE t.telegram_id = ? AND t.status = 'done' AND e.mock_id = ? AND {_period('t.created_at', since)}
            LIMIT 1""",
        (telegram_id, mock_id, *((since,) if since else ())),
    )
    return bool(row)


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
             (SELECT COUNT(*) FROM premium_log WHERE action = 'grant' AND created_at >= datetime('now', '-30 days')) AS grants_month,
             (SELECT COUNT(*) FROM premium WHERE until > datetime('now') AND plan = 'standard') AS standard,
             (SELECT COUNT(*) FROM premium WHERE until > datetime('now') AND plan = 'pro') AS pro,
             (SELECT COUNT(*) FROM premium WHERE until > datetime('now') AND plan = 'vip') AS vip,
             (SELECT COUNT(*) FROM mocks) AS mocks"""
    )  # bitta so'rov: admin / Boss paneli tez ochilsin
    row["premium"] = row["standard"] + row["pro"] + row["vip"]
    return row


# ---------- Sozlamalar (xotirada keshlanadi - har safar bazaga bormaydi) ----------

async def load_settings():
    global _settings
    rows = await _fetchall("SELECT key, value FROM settings")
    _settings = {r["key"]: r["value"] or "" for r in rows}


def setting(key: str, default: str = "") -> str:
    """Keshdan sinxron o'qish (init_db / set_setting keshni yangilab turadi)."""
    return _settings.get(key) or default


def admin_contact() -> str:
    """O'quvchilar yozadigan admin (@username): Boss sozlamasi, bo'lmasa Render'dagi ADMIN_CONTACT."""
    custom = setting("admin_contact").strip().lstrip("@")
    return "@" + custom if custom else config.ADMIN_CONTACT


def admin_link() -> str:
    return "https://t.me/" + admin_contact().lstrip("@")


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
    """audience: all | free | standard | pro | vip | premium"""
    now = _now().strftime(_FMT)
    base = ("SELECT u.telegram_id FROM users u LEFT JOIN premium p ON p.telegram_id = u.telegram_id AND p.until > ? "
            "WHERE COALESCE(u.blocked, 0) = 0 AND u.full_name IS NOT NULL")  # botga yozmagan (faqat bloklangan) - yo'q
    cond = {
        "all": "",
        "free": " AND p.telegram_id IS NULL",
        "standard": " AND p.plan = 'standard'",
        "pro": " AND p.plan = 'pro'",
        "vip": " AND p.plan = 'vip'",
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
