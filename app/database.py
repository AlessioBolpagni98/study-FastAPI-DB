"""
Layer di accesso al database — PostgreSQL via SQLAlchemy Async.

Perché async?
-------------
FastAPI gira su un event loop asyncio (via Uvicorn/Starlette). Quando un
endpoint fa I/O sincrono (query DB, HTTP call, lettura file), **blocca
l'intero event loop**: nessun'altra richiesta può essere servita finché
quella operazione non finisce. Con migliaia di utenti concorrenti, questo
è un collo di bottiglia critico.

La soluzione è usare driver e sessioni async: l'event loop "sospende"
il coroutine che aspetta la risposta del DB e serve nel frattempo altre
richieste — le riprende non appena il DB risponde.

Stack async usato
-----------------
- `asyncpg`                  : driver PostgreSQL nativo async (sostituisce psycopg2)
- `create_async_engine`      : engine che usa asyncpg sotto il cofano
- `AsyncSession`             : sessione SQLAlchemy che non blocca l'event loop
- `async_sessionmaker`       : factory di sessioni async
- `asyncio.gather()`         : esecuzione parallela di più query indipendenti

Struttura del file
------------------
A. Engine & AsyncSessionLocal  — connessione al DB e factory delle sessioni.
B. init_db()                   — crea le tabelle all'avvio (sostituisce create_all
                                 a livello di modulo, che non funziona con async).
C. get_db()                    — dependency FastAPI: sessione per ogni request.
D. Funzioni CRUD (async)       — stessa interfaccia di prima, ora con await.
E. get_many()                  — fetch parallelo con asyncio.gather().
F. seed_db()                   — dati di esempio all'avvio.
"""

import asyncio
import os
from collections.abc import AsyncGenerator

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.orm import Base, ProductORM

# ---------------------------------------------------------------------------
# A. Engine & AsyncSessionLocal
# ---------------------------------------------------------------------------

load_dotenv()

_raw_url = os.environ["DATABASE_URL"]

# asyncpg richiede lo schema `postgresql+asyncpg://`.
# Supportiamo sia `postgresql://` che `postgresql+asyncpg://` nel .env.
DATABASE_URL = (
    _raw_url
    .replace("postgresql://", "postgresql+asyncpg://", 1)
    .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
)

# `create_async_engine` crea un engine asincrono: le query restituiscono
# awaitable invece di bloccare il thread corrente.
#
# --- Parametri chiave per la produzione ---
#
# pool_size=20
#   Connessioni aperte in modo permanente nel pool. Ogni connessione può
#   servire una sola query alla volta; con 20 connessioni si gestiscono 20
#   query DB concorrenti senza overhead di apertura.
#
# max_overflow=10
#   Connessioni extra consentite quando il pool è pieno. Totale massimo:
#   20 + 10 = 30 connessioni simultanee. PostgreSQL di default accetta 100.
#
# pool_timeout=30
#   Secondi di attesa per una connessione libera prima di sollevare
#   TimeoutError. Evita che le richieste restino in coda indefinitamente.
#
# pool_recycle=1800
#   Ricicla le connessioni ogni 30 minuti. Previene errori "connection
#   closed" causati da firewall/NAT che terminano connessioni TCP idle.
#
# pool_pre_ping=True
#   Prima di usare una connessione dal pool, esegue `SELECT 1` per
#   verificare che sia ancora viva. Se è morta, ne apre una nuova.
#   Costo: ~1 ms, ma elimina gli errori "broken pipe" intermittenti.
engine = create_async_engine(
    DATABASE_URL,
    echo=True,           # In produzione: echo=False (o logging configurato)
    pool_size=20,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800,
    pool_pre_ping=True,
)

# `async_sessionmaker` è l'equivalente async di `sessionmaker`.
#
# expire_on_commit=False — CRITICO per le sessioni async.
#   Con SQLAlchemy sync, dopo db.commit() gli attributi degli oggetti ORM
#   vengono "scaduti" (expired): al prossimo accesso SQLAlchemy li ricarica
#   con una query. In modalità async questo caricamento implicito
#   NON è possibile (richiederebbe un await invisibile). Con
#   expire_on_commit=False gli attributi rimangono validi dopo il commit,
#   evitando MissingGreenlet / lazy-load errors.
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# ---------------------------------------------------------------------------
# B. init_db — crea le tabelle all'avvio
# ---------------------------------------------------------------------------

async def init_db() -> None:
    """Crea le tabelle DDL se non esistono.

    Con un engine asincrono non si può chiamare `Base.metadata.create_all`
    a livello di modulo (blocca il thread). Si usa invece `engine.begin()`
    che restituisce una AsyncConnection e `conn.run_sync()` che esegue
    funzioni sync nel contesto asincrono.

    Chiamata dal lifespan di main.py prima dello yield.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ---------------------------------------------------------------------------
# C. Dependency FastAPI
# ---------------------------------------------------------------------------

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency che fornisce una AsyncSession per ogni request.

    `async with AsyncSessionLocal() as session` gestisce automaticamente:
    - apertura della sessione (prende una connessione dal pool)
    - chiusura della sessione a fine request (restituisce al pool)
    - rollback in caso di eccezione non gestita

    Nei router si usa così:
        async def my_endpoint(db: AsyncSession = Depends(get_db)):
            result = await database.get(db, 42)
    """
    async with AsyncSessionLocal() as session:
        yield session


# ---------------------------------------------------------------------------
# D. Funzioni CRUD (async)
# ---------------------------------------------------------------------------

async def list_all(
    db: AsyncSession,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
) -> list[ProductORM]:
    """SELECT con filtri opzionali — non blocca l'event loop."""
    stmt = select(ProductORM)
    if category is not None:
        stmt = stmt.where(ProductORM.category == category)
    if min_price is not None:
        stmt = stmt.where(ProductORM.price >= min_price)
    if max_price is not None:
        stmt = stmt.where(ProductORM.price <= max_price)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get(db: AsyncSession, product_id: int) -> ProductORM | None:
    """SELECT per PK. Usa l'identity map se l'oggetto è già in sessione."""
    return await db.get(ProductORM, product_id)


async def create(db: AsyncSession, data: dict) -> ProductORM:
    """INSERT — db.add() è sync, commit/refresh sono async."""
    product = ProductORM(**data)
    db.add(product)
    await db.commit()
    # refresh rilegge la riga dal DB: necessario per ottenere l'id
    # assegnato da PostgreSQL (SERIAL/IDENTITY).
    await db.refresh(product)
    return product


async def replace(db: AsyncSession, product_id: int, data: dict) -> ProductORM | None:
    """UPDATE completo (tutti i campi)."""
    product = await db.get(ProductORM, product_id)
    if product is None:
        return None
    for key, value in data.items():
        setattr(product, key, value)
    await db.commit()
    await db.refresh(product)
    return product


async def update(db: AsyncSession, product_id: int, patch: dict) -> ProductORM | None:
    """UPDATE parziale — `patch` contiene solo i campi da modificare."""
    product = await db.get(ProductORM, product_id)
    if product is None:
        return None
    for key, value in patch.items():
        setattr(product, key, value)
    await db.commit()
    await db.refresh(product)
    return product


async def delete(db: AsyncSession, product_id: int) -> bool:
    """DELETE per PK."""
    product = await db.get(ProductORM, product_id)
    if product is None:
        return False
    await db.delete(product)
    await db.commit()
    return True


# ---------------------------------------------------------------------------
# E. Fetch parallelo con asyncio.gather()
# ---------------------------------------------------------------------------

async def get_many(product_ids: list[int]) -> list[ProductORM | None]:
    """Recupera più prodotti in PARALLELO usando asyncio.gather().

    Perché sessioni separate?
    -------------------------
    Una singola AsyncSession non è concurrency-safe: non può essere usata
    da più coroutine contemporaneamente. Per parallelizzare le query, ogni
    fetch apre la propria sessione dal pool — ogni sessione ottiene una
    connessione indipendente.

    Con asyncio.gather() le N query partono simultaneamente e si aspettano
    che finiscano tutte: il tempo totale è max(t1, t2, ..., tN) invece
    di sum(t1, t2, ..., tN).

    In produzione questo pattern è usato per:
    - fetch di risorse correlate da tabelle diverse
    - chiamate a più microservizi in parallelo
    - enrichment concorrente (es. prezzi + disponibilità + recensioni)
    """
    async def _fetch_one(pid: int) -> ProductORM | None:
        # Ogni task ha la propria sessione → connessione dal pool indipendente.
        async with AsyncSessionLocal() as session:
            return await session.get(ProductORM, pid)

    results = await asyncio.gather(*(_fetch_one(pid) for pid in product_ids))
    return list(results)


# ---------------------------------------------------------------------------
# F. Seed — dati di esempio all'avvio
# ---------------------------------------------------------------------------

async def seed_db(db: AsyncSession) -> None:
    """Inserisce 3 prodotti di esempio solo se la tabella è vuota.

    Chiamata da main.py nel lifespan (all'avvio del server).
    """
    result = await db.execute(select(ProductORM))
    if result.first() is not None:
        return
    for data in [
        {
            "name": "Libro: Clean Code",
            "description": "Robert C. Martin",
            "price": 29.90,
            "category": "libri",
            "stock": 7,
        },
        {
            "name": "Mouse wireless",
            "description": "Ergonomico, ricaricabile",
            "price": 24.50,
            "category": "informatica",
            "stock": 30,
        },
        {
            "name": "Tazza in ceramica",
            "description": None,
            "price": 9.00,
            "category": "casa",
            "stock": 100,
        },
    ]:
        await create(db, data)
