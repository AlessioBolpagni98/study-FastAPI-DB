"""
Fixtures condivise per la test suite.

Strategia di test
-----------------
Non si usa PostgreSQL nei test: si usa SQLite in-memory tramite `aiosqlite`.
SQLite supporta lo stesso dialetto SQL usato dal codice ORM e permette
test veloci, isolati e senza dipendenze esterne.

Override delle dipendenze
--------------------------
FastAPI espone `app.dependency_overrides` per sostituire le dipendenze
(come `get_db`) con versioni di test. Facciamo lo stesso con le variabili
di modulo (`AsyncSessionLocal`, `engine`) usate direttamente in
`get_many()` e nel background task di restock.

Fixture hierarchy
-----------------
    test_db_url  →  engine (async, crea tabelle)
                        ↓
                 session_factory (async_sessionmaker sul test engine)
                        ↓
                 client (AsyncClient HTTP + override dipendenze + seed)
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.database as db_module
import app.routers.products as prod_module
from app import database
from app.main import app
from app.orm import Base


# ---------------------------------------------------------------------------
# DB URL — SQLite per test, un file diverso per ogni test (isolamento totale)
# ---------------------------------------------------------------------------

@pytest.fixture
def test_db_url(tmp_path):
    """Percorso univoco per ogni test: garantisce isolamento completo."""
    db_file = tmp_path / "test.db"
    return f"sqlite+aiosqlite:///{db_file}"


# ---------------------------------------------------------------------------
# Engine — crea le tabelle sul DB di test
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def engine(test_db_url):
    """Async engine SQLite per test. Crea le tabelle e le distrugge a fine test."""
    eng = create_async_engine(
        test_db_url,
        echo=False,
        # SQLite non supporta pool_size/max_overflow: usiamo i default
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield eng

    # Teardown: distrugge le tabelle e chiude tutte le connessioni
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


# ---------------------------------------------------------------------------
# Session factory — async_sessionmaker sul test engine
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def session_factory(engine):
    """Factory di sessioni che punta al DB di test."""
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


# ---------------------------------------------------------------------------
# Client HTTP — override di tutte le dipendenze + seed dei dati
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client(engine, session_factory, monkeypatch):
    """
    AsyncClient HTTP preconfigurato con:
    - DB di test (SQLite) al posto di PostgreSQL
    - 3 prodotti di esempio già inseriti
    - dipendenze FastAPI e variabili di modulo patched

    Cosa viene patched e perché
    ---------------------------
    db_module.engine
        Usato da `database.init_db()` nel lifespan. Deve puntare al test DB
        affinché `create_all` non tenti di connettersi a PostgreSQL.

    db_module.AsyncSessionLocal
        Usato direttamente in `database.get_many()` e nel background task
        di restock. Non passa per `Depends(get_db)`, quindi va patchato
        esplicitamente come variabile di modulo.

    prod_module.AsyncSessionLocal
        `products.py` importa AsyncSessionLocal con `from app.database import ...`,
        creando una copia locale. Va patchata separatamente.

    app.dependency_overrides[database.get_db]
        Sostituisce la dependency iniettata via `Depends(get_db)` in tutti
        gli endpoint con una versione che usa la sessione di test.
    """
    # -- Patch variabili di modulo -------------------------------------------
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(prod_module, "AsyncSessionLocal", session_factory)

    # -- Override dependency FastAPI -----------------------------------------
    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[database.get_db] = override_get_db

    # -- Seed: 3 prodotti di esempio -----------------------------------------
    # Inseriamo i dati PRIMA di avviare il client (il lifespan troverà la
    # tabella non vuota e salterà il seed di produzione).
    async with session_factory() as db:
        for data in [
            {
                "name": "Prodotto A",
                "description": "Primo prodotto di test",
                "price": 10.0,
                "category": "test",
                "stock": 5,
            },
            {
                "name": "Prodotto B",
                "description": "Secondo prodotto di test",
                "price": 20.0,
                "category": "test",
                "stock": 10,
            },
            {
                "name": "Prodotto C",
                "description": "Terzo prodotto",
                "price": 30.0,
                "category": "altro",
                "stock": 15,
            },
        ]:
            await database.create(db, data)

    # -- Client HTTP ----------------------------------------------------------
    # ASGITransport fa girare l'app in-process (niente rete reale).
    # Il `async with` triggera il lifespan: init_db (no-op, tabelle esistono)
    # e seed_db (no-op, prodotti già inseriti).
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    # -- Cleanup --------------------------------------------------------------
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Fixture di convenienza: IDs dei prodotti seedati
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def product_ids(session_factory) -> list[int]:
    """Restituisce gli ID dei 3 prodotti seedati in ordine."""
    async with session_factory() as db:
        products = await database.list_all(db)
        return [p.id for p in products]
