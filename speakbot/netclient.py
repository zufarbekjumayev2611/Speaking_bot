"""Bitta umumiy aiohttp sessiyasi: har so'rovda yangi TLS ulanish ochmaslik uchun (Groq/Claude/Whisper chaqiruvlari tezlashadi)."""
import aiohttp

_session: aiohttp.ClientSession | None = None


def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=50, keepalive_timeout=60))
    return _session


async def close_session():
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None
