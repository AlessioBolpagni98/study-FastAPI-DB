# Mini Backend REST con FastAPI — Catalogo Prodotti

API REST didattica per imparare i concetti fondamentali di FastAPI e PostgreSQL.

Concetti trattati: [README-API.md](README-API.md) · [README-DB.md](README-DB.md) · [README-Docker.md](README-Docker.md)

---

## Struttura

```
app/
├── main.py              # FastAPI app, lifespan, router montaggio
├── models.py            # Schemi Pydantic (input/output HTTP)
├── orm.py               # Modello SQLAlchemy: classe Python ↔ tabella SQL
├── database.py          # Engine, SessionLocal, get_db(), funzioni CRUD
└── routers/
    ├── products.py      # Endpoint CRUD per /products
    └── jobs.py          # Endpoint per il tracking dei job asincroni
```

## Avvio con Docker (consigliato)

Nessun prerequisito — Docker gestisce tutto, incluso PostgreSQL.

```bash
docker compose up --build
```

Il server parte su <http://127.0.0.1:8000>. Per fermare: `docker compose down`.

Per approfondire Docker e Docker Compose → [README-Docker.md](README-Docker.md).

## Avvio in locale (modalità sviluppo)

Utile per sviluppare con auto-reload. Richiede PostgreSQL già in esecuzione.

```bash
# 1. Avvia solo il DB con Docker
docker run --name pgdemo \
  -e POSTGRES_PASSWORD=yourpassword \
  -e POSTGRES_DB=products_db \
  -p 5432:5432 -d postgres:16

# 2. Verifica che .env punti a localhost
DATABASE_URL=postgresql+psycopg2://postgres:yourpassword@localhost:5432/products_db

# 3. Installa le dipendenze con uv
uv sync

# 4. Avvia il server di sviluppo (auto-reload su salvataggio)
uv run fastapi dev app/main.py
```

Il server parte su <http://127.0.0.1:8000>.

All'avvio vedrai sul terminale l'SQL generato da SQLAlchemy:
- `CREATE TABLE IF NOT EXISTS products ...` — la tabella viene creata automaticamente.
- Tre `INSERT INTO products ...` — i prodotti di esempio vengono inseriti se la tabella è vuota.

## Test manuale

Apri <http://127.0.0.1:8000/docs> per la Swagger UI interattiva.

Prova in quest'ordine:

1. `GET /health` → verifica che il server e il DB siano raggiungibili.
2. `GET /products` → lista pre-popolata (200 OK).
3. `GET /products?category=libri&min_price=5` → filtri via query params; guarda il `WHERE` nel terminale.
4. `GET /products/1` → dettaglio (200).
5. `GET /products/999` → **404 Not Found**.
6. `POST /products` con body valido → **201 Created**; nota `RETURNING id` nel terminale.
7. `POST /products` con `price: -10` → **422 Unprocessable Entity**.
8. `PATCH /products/{id}` con solo `{ "stock": 0 }` → aggiorna solo quel campo.
9. `PUT /products/{id}` con tutti i campi → sostituzione completa.
10. `DELETE /products/{id}` → 204 No Content. Poi `GET` sullo stesso id → 404.
11. **Riavvia il server** → `GET /products`: i dati ci sono ancora.

Verifica diretta su PostgreSQL:

```bash
psql -U postgres -d products_db -c "SELECT * FROM products;"
```
