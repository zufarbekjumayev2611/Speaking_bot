"""Turso (libSQL) bazasi bilan HTTP orqali ishlash (Hrana "v2/pipeline" API) - qo'shimcha kutubxonasiz.

Render'da ma'lumotlar o'chib ketmasligi uchun: TURSO_DATABASE_URL (libsql://<baza>.turso.io) va
TURSO_AUTH_TOKEN berilsa, bot lokal SQLite fayl o'rniga Turso'dan foydalanadi."""
import asyncio
import base64
import json
import logging

import aiohttp

from netclient import get_session


class TursoError(RuntimeError):
    pass


class TursoUnavailable(TursoError):
    """Server vaqtincha javob bermadi (5xx) - qayta urinish mumkin."""


def _http_url(url: str) -> str:
    url = url.strip().rstrip("/")
    for prefix in ("libsql://", "wss://", "ws://"):
        if url.startswith(prefix):
            return "https://" + url[len(prefix):]
    return url if url.startswith("http") else "https://" + url


def _arg(value) -> dict:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    if isinstance(value, (bytes, bytearray)):
        return {"type": "blob", "base64": base64.b64encode(bytes(value)).decode()}
    return {"type": "text", "value": str(value)}


def _value(v: dict):
    t = v.get("type")
    if t == "null":
        return None
    if t == "integer":
        return int(v["value"])
    if t == "float":
        return float(v["value"])
    if t == "blob":
        return base64.b64decode(v.get("base64", ""))
    return v.get("value")


class Result:
    def __init__(self, data: dict):
        cols = [c.get("name") for c in data.get("cols", [])]
        self.rows = [dict(zip(cols, (_value(v) for v in row))) for row in data.get("rows", [])]
        rowid = data.get("last_insert_rowid")
        self.lastrowid = int(rowid) if rowid is not None else None
        self.rowcount = int(data.get("affected_row_count") or 0)


class TursoClient:
    def __init__(self, url: str, token: str):
        self.url = _http_url(url) + "/v2/pipeline"
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    async def batch(self, statements: list[tuple[str, tuple]]) -> list[Result]:
        reqs = [{"type": "execute", "stmt": {"sql": sql, "args": [_arg(a) for a in params]}} for sql, params in statements]
        reqs.append({"type": "close"})
        async with get_session().post(
            self.url, json={"requests": reqs}, headers=self.headers, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            raw = await resp.text()
            if resp.status >= 500:
                raise TursoUnavailable(f"Turso HTTP {resp.status}: {raw[:300]}")
            try:
                body = json.loads(raw)
            except ValueError:
                raise TursoError(f"Turso HTTP {resp.status}: javob JSON emas: {raw[:300]}") from None
            if resp.status != 200:
                raise TursoError(f"Turso HTTP {resp.status}: {str(body)[:300]}")
        out = []
        for r in body.get("results", [])[: len(statements)]:
            if r.get("type") != "ok":
                raise TursoError(f"Turso xatosi: {(r.get('error') or {}).get('message', r)}")
            out.append(Result(r["response"]["result"]))
        return out

    async def execute(self, sql: str, params=()) -> Result:
        # Tarmoq uzilishida bir marta qayta yuboriladi - faqat takrorlansa zarari yo'q so'rovlar
        # (o'qish, UPDATE/DELETE, ON CONFLICT bilan INSERT); oddiy INSERT ikki marta yozilmasin.
        head = sql.lstrip()[:6].upper()
        retries = 0 if head == "INSERT" and "ON CONFLICT" not in sql.upper() else 1
        for attempt in range(retries + 1):
            try:
                return (await self.batch([(sql, tuple(params))]))[0]
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError, TursoUnavailable):
                if attempt == retries:
                    raise
                logging.getLogger("turso").warning("Turso bilan aloqa uzildi, qayta urinilmoqda")
                await asyncio.sleep(0.5)
