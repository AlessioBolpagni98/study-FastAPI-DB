"""
Punto di ingresso dell'applicazione FastAPI.

Cosa succede qui
----------------
1. Istanziamo l'oggetto `FastAPI`: è "l'applicazione" — registra le rotte,
   gestisce il ciclo di vita, espone lo schema OpenAPI.
2. Il `lifespan` esegue codice di setup all'avvio e teardown allo spegnimento.
3. Definiamo due endpoint "globali" (root e health check).
4. Includiamo il router dei prodotti: tutti i suoi endpoint vengono
   "agganciati" all'app principale sotto il prefisso /products.

Come avviarlo
-------------
Dalla cartella del progetto, con il virtualenv attivo:

    fastapi dev app/main.py

Poi apri http://127.0.0.1:8000/docs per la Swagger UI interattiva.
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db, seed_db
from app.routers import jobs, products


# ---------------------------------------------------------------------------
# Lifespan: setup e teardown dell'applicazione
# ---------------------------------------------------------------------------
# Il `lifespan` è un context manager asincrono che FastAPI esegue:
#   - Il codice PRIMA del `yield` → all'avvio (prima di ricevere richieste).
#   - Il codice DOPO il `yield`   → allo spegnimento (dopo l'ultima richiesta).
#
# È il posto giusto per:
#   - Inizializzare connessioni al DB, pool, client esterni.
#   - Precaricare modelli ML in memoria.
#   - Liberare risorse alla chiusura.
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Avvio: inserisce i prodotti di esempio se la tabella è vuota.
    with SessionLocal() as db:
        seed_db(db)
    yield
    # Spegnimento: nulla da fare esplicitamente — il connection pool
    # di SQLAlchemy si chiude automaticamente quando il processo termina.


# I parametri `title`, `description`, `version` finiscono nella
# Swagger UI e nello schema OpenAPI: documentano l'API per chi la usa.
app = FastAPI(
    title="Catalogo Prodotti — Demo FastAPI + PostgreSQL",
    description=(
        "API REST didattica per imparare i concetti fondamentali di FastAPI: "
        "metodi HTTP, status code, Pydantic, gestione errori, SQLAlchemy ORM."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

# "Monta" i router. Da questo momento tutti gli endpoint definiti nei
# router sono raggiungibili sotto i rispettivi prefissi.
app.include_router(products.router)
app.include_router(jobs.router)


@app.get("/", tags=["meta"])
def root():
    """Endpoint di benvenuto. Utile per verificare al volo che il server sia vivo."""
    return {
        "message": "Benvenuto! Vai su /docs per la documentazione interattiva.",
        "docs_url": "/docs",
        "openapi_url": "/openapi.json",
    }


@app.get("/health", tags=["meta"])
def health(db: Session = Depends(get_db)):
    """Health check con verifica della connessione al database.

    Esegue `SELECT 1`: la query più leggera possibile per verificare che
    PostgreSQL sia raggiungibile. Se il DB è down, l'eccezione si propaga
    e FastAPI risponde con 500 Internal Server Error.

    Pattern standard usato da load balancer e orchestratori (es. Kubernetes)
    per sapere se il servizio è pronto a ricevere traffico.
    """
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}
