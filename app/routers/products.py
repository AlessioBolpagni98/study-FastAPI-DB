"""
Endpoint REST per la risorsa "Product".

Cos'è un APIRouter?
-------------------
Un `APIRouter` raggruppa endpoint correlati. Ha gli stessi metodi di
`FastAPI` (`@router.get`, `@router.post`, ...) ma non avvia il server
da solo: viene "montato" nell'app principale con `app.include_router(...)`.

Vantaggi:
- Permette di organizzare il codice per risorsa (qui: prodotti).
- `prefix="/products"` evita di ripetere la stringa in ogni decoratore.
- `tags=["products"]` raggruppa gli endpoint nella sezione omonima di /docs.

Concetti REST applicati qui sotto
---------------------------------
- L'URL identifica una RISORSA (es. /products, /products/{id}), non un'azione.
- Il METODO HTTP esprime l'azione (GET legge, POST crea, PUT sostituisce,
  PATCH aggiorna parzialmente, DELETE elimina).
- Lo STATUS CODE esprime l'esito (200 OK, 201 Created, 204 No Content,
  404 Not Found, 422 dati invalidi, ecc.).

Pattern avanzati inclusi in questo router
-----------------------------------------
- StreamingResponse: risposta "a flusso", il server invia i dati man mano
  che li produce invece di aspettare di avere tutto il payload pronto.
  Stessa meccanica usata dai provider LLM (OpenAI, Anthropic) per lo
  streaming di token.
- BackgroundTasks: operazioni asincrone che partono dopo che la response
  è già stata inviata al client. Pattern chiave per wrappare inferenze AI
  o chiamate lente a servizi esterni.
"""

import asyncio
import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app import database, jobs
from app.database import SessionLocal, get_db
from app.models import ProductCreate, ProductRead, ProductUpdate, RestockPayload

router = APIRouter(prefix="/products", tags=["products"])


# -------------------------------------------------------------------------
# GET /products/export/stream  →  catalogo in streaming (NDJSON)
# -------------------------------------------------------------------------
# StreamingResponse invia i dati al client man mano che vengono prodotti,
# senza aspettare che il payload sia completo. Il formato NDJSON
# (Newline-Delimited JSON) è una convenzione: ogni riga è un JSON valido.
#
# Perché questo pattern è rilevante per un AI engineer?
# È esattamente ciò che fanno i provider LLM (Anthropic, OpenAI) quando
# fai una richiesta con stream=True: invece di aspettare che il modello
# generi l'intera risposta, il server invia un chunk alla volta man mano
# che i token vengono prodotti.
#
# NOTA: questo endpoint va definito PRIMA di /{product_id}.
# FastAPI tipizza {product_id} come int, quindi "export" non entrerebbe
# mai in conflitto — ma per chiarezza didattica teniamo l'ordine logico.
@router.get("/export/stream", response_class=StreamingResponse)
async def stream_products(db: Session = Depends(get_db)):
    """
    Restituisce il catalogo prodotto per prodotto in formato NDJSON.

    Ogni riga della risposta è un JSON valido separato da `\\n`.
    Il delay di 400ms tra un prodotto e l'altro simula la latenza
    di lettura da un DB o di generazione token da un modello.

    **Prova con curl:**
    ```
    curl -N http://127.0.0.1:8000/products/export/stream
    ```
    L'opzione `-N` disabilita il buffering di curl: vedrai i prodotti
    arrivare uno alla volta, non tutti insieme alla fine.
    """
    products = database.list_all(db)

    async def generate():
        for product in products:
            # Convertiamo l'oggetto ORM in dict per json.dumps.
            yield json.dumps({
                "id": product.id,
                "name": product.name,
                "description": product.description,
                "price": product.price,
                "category": product.category,
                "stock": product.stock,
            }, ensure_ascii=False) + "\n"
            # Simula la latenza di produzione di ogni "chunk"
            # (in un LLM reale: il tempo per generare N token).
            await asyncio.sleep(0.4)

    return StreamingResponse(generate(), media_type="application/x-ndjson")


# -------------------------------------------------------------------------
# POST /products/{product_id}/restock  →  rifornimento asincrono (202)
# -------------------------------------------------------------------------
# BackgroundTasks permette di eseguire una funzione DOPO che FastAPI ha
# già inviato la risposta al client. Il client riceve subito il 202 con
# un job_id e può fare polling su GET /jobs/{job_id} per sapere quando
# l'operazione è completata.
#
# Perché questo pattern è rilevante per un AI engineer?
# Qualsiasi chiamata a un modello AI è intrinsecamente lenta (latenza
# variabile, da secondi a minuti). Invece di tenere aperta la connessione
# HTTP, si accetta la richiesta subito e si processa in background.
async def _do_restock(job_id: str, product_id: int, quantity: int) -> None:
    """Task eseguito in background: aggiorna lo stock dopo un delay simulato.

    IMPORTANTE: questo coroutine gira DOPO che la request è terminata e
    la Session iniettata via Depends è già chiusa. Non può riutilizzarla.
    Apre quindi una sessione propria con `with SessionLocal() as db`.
    """
    try:
        # Simula una chiamata lenta a un servizio esterno (es. API fornitore).
        await asyncio.sleep(3)
        # Sessione dedicata al background task: ciclo di vita indipendente
        # dalla request originale.
        with SessionLocal() as db:
            product = database.get(db, product_id)
            if product is None:
                jobs.fail_job(job_id, f"Product {product_id} not found during restock")
                return
            # `.stock` è un attributo dell'istanza ORM, non una chiave dict.
            new_stock = product.stock + quantity
            database.update(db, product_id, {"stock": new_stock})
        jobs.complete_job(job_id, {"product_id": product_id, "new_stock": new_stock})
    except Exception as exc:
        jobs.fail_job(job_id, str(exc))


@router.post(
    "/{product_id}/restock",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["products"],
)
async def restock_product(
    product_id: int,
    payload: RestockPayload,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Avvia un rifornimento di stock in background.

    Risponde subito con **202 Accepted** e un `job_id`.
    Il rifornimento viene eseguito in background (delay simulato di 3s).

    Monitora l'avanzamento con:
    ```
    GET /jobs/{job_id}
    ```
    Lo status passerà da `pending` a `completed` (o `failed`).
    """
    if database.get(db, product_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found",
        )
    job_id = jobs.create_job()
    # `add_task` registra il coroutine: verrà eseguito dopo l'invio della risposta.
    background_tasks.add_task(_do_restock, job_id, product_id, payload.quantity)
    return {"job_id": job_id, "status": "pending"}


# -------------------------------------------------------------------------
# GET /products  →  lista (con filtri opzionali via query string)
# -------------------------------------------------------------------------
# `response_model=list[ProductRead]` dice a FastAPI:
#   - serializza la risposta secondo questo schema
#   - filtra eventuali campi extra che non sono nel modello
#   - documenta lo schema in OpenAPI
@router.get("", response_model=list[ProductRead])
def list_products(
    # I parametri NON dichiarati nel path diventano automaticamente
    # query parameters: /products?category=libri&min_price=5
    # `Query(...)` permette di aggiungere descrizioni e vincoli.
    category: str | None = Query(default=None, description="Filtra per categoria"),
    min_price: float | None = Query(default=None, ge=0),
    max_price: float | None = Query(default=None, ge=0),
    db: Session = Depends(get_db),
):
    """Ritorna la lista dei prodotti, eventualmente filtrata."""
    return database.list_all(db, category=category, min_price=min_price, max_price=max_price)


# -------------------------------------------------------------------------
# GET /products/{product_id}  →  dettaglio singolo
# -------------------------------------------------------------------------
# `{product_id}` nel path = path parameter. FastAPI lo passa come argomento
# alla funzione e — grazie al type hint `int` — lo converte da stringa a
# intero (e ritorna 422 se non è convertibile, es. /products/abc).
@router.get("/{product_id}", response_model=ProductRead)
def get_product(product_id: int, db: Session = Depends(get_db)):
    product = database.get(db, product_id)
    if product is None:
        # HTTPException ferma l'esecuzione e fa rispondere FastAPI con
        # lo status code indicato e un body JSON `{"detail": "..."}`.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found",
        )
    return product


# -------------------------------------------------------------------------
# POST /products  →  crea un nuovo prodotto
# -------------------------------------------------------------------------
# `status_code=201` perché POST che crea una risorsa deve ritornare 201,
# non 200. È una convenzione REST.
#
# Il parametro `payload: ProductCreate` indica a FastAPI che il body della
# richiesta deve essere validato contro `ProductCreate`. Se il client manda
# un JSON invalido (es. price negativo), FastAPI risponde 422 senza
# nemmeno entrare in questa funzione.
@router.post("", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate, db: Session = Depends(get_db)):
    # `model_dump()` converte il modello Pydantic in dict, che passiamo
    # alla funzione CRUD che costruirà l'istanza ProductORM.
    return database.create(db, payload.model_dump())


# -------------------------------------------------------------------------
# PUT /products/{product_id}  →  sostituzione completa
# -------------------------------------------------------------------------
# Differenza chiave da PATCH: PUT è IDEMPOTENTE e SOSTITUISCE l'intera
# risorsa. Il client deve mandare TUTTI i campi obbligatori.
@router.put("/{product_id}", response_model=ProductRead)
def replace_product(product_id: int, payload: ProductCreate, db: Session = Depends(get_db)):
    updated = database.replace(db, product_id, payload.model_dump())
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found",
        )
    return updated


# -------------------------------------------------------------------------
# PATCH /products/{product_id}  →  aggiornamento parziale
# -------------------------------------------------------------------------
@router.patch("/{product_id}", response_model=ProductRead)
def update_product(product_id: int, payload: ProductUpdate, db: Session = Depends(get_db)):
    # `exclude_unset=True` è IL trucco di PATCH:
    # include nel dict SOLO i campi che il client ha effettivamente inviato,
    # ignorando i default. Così possiamo aggiornare un singolo campo senza
    # azzerare gli altri.
    patch = payload.model_dump(exclude_unset=True)
    updated = database.update(db, product_id, patch)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found",
        )
    return updated


# -------------------------------------------------------------------------
# DELETE /products/{product_id}  →  204 No Content
# -------------------------------------------------------------------------
# Convenzione REST: una DELETE riuscita ritorna 204 e NESSUN body.
# Per questo non specifichiamo `response_model` e la funzione ritorna None.
@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: int, db: Session = Depends(get_db)):
    if not database.delete(db, product_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found",
        )
    # `return None` implicito → FastAPI invia una response vuota con 204.
