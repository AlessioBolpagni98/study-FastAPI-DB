# Concetti: API REST con FastAPI e Pydantic

Concetti trattati in questo progetto relativi al layer HTTP e alla struttura dell'API.

---

## Fondamenti HTTP

**HTTP** (HyperText Transfer Protocol) è il protocollo di comunicazione su cui si basano le API REST. Funziona come un dialogo **request/response** tra un client e un server:

1. Il client invia una **request** con un metodo, un URL, degli header opzionali e un body opzionale.
2. Il server elabora la richiesta e risponde con uno **status code**, degli header e un body opzionale.

### Caratteristiche fondamentali

| Proprietà | Significato |
|---|---|
| **Stateless** | Ogni richiesta è indipendente. Il server non ricorda le richieste precedenti — tutta l'informazione necessaria deve essere nella richiesta stessa. |
| **Client-server** | Separazione netta: il client si occupa della presentazione, il server dei dati e della logica. |
| **Uniform interface** | Interfaccia standardizzata: metodi HTTP, URL, status code e header hanno semantica definita dalla specifica. |

### Header HTTP principali

Gli header trasportano metadati sulla richiesta o sulla risposta, separati dal body:

```
Content-Type: application/json   # formato del body inviato
Accept: application/json         # formato accettato in risposta
Authorization: Bearer <token>    # credenziali di accesso
```

FastAPI imposta automaticamente `Content-Type: application/json` su tutte le risposte JSON. Il client deve inviare lo stesso header quando manda un body.

---

## Risorse e URL design

In REST l'URL identifica **cosa** si sta manipolando (la risorsa), non **come** (l'azione). L'azione è espressa dal metodo HTTP.

### Regole di naming

| Regola | Corretto | Da evitare |
|---|---|---|
| **Sostantivi, non verbi** | `/products` | `/getProducts`, `/deleteProduct` |
| **Plurale per le collezioni** | `/products` | `/product` |
| **Minuscolo con trattini** | `/product-categories` | `/ProductCategories` |
| **Gerarchia con slash** | `/products/{id}/restock` | `/restockProduct?id=5` |

### Risorse gerarchiche

Quando una risorsa appartiene a un'altra, l'URL rispecchia la gerarchia:

```
/products                   → collezione di tutti i prodotti
/products/{id}              → un singolo prodotto
/products/{id}/restock      → azione specifica su un prodotto
```

La profondità va limitata a **due livelli** oltre la collezione radice. Gerarchie più profonde diventano difficili da leggere e da mantenere.

### Path vs query parameter

| Tipo | Uso | Esempio |
|---|---|---|
| **Path parameter** | Identifica una risorsa specifica | `/products/42` |
| **Query parameter** | Filtra, ordina, pagina una collezione | `/products?category=libri&min_price=10` |

---

## Request e Response body

Il **body** è il payload trasportato dalla richiesta o dalla risposta. Nel progetto si usa sempre JSON (`application/json`).

### Request body

Inviato dal client nelle operazioni che creano o modificano dati (`POST`, `PUT`, `PATCH`). Deve corrispondere allo schema Pydantic dichiarato nell'endpoint:

```json
// POST /products — body atteso
{
  "name": "Tastiera meccanica",
  "price": 89.99,
  "category": "elettronica",
  "stock": 15
}
```

FastAPI legge il body, lo passa a Pydantic per la validazione e inietta il modello validato nell'endpoint. Se il body è malformato o mancano campi obbligatori, risponde `422` in automatico, senza codice manuale.

### Response body

Restituito dal server, serializzato dal modello dichiarato in `response_model`. Garantisce che solo i campi autorizzati vengano esposti:

```json
// risposta — include l'id generato dal server
{
  "id": 4,
  "name": "Tastiera meccanica",
  "price": 89.99,
  "category": "elettronica",
  "stock": 15
}
```

**Alcune risposte non hanno body** — `DELETE` risponde `204 No Content` con body vuoto. In FastAPI si dichiara `response_model=None` e si ritorna `None` (o nulla) dall'endpoint.

### Serializzazione Pydantic

```
request JSON  →  Pydantic model (validazione)  →  Python dict / oggetto
Python dict   →  Pydantic model (response_model) →  response JSON
```

Pydantic gestisce la conversione in entrambe le direzioni. Lo stesso schema che valida l'input documenta automaticamente il contratto nella Swagger UI.

---

## Error handling

Le API REST comunicano gli errori attraverso **status code HTTP** e un body JSON con il dettaglio dell'errore. Il client non deve fare parsing del testo — il codice è sufficiente a capire cosa fare.

### Categorie di errori

| Range | Categoria | Chi sbaglia |
|---|---|---|
| `4xx` | Client error | Il client ha fatto una richiesta non valida |
| `5xx` | Server error | Il server ha avuto un problema interno |

### Status code di errore usati nel progetto

| Codice | Quando | Come viene generato |
|---|---|---|
| `404 Not Found` | ID inesistente | `raise HTTPException(status_code=404)` |
| `422 Unprocessable Entity` | Body o parametri invalidi | Automatico da FastAPI+Pydantic |

### `HTTPException` in FastAPI

L'unico meccanismo di errore esplicito nel codice. Interrompe immediatamente l'esecuzione dell'endpoint e fa rispondere FastAPI con il body standard:

```python
raise HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail=f"Product {product_id} not found",
)
```

Body della risposta generata automaticamente:

```json
{"detail": "Product 42 not found"}
```

### Errori 422 automatici

FastAPI intercetta i `ValidationError` di Pydantic e li trasforma in `422` con un body strutturato che elenca ogni campo invalido, il valore ricevuto e il motivo del rifiuto. Zero righe di codice manuale.

```json
{
  "detail": [
    {
      "loc": ["body", "price"],
      "msg": "Input should be greater than 0",
      "type": "greater_than"
    }
  ]
}
```

### Cosa NON fare

- **Non usare `200` per gli errori** — alcuni client ignorano il body e si basano solo sul codice.
- **Non esporre dettagli interni** nei `5xx` (stack trace, query SQL, percorsi di file) — queste informazioni aiutano gli attaccanti.
- **Non inventare status code** — usare solo i codici definiti dalla specifica HTTP.

---

## REST — princìpi base

**REST** (Representational State Transfer) è uno stile architetturale per API HTTP basato su tre regole:

1. **L'URL identifica una risorsa** — es. `/products`, `/products/{id}`. Non un'azione (`/getProduct`, `/deleteProduct`).
2. **Il metodo HTTP esprime l'azione** — cosa fare con quella risorsa.
3. **Lo status code esprime l'esito** — cosa è successo.

### Metodi HTTP usati nel progetto

| Metodo | URL | Semantica |
|---|---|---|
| `GET` | `/products` | Legge la lista |
| `GET` | `/products/{id}` | Legge un singolo elemento |
| `POST` | `/products` | Crea una nuova risorsa |
| `PUT` | `/products/{id}` | Sostituisce completamente la risorsa (tutti i campi obbligatori) |
| `PATCH` | `/products/{id}` | Aggiorna parzialmente (solo i campi inviati) |
| `DELETE` | `/products/{id}` | Elimina la risorsa |

**PUT vs PATCH:** PUT è idempotente e richiede l'intera risorsa. PATCH aggiorna solo i campi presenti nel body — mandare `{"stock": 0}` non azzera gli altri campi.

### Status code usati nel progetto

| Codice | Significato | Quando |
|---|---|---|
| `200 OK` | Successo con body | GET, PUT, PATCH |
| `201 Created` | Risorsa creata | POST |
| `202 Accepted` | Richiesta accettata, elaborazione in background | POST `/restock` |
| `204 No Content` | Successo senza body | DELETE |
| `404 Not Found` | Risorsa inesistente | GET/PUT/PATCH/DELETE su id non presente |
| `422 Unprocessable Entity` | Dati invalidi | Automatico da Pydantic |

---

## FastAPI — concetti chiave

### Parametri di una request

FastAPI distingue tre tipi di parametri in base alla loro posizione:

```python
@router.get("/{product_id}")
def get_product(
    product_id: int,                    # PATH parameter: parte dell'URL
    category: str | None = None,        # QUERY parameter: ?category=libri
    payload: ProductCreate = Body(...), # REQUEST BODY: JSON nel body
    db: Session = Depends(get_db),      # DEPENDENCY: iniettata dal framework
):
```

- **Path parameter** — dichiarato con `{nome}` nell'URL, FastAPI lo converte nel tipo annotato (es. `int`). Se non convertibile, risponde 422 automaticamente.
- **Query parameter** — qualsiasi argomento non nel path e non un body. Aggiunto con `?chiave=valore` nell'URL.
- **Request body** — un argomento annotato con un modello Pydantic. FastAPI legge e valida automaticamente il JSON.

### `response_model` — [routers/products.py](app/routers/products.py)

```python
@router.get("", response_model=list[ProductRead])
```

Serve a due cose:
1. **Filtra** i campi dell'oggetto restituito — nessun campo interno trapela.
2. **Documenta** lo schema nella Swagger UI e nell'OpenAPI JSON.

### `HTTPException` — [routers/products.py](app/routers/products.py)

L'unico errore esplicito nel codice. Ferma l'esecuzione e fa rispondere FastAPI con lo status code e un body JSON `{"detail": "..."}`:

```python
raise HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail=f"Product {product_id} not found",
)
```

Tutti i `422` per dati invalidi vengono generati automaticamente da FastAPI+Pydantic, senza una sola riga aggiuntiva.

### `Depends(get_db)` — Dependency Injection

```python
def get_product(product_id: int, db: Session = Depends(get_db)):
```

FastAPI chiama `get_db()`, avanza fino allo `yield`, inietta la sessione nell'endpoint, poi riprende il blocco `finally` — garantendo che `db.close()` venga sempre chiamato, anche in caso di eccezione. Questo pattern si chiama **generator dependency**.

### `APIRouter` — [routers/products.py](app/routers/products.py)

Raggruppa endpoint correlati. Viene montato nell'app principale con `app.include_router(...)`:

```python
router = APIRouter(prefix="/products", tags=["products"])
# ...
app.include_router(products.router)
```

`prefix` evita di ripetere `/products` su ogni decoratore. `tags` raggruppa gli endpoint nella sezione omonima di `/docs`.

### Lifespan — [main.py](app/main.py)

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # codice PRIMA dello yield → eseguito all'avvio
    with SessionLocal() as db:
        seed_db(db)
    yield
    # codice DOPO lo yield → eseguito allo spegnimento
```

Separa il codice di startup (inizializzazione connessioni, precaricamento dati) dal teardown (rilascio risorse). Sostituisce i vecchi `@app.on_event("startup")`.

---

## Pydantic — validazione e serializzazione

### Perché modelli separati per Create / Update / Read — [models.py](app/models.py)

| Schema | Usato da | Perché |
|---|---|---|
| `ProductCreate` | POST, PUT | l'id lo genera il server, non arriva dal client |
| `ProductUpdate` | PATCH | tutti i campi opzionali per aggiornamenti parziali |
| `ProductRead` | response | include l'id, è ciò che il client riceve |

Separare input e output evita di esporre campi interni (es. password hash) e rende lo schema auto-documentato.

### `Field()` — vincoli di validazione

```python
price: float = Field(gt=0, description="Prezzo in euro, deve essere > 0")
stock: int = Field(ge=0, default=0)
```

- `gt=0` → must be **g**reater **t**han zero.
- `ge=0` → **g**reater or **e**qual to zero.
- `min_length`, `max_length` per le stringhe.

Se il client manda dati invalidi, Pydantic genera automaticamente un `422` con un messaggio di errore dettagliato — senza una sola riga di validazione manuale.

### `model_dump(exclude_unset=True)` — il trucco di PATCH

```python
patch = payload.model_dump(exclude_unset=True)
```

Pydantic distingue tra "il client non ha mandato questo campo" e "il client ha mandato `null`". `exclude_unset=True` restituisce solo i campi effettivamente inviati. Così mandare `{"stock": 0}` non azzera `name`, `price`, ecc.

### `model_dump()` — da Pydantic a dict

```python
return database.create(db, payload.model_dump())
```

Converte il modello Pydantic in `dict` per passarlo al layer CRUD, che costruirà l'istanza ORM.

---

## Pattern avanzati

### `BackgroundTasks` — operazioni asincrone — [routers/products.py](app/routers/products.py)

```python
@router.post("/{product_id}/restock", status_code=202)
async def restock_product(background_tasks: BackgroundTasks, ...):
    background_tasks.add_task(_do_restock, job_id, product_id, quantity)
    return {"job_id": job_id, "status": "pending"}
```

Il client riceve subito il `202 Accepted` con un `job_id`. L'operazione lenta (aggiornamento stock, simulata con 3s di delay) viene eseguita dopo che la response è già stata inviata.

**Perché è rilevante:** qualsiasi chiamata a un modello AI ha latenza variabile (da secondi a minuti). Invece di tenere aperta la connessione HTTP, si accetta la richiesta e si processa in background. Il client fa polling su `GET /jobs/{job_id}` per sapere quando è completata.

Il background task apre una propria sessione DB (`with SessionLocal() as db`): la sessione iniettata via `Depends` è già chiusa quando il task parte.

### `StreamingResponse` — risposta a flusso — [routers/products.py](app/routers/products.py)

```python
async def generate():
    for product in products:
        yield json.dumps({...}) + "\n"
        await asyncio.sleep(0.4)

return StreamingResponse(generate(), media_type="application/x-ndjson")
```

Il server invia i dati man mano che li produce, senza aspettare di avere tutto il payload pronto. Il formato **NDJSON** (Newline-Delimited JSON) usa una riga per ogni oggetto JSON.

**Perché è rilevante:** è esattamente come funziona lo streaming dei token dai provider LLM (Anthropic, OpenAI) quando si usa `stream=True`.

```bash
# Prova con curl (-N disabilita il buffering)
curl -N http://127.0.0.1:8000/products/export/stream
```

### Health check — [main.py](app/main.py)

```python
@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}
```

`SELECT 1` è la query più leggera possibile per verificare che PostgreSQL sia raggiungibile. Pattern standard usato da load balancer e orchestratori (es. Kubernetes) per sapere se il servizio è pronto a ricevere traffico.

---

## Concorrenza asincrona — servire migliaia di utenti

### Il problema: I/O sincrono blocca l'event loop

FastAPI gira su un **event loop asyncio** (gestito da Uvicorn). L'event loop esegue i coroutine uno alla volta, **ma può passare da uno all'altro mentre aspettano I/O**. Se un endpoint fa I/O *sincrono* (query DB bloccante, HTTP call, lettura file), blocca l'intero loop: nessun'altra richiesta viene servita finché quell'operazione non finisce.

```
# PROBLEMA — psycopg2 (sync) blocca il loop:
def get_product(db: Session = Depends(get_db)):
    return db.get(ProductORM, product_id)   # ← blocca per 5–50 ms
    # In questo tempo: ZERO altre richieste servite

# SOLUZIONE — asyncpg + AsyncSession non blocca:
async def get_product(db: AsyncSession = Depends(get_db)):
    return await database.get(db, product_id)   # ← cede il controllo
    # In questo tempo: l'event loop serve N altre richieste
```

### I/O-bound vs CPU-bound

| Tipo di task | Esempio | Come gestirlo |
|---|---|---|
| **I/O-bound** | Query DB, HTTP call, lettura file | `async def` + libreria async (`asyncpg`, `httpx`) |
| **CPU-bound leggero** | Calcolo in-memory, dict lookup | `def` normale (FastAPI lo manda in threadpool) |
| **CPU-bound pesante** | ML inference, encoding video | `ProcessPoolExecutor` o worker separato |

Regola pratica: **se il codice aspetta qualcosa di esterno → async**.

### Stack async usato nel progetto

```
HTTP Request
    ↓
Uvicorn (ASGI server)
    ↓
FastAPI (async def endpoint)
    ↓
SQLAlchemy AsyncSession
    ↓
asyncpg (driver PostgreSQL nativo async)
    ↓
PostgreSQL
```

Ogni livello è non-bloccante: l'event loop può servire migliaia di
richieste concorrenti con un singolo processo Python.

### `AsyncSession` e `create_async_engine` — [database.py](app/database.py)

```python
engine = create_async_engine(
    DATABASE_URL,          # postgresql+asyncpg://...
    pool_size=20,          # connessioni permanenti nel pool
    max_overflow=10,       # extra connessioni quando il pool è pieno
    pool_timeout=30,       # secondi di attesa per una connessione libera
    pool_recycle=1800,     # ricicla connessioni ogni 30 min (anti stale-TCP)
    pool_pre_ping=True,    # SELECT 1 prima di usare una connessione dal pool
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,   # CRITICO: impedisce lazy-load impliciti dopo commit
)
```

`expire_on_commit=False` è fondamentale: con il layer sync, dopo un `commit()` SQLAlchemy "scade" gli attributi ORM e li ricarica al prossimo accesso. In async questo reload implicito è impossibile (richiederebbe un `await` invisibile) e solleva `MissingGreenlet`. Con `False` gli attributi rimangono validi.

### `asyncio.gather()` — I/O parallelo — [database.py](app/database.py)

```python
async def get_many(product_ids: list[int]) -> list[ProductORM | None]:
    async def _fetch_one(pid: int) -> ProductORM | None:
        async with AsyncSessionLocal() as session:   # sessione indipendente
            return await session.get(ProductORM, pid)

    # Le N query partono simultaneamente:
    # t_totale = max(t1, t2, ..., tN)  invece di  sum(t1, t2, ..., tN)
    return list(await asyncio.gather(*(_fetch_one(pid) for pid in product_ids)))
```

**Perché sessioni separate?** Una singola `AsyncSession` non è concurrency-safe: non può essere usata da più coroutine contemporaneamente. Per parallelizzare, ogni task prende la propria connessione dal pool.

Esposto via `GET /products/compare?ids=1&ids=2&ids=3`.

### `asyncio.Semaphore` — rate limiting — [routers/products.py](app/routers/products.py)

```python
_restock_semaphore = asyncio.Semaphore(5)   # max 5 restock concorrenti

async def _restock_task(job_id, product_id, quantity):
    async with _restock_semaphore:           # aspetta se ci sono già 5 in esecuzione
        await asyncio.sleep(3)              # chiamata lenta al servizio esterno
        async with AsyncSessionLocal() as db:
            await database.update(db, product_id, {"stock": new_stock})
```

Senza semaphore, 1000 richieste di restock simultanee aprirebbero 1000 connessioni verso il servizio esterno. Il semaphore limita il burst a 5 concorrenti; le altre aspettano **senza bloccare il loop** (sono in pausa, non consumano CPU).

### `asyncio.wait_for()` — timeout — [routers/products.py](app/routers/products.py)

```python
await asyncio.wait_for(
    _restock_task(job_id, product_id, quantity),
    timeout=30.0,   # se non finisce in 30s → TimeoutError
)
```

Ogni operazione lenta deve avere un timeout. Senza di esso, un servizio esterno down terrà il coroutine in attesa per sempre, esaurendo il semaphore e poi il pool di connessioni.

Python 3.11+ offre anche `asyncio.timeout()` come context manager:

```python
async with asyncio.timeout(30.0):
    await _restock_task(...)
```

### `def` vs `async def` — quando usare quale

| Caso | Dichiarazione | Perché |
|---|---|---|
| Query DB, HTTP call | `async def` | I/O-bound: cede il controllo mentre aspetta |
| Lettura dict in-memory | `def` | Sync puro, nessun I/O; FastAPI usa threadpool |
| Background task asincrono | `async def` | Deve usare `await` internamente |
| Endpoint puramente CPU | `def` | FastAPI lo delega al threadpool automaticamente |

FastAPI gestisce entrambi: i `def` sync vengono eseguiti in un `ThreadPoolExecutor` per non bloccare il loop; gli `async def` girano direttamente nell'event loop.

### Schema riassuntivo

```
1000 richieste concorrenti
        ↓
   Event Loop (1 thread)
   ├─ request_1: await db.get()  ─────── aspetta il DB ──────→ risponde
   ├─ request_2: await db.list() ──────── aspetta il DB ─────→ risponde
   ├─ ...                                (nel frattempo serve le altre)
   └─ request_N: await db.create() ───── aspetta il DB ─────→ risponde

Connection Pool (max 20+10 connessioni)
   └─ ogni await db.* prende una connessione, la usa, la restituisce
```

---

## Documentazione automatica

FastAPI genera automaticamente due endpoint di documentazione:

- **`/docs`** — Swagger UI interattiva: permette di provare tutti gli endpoint dal browser.
- **`/openapi.json`** — schema JSON grezzo usato da tool esterni (Postman, client autogenerati).

Lo schema viene costruito da `response_model`, dai modelli Pydantic e dalle annotazioni dei parametri — senza scrivere documentazione manuale.
