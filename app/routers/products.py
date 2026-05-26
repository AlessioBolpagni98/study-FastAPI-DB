"""
Endpoint REST per la risorsa "Product" — versione async/production.

Concorrenza in questo router
-----------------------------
Tutti gli endpoint sono `async def`: FastAPI li esegue nell'event loop
senza delegarli a un thread. Le query DB non bloccano più il loop perché
`database.*` usa AsyncSession + asyncpg sotto il cofano.

Pattern avanzati di concorrenza
---------------------------------
1. asyncio.gather()    → più I/O in parallelo (endpoint /compare)
2. asyncio.Semaphore   → limita operazioni costose concorrenti (restock)
3. asyncio.wait_for()  → timeout su task lenti (restock background)
4. StreamingResponse   → streaming NDJSON token-by-token
5. BackgroundTasks     → risposta immediata, elaborazione asincrona
"""

import asyncio
import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import database, jobs
from app.database import AsyncSessionLocal, get_db
from app.models import ProductCreate, ProductRead, ProductUpdate, RestockPayload

router = APIRouter(prefix="/products", tags=["products"])

# ---------------------------------------------------------------------------
# Semaphore — rate limiting sui task di restock
# ---------------------------------------------------------------------------
# In produzione ogni restock simula una chiamata a un servizio esterno lento
# (ERP, fornitore, warehouse API). Se arrivano 1000 richieste concorrenti,
# aprire 1000 connessioni esterne contemporaneamente può sovraccaricare il
# servizio esterno o il DB.
#
# `asyncio.Semaphore(N)` permette al massimo N coroutine di entrare nel
# blocco `async with` contemporaneamente. Le altre aspettano senza bloccare
# l'event loop — sono semplicemente "in pausa" finché un posto si libera.
_RESTOCK_CONCURRENCY = 5          # task di restock simultanei massimi
_RESTOCK_TIMEOUT_SEC = 30.0       # timeout per singolo task (costante per testability)
_restock_semaphore = asyncio.Semaphore(_RESTOCK_CONCURRENCY)


# ---------------------------------------------------------------------------
# GET /products/export/stream  →  catalogo in streaming (NDJSON)
# ---------------------------------------------------------------------------
# NOTA: va definito PRIMA di /{product_id} e di /compare per evitare
# ambiguità nel routing (anche se FastAPI gestisce i path literal prima
# degli int param, per chiarezza li mettiamo in cima).
@router.get("/export/stream", response_class=StreamingResponse)
async def stream_products(db: AsyncSession = Depends(get_db)):
    """
    Restituisce il catalogo prodotto per prodotto in formato NDJSON.

    Ogni riga della risposta è un JSON valido separato da `\\n`.
    Il delay di 400ms tra un prodotto e l'altro simula la latenza
    di generazione token da un modello LLM.

    **Prova con curl:**
    ```
    curl -N http://127.0.0.1:8000/products/export/stream
    ```
    """
    products = await database.list_all(db)

    async def generate():
        for product in products:
            yield json.dumps(
                {
                    "id": product.id,
                    "name": product.name,
                    "description": product.description,
                    "price": product.price,
                    "category": product.category,
                    "stock": product.stock,
                },
                ensure_ascii=False,
            ) + "\n"
            # Simula latenza di produzione chunk (token LLM, riga CSV, ecc.)
            await asyncio.sleep(0.4)

    return StreamingResponse(generate(), media_type="application/x-ndjson")


# ---------------------------------------------------------------------------
# GET /products/compare  →  fetch parallelo con asyncio.gather()
# ---------------------------------------------------------------------------
@router.get("/compare", response_model=list[ProductRead])
async def compare_products(
    ids: list[int] = Query(
        ...,
        description="Lista di ID da confrontare. Es: ?ids=1&ids=2&ids=3",
        min_length=2,
    ),
):
    """
    Recupera più prodotti **in parallelo** usando `asyncio.gather()`.

    ### Perché gather() invece di query sequenziali?

    Con query sequenziali:
    ```
    t_totale = t_query_1 + t_query_2 + ... + t_query_N
    ```
    Con `asyncio.gather()`:
    ```
    t_totale = max(t_query_1, t_query_2, ..., t_query_N)
    ```

    Ogni query usa una propria sessione DB (una singola `AsyncSession` non
    è concurrency-safe). Le sessioni vengono prese dal pool in parallelo.

    **Uso tipico:** confrontare prezzi/stock di più prodotti, arricchire
    una lista con dati da risorse correlate, fan-out verso microservizi.
    """
    products = await database.get_many(ids)
    missing = [ids[i] for i, p in enumerate(products) if p is None]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prodotti non trovati: {missing}",
        )
    return products


# ---------------------------------------------------------------------------
# Background task di restock — con Semaphore e wait_for
# ---------------------------------------------------------------------------
async def _do_restock(job_id: str, product_id: int, quantity: int) -> None:
    """
    Task eseguito in background dopo che la response 202 è già partita.

    Pattern di produzione:
    ----------------------
    1. `asyncio.wait_for(timeout=...)` — se il task non finisce in N secondi,
       viene cancellato e il job viene marcato "failed". Previene task zombie
       che occupano slot nel semaphore per sempre.

    2. `_restock_semaphore` — al massimo `_RESTOCK_CONCURRENCY` restock
       girano contemporaneamente. Gli altri aspettano senza bloccare il loop.

    3. Sessione dedicata — la sessione iniettata via Depends è già chiusa
       quando il background task parte. Il task apre la propria sessione.
    """
    try:
        # wait_for annulla il coroutine interno se supera il timeout.
        # Python 3.11+ ha anche `asyncio.timeout()` come context manager.
        await asyncio.wait_for(
            _restock_task(job_id, product_id, quantity),
            timeout=_RESTOCK_TIMEOUT_SEC,   # costante: modificabile nei test
        )
    except TimeoutError:
        jobs.fail_job(job_id, f"Restock {product_id} timeout dopo 30s")
    except Exception as exc:
        jobs.fail_job(job_id, str(exc))


async def _restock_task(job_id: str, product_id: int, quantity: int) -> None:
    """Corpo del restock protetto dal semaphore."""
    # Il semaphore garantisce che al massimo _RESTOCK_CONCURRENCY task
    # eseguano questa sezione contemporaneamente. Gli altri awaittano
    # senza consumare CPU né connessioni DB.
    async with _restock_semaphore:
        # Simula latenza di una chiamata a servizio esterno (ERP, fornitore).
        await asyncio.sleep(3)

        async with AsyncSessionLocal() as db:
            product = await database.get(db, product_id)
            if product is None:
                jobs.fail_job(job_id, f"Product {product_id} not found during restock")
                return
            new_stock = product.stock + quantity
            await database.update(db, product_id, {"stock": new_stock})

    jobs.complete_job(job_id, {"product_id": product_id, "new_stock": new_stock})


@router.post(
    "/{product_id}/restock",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["products"],
)
async def restock_product(
    product_id: int,
    payload: RestockPayload,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Avvia un rifornimento di stock in background.

    Risponde subito con **202 Accepted** e un `job_id`.

    Il task in background è limitato da un `Semaphore` (max
    **5 restock concorrenti**) e da un timeout di **30 secondi**.

    Monitora l'avanzamento:
    ```
    GET /jobs/{job_id}
    ```
    """
    if await database.get(db, product_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found",
        )
    job_id = jobs.create_job()
    background_tasks.add_task(_do_restock, job_id, product_id, payload.quantity)
    return {
        "job_id": job_id,
        "status": "pending",
        "concurrency_limit": _RESTOCK_CONCURRENCY,
    }


# ---------------------------------------------------------------------------
# GET /products  →  lista (con filtri opzionali)
# ---------------------------------------------------------------------------
@router.get("", response_model=list[ProductRead])
async def list_products(
    category: str | None = Query(default=None, description="Filtra per categoria"),
    min_price: float | None = Query(default=None, ge=0),
    max_price: float | None = Query(default=None, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Ritorna la lista dei prodotti, eventualmente filtrata."""
    return await database.list_all(
        db, category=category, min_price=min_price, max_price=max_price
    )


# ---------------------------------------------------------------------------
# GET /products/{product_id}  →  dettaglio singolo
# ---------------------------------------------------------------------------
@router.get("/{product_id}", response_model=ProductRead)
async def get_product(product_id: int, db: AsyncSession = Depends(get_db)):
    product = await database.get(db, product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found",
        )
    return product


# ---------------------------------------------------------------------------
# POST /products  →  crea un nuovo prodotto
# ---------------------------------------------------------------------------
@router.post("", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
async def create_product(payload: ProductCreate, db: AsyncSession = Depends(get_db)):
    return await database.create(db, payload.model_dump())


# ---------------------------------------------------------------------------
# PUT /products/{product_id}  →  sostituzione completa
# ---------------------------------------------------------------------------
@router.put("/{product_id}", response_model=ProductRead)
async def replace_product(
    product_id: int, payload: ProductCreate, db: AsyncSession = Depends(get_db)
):
    updated = await database.replace(db, product_id, payload.model_dump())
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found",
        )
    return updated


# ---------------------------------------------------------------------------
# PATCH /products/{product_id}  →  aggiornamento parziale
# ---------------------------------------------------------------------------
@router.patch("/{product_id}", response_model=ProductRead)
async def update_product(
    product_id: int, payload: ProductUpdate, db: AsyncSession = Depends(get_db)
):
    patch = payload.model_dump(exclude_unset=True)
    updated = await database.update(db, product_id, patch)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found",
        )
    return updated


# ---------------------------------------------------------------------------
# DELETE /products/{product_id}  →  204 No Content
# ---------------------------------------------------------------------------
@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(product_id: int, db: AsyncSession = Depends(get_db)):
    if not await database.delete(db, product_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found",
        )
