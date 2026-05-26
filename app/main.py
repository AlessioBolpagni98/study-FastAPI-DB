"""
Punto di ingresso dell'applicazione FastAPI — versione async/production.

Cosa succede qui
----------------
1. Il `lifespan` asincrono crea le tabelle e inserisce i seed all'avvio.
2. Gli endpoint globali (root, health) sono `async def`: non bloccano l'event loop.
3. Il health check usa `AsyncSession` per verificare il DB senza blocking I/O.

Come avviarlo
-------------
    fastapi dev app/main.py

Poi apri http://127.0.0.1:8000/docs per la Swagger UI interattiva.
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import database
from app.database import AsyncSessionLocal, get_db
from app.routers import jobs, products


# ---------------------------------------------------------------------------
# Lifespan asincrono: setup e teardown dell'applicazione
# ---------------------------------------------------------------------------
# Il lifespan è un asynccontextmanager: FastAPI lo esegue all'avvio (codice
# prima del yield) e allo spegnimento (codice dopo il yield).
#
# Con il layer async, le operazioni di startup devono essere anch'esse async:
# - `database.init_db()` crea le tabelle via AsyncConnection
# - `seed_db()` inserisce i dati via AsyncSession
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- AVVIO ---
    # 1. Crea le tabelle DDL se non esistono ancora.
    #    (Non si può fare a livello di modulo con async engine.)
    await database.init_db()

    # 2. Inserisce i prodotti di esempio se la tabella è vuota.
    async with AsyncSessionLocal() as db:
        await database.seed_db(db)

    yield

    # --- SPEGNIMENTO ---
    # Il connection pool di SQLAlchemy async si chiude automaticamente
    # quando il processo termina. Nulla da fare esplicitamente.
    #
    # In produzione, qui si chiuderebbero eventuali client HTTP aperti
    # (httpx.AsyncClient, aiohttp.ClientSession, ecc.).


app = FastAPI(
    title="Catalogo Prodotti — Demo FastAPI Async",
    description=(
        "API REST didattica per imparare i concetti fondamentali di FastAPI: "
        "metodi HTTP, status code, Pydantic, gestione errori, SQLAlchemy Async ORM, "
        "asyncio.gather(), Semaphore, wait_for(), StreamingResponse."
    ),
    version="0.3.0",
    lifespan=lifespan,
)

app.include_router(products.router)
app.include_router(jobs.router)


# ---------------------------------------------------------------------------
# Endpoint di meta
# ---------------------------------------------------------------------------

@app.get("/", tags=["meta"])
async def root():
    """Endpoint di benvenuto — async perché non fa I/O ma è coerente."""
    return {
        "message": "Benvenuto! Vai su /docs per la documentazione interattiva.",
        "docs_url": "/docs",
        "openapi_url": "/openapi.json",
    }


@app.get("/health", tags=["meta"])
async def health(db: AsyncSession = Depends(get_db)):
    """Health check con verifica asincrona della connessione al database.

    `await db.execute(text("SELECT 1"))` non blocca l'event loop:
    se il DB risponde lentamente, FastAPI può continuare a servire
    altre richieste nel frattempo.

    Pattern standard per load balancer e orchestratori Kubernetes:
    il readiness probe chiama questo endpoint; se risponde 200, il pod
    è pronto a ricevere traffico.
    """
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}
