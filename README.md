# Mini Backend REST con FastAPI — Catalogo Prodotti

Progetto didattico per imparare i concetti fondamentali di un backend REST,
le funzionalità base di **FastAPI** e l'integrazione con **PostgreSQL** via **SQLAlchemy**.

---

## Concetti fondamentali

### Cos'è un ORM e perché usarlo

Un **ORM** (Object-Relational Mapper) è uno strato software che traduce
automaticamente tra due mondi incompatibili:

| Mondo Python | Mondo SQL |
|---|---|
| Classe (`ProductORM`) | Tabella (`products`) |
| Istanza (`product`) | Riga |
| Attributo (`product.price`) | Colonna (`price`) |
| Metodo (`db.add`, `db.delete`) | Statement (`INSERT`, `DELETE`) |

Senza ORM dovresti scrivere SQL a mano — stringhe come
`"SELECT * FROM products WHERE price > %s"` — e poi convertire manualmente
le tuple restituite in oggetti Python. L'ORM fa tutto questo per te,
mantenendo il codice in Python puro e fortemente tipato.

**Il prezzo:** una query molto complessa o ottimizzata può richiedere SQL grezzo
(`db.execute(text(...))`) perché l'ORM non riesce a generarla in modo efficiente.
In questo progetto, con `echo=True`, vedi esattamente quale SQL viene generato —
è il modo migliore per capire cosa fa l'ORM sotto il cofano.

---

### Perché SQLAlchemy

SQLAlchemy è lo standard de facto per gli ORM in Python. Esistono due interfacce:

- **Core** — costruisce ed esegue SQL in modo programmatico, vicino al metallo.
- **ORM** — quello che usiamo qui: classi Python mappate su tabelle, sessioni, identity map.

In questo progetto vedi la sintassi moderna (SQLAlchemy 2.x) con `Mapped[T]` e
`mapped_column()`. Rispetto alla sintassi legacy (`Column`, `relationship`),
è più leggibile e funziona meglio con i type checker (mypy, pyright).

Punti chiave di SQLAlchemy che trovi nel codice:

**`DeclarativeBase`** ([orm.py](app/orm.py)) — classe base che insegna a SQLAlchemy
la struttura della tabella leggendo le annotazioni Python. Da qui deriva `Base`,
e `Base.metadata` contiene la mappa di tutte le tabelle del progetto.

**`Mapped[T]`** ([orm.py](app/orm.py)) — il tipo `Mapped[str]` significa colonna
`NOT NULL`, `Mapped[str | None]` significa colonna nullable. Il type checker può
ragionare sugli attributi ORM come su normali attributi Python.

**`sessionmaker` e `Session`** ([database.py](app/database.py)) — la sessione è
l'unità di lavoro di SQLAlchemy. Tiene traccia di tutti gli oggetti caricati
(_identity map_) e di tutte le modifiche pendenti. Al `commit()` le traduce in SQL.

**Dirty tracking** ([database.py](app/database.py), funzioni `replace` e `update`) —
SQLAlchemy confronta lo stato corrente degli attributi con lo snapshot al momento
del caricamento. Al `commit()` genera un `UPDATE` con solo le colonne cambiate,
senza che tu debba costruire la query.

**`echo=True`** ([database.py](app/database.py)) — stampa ogni SQL sul terminale.
Attivalo sempre mentre studi. Toglilo in produzione.

---

### Perché PostgreSQL

PostgreSQL è un database relazionale open source maturo, usato in produzione da
grandi aziende (Apple, Instagram, Spotify). In questo progetto lo scegliamo perché:

- **ACID** — le transazioni sono Atomiche, Consistenti, Isolate e Durevoli. Se il
  server crasha a metà di un `commit()`, il DB torna allo stato precedente.
- **SERIAL / sequenze** — un id intero con `primary_key=True` diventa una sequenza
  `SERIAL` gestita dal DB. PostgreSQL assegna id univoci anche sotto concorrenza,
  senza race condition (a differenza di `itertools.count` in un dict Python).
- **Tipi ricchi** — `VARCHAR(100)`, `TEXT`, `FLOAT`, `INTEGER` corrispondono
  esattamente ai tipi SQLAlchemy usati in [orm.py](app/orm.py).
- **Persistenza reale** — i dati sopravvivono al riavvio del server. Confronta
  con la versione precedente (dict Python) dove bastava riavviare per perdere tutto.
- **psycopg2** — il driver Python che fa da ponte tra SQLAlchemy e PostgreSQL.
  Appare nella `DATABASE_URL`: `postgresql+psycopg2://...`.

PostgreSQL vs alternative:
- **SQLite**: ottimo per test e prototipazione, ma non supporta concorrenza reale.
- **MySQL/MariaDB**: popolare, ma PostgreSQL ha uno standard SQL più rigoroso e
  feature più avanzate (JSON nativo, full-text search, window functions).
- **MongoDB**: document store NoSQL, adatto a dati schema-less; qui i nostri dati
  hanno uno schema fisso, quindi un DB relazionale è la scelta corretta.

---

### Concetti FastAPI nel codice

**`Depends(get_db)`** ([routers/products.py](app/routers/products.py)) —
Dependency Injection integrata nel framework. FastAPI chiama `get_db()`, avanza
fino allo `yield`, inietta la sessione nell'endpoint, poi riprende l'esecuzione
nel blocco `finally` — garantendo che `db.close()` venga sempre chiamato, anche
in caso di eccezione.

**Pydantic e la separazione input/output** ([models.py](app/models.py)) —
tre modelli distinti per la stessa risorsa:

| Schema | Usato da | Perché |
|---|---|---|
| `ProductCreate` | POST, PUT | l'id lo genera il server, non arriva dal client |
| `ProductUpdate` | PATCH | tutti i campi opzionali per aggiornamenti parziali |
| `ProductRead` | response | include l'id, è ciò che il client riceve |

**`model_dump(exclude_unset=True)`** ([routers/products.py](app/routers/products.py)) —
per PATCH, Pydantic distingue tra "il client non ha mandato questo campo" e
"il client ha mandato `null`". `exclude_unset=True` restituisce solo i campi
effettivamente inviati, così l'ORM genera un `UPDATE` che tocca solo quelli.

**`response_model`** — dichiarato su ogni endpoint, serve a due cose:
filtrare i campi dell'oggetto restituito (nessun campo interno trapela) e
guidare FastAPI nella generazione dello schema OpenAPI.

**Lifespan** ([main.py](app/main.py)) — il pattern `@asynccontextmanager` con
`yield` separa il codice di startup (prima dello yield) da quello di shutdown
(dopo). Qui viene usato per chiamare `seed_db()` all'avvio.

**`HTTPException`** — l'unico errore esplicito nel codice. Tutti i 422 vengono
gestiti automaticamente da FastAPI+Pydantic senza una sola riga aggiuntiva.

---

## Struttura

```
app/
├── main.py              # Punto di ingresso: FastAPI app, lifespan, router
├── models.py            # Modelli Pydantic (schema di input/output HTTP)
├── orm.py               # Modello SQLAlchemy: mappa la classe Python ↔ tabella SQL
├── database.py          # Engine, SessionLocal, get_db(), funzioni CRUD
└── routers/
    ├── products.py      # Endpoint CRUD per /products
    └── jobs.py          # Endpoint per il tracking dei job asincroni
```

| File | Sistema | Scopo |
|---|---|---|
| `models.py` | Pydantic | Validare e serializzare JSON ↔ Python |
| `orm.py` | SQLAlchemy | Mappare oggetti Python ↔ righe SQL |
| `database.py` | SQLAlchemy | Connessione, sessioni, CRUD |

---

## Prerequisiti

Avere **PostgreSQL** in esecuzione. Il modo più semplice con Docker:

```bash
docker run --name pgdemo \
  -e POSTGRES_PASSWORD=yourpassword \
  -e POSTGRES_DB=products_db \
  -p 5432:5432 -d postgres:16
```

## Avvio rapido

```bash
# 1. Clona il repo e spostati nella cartella
cd FastAPI

# 2. Aggiorna .env con le tue credenziali PostgreSQL
#    (il file è già presente con un valore di esempio)
DATABASE_URL=postgresql+psycopg2://postgres:yourpassword@localhost:5432/products_db

# 3. Installa le dipendenze con uv
uv sync

# 4. Avvia il server di sviluppo
uv run fastapi dev app/main.py
```

Il server parte su <http://127.0.0.1:8000>

All'avvio vedrai sul terminale l'SQL generato da SQLAlchemy:
- `CREATE TABLE IF NOT EXISTS products ...` — la tabella viene creata automaticamente.
- Tre `INSERT INTO products ...` — i prodotti di esempio vengono inseriti se la tabella è vuota.

**Riavviando il server i dati sopravvivono**: questa è la differenza chiave rispetto alla versione precedente con il dict Python.

---

## Test manuale dal browser

Apri **<http://127.0.0.1:8000/docs>** → Swagger UI ti permette di
provare tutti gli endpoint cliccando "Try it out".

### Percorso di esplorazione consigliato

Prova in quest'ordine, leggendo cosa fa ogni endpoint e che status code ritorna:

1. `GET /health` → verifica che il server e il DB siano raggiungibili.
2. `GET /products` → vedi la lista pre-popolata di prodotti (200 OK).
3. `GET /products?category=libri&min_price=5` → filtra con query params; guarda il `WHERE` generato nel terminale.
4. `GET /products/1` → dettaglio di un prodotto (200).
5. `GET /products/999` → prodotto inesistente → **404 Not Found**.
6. `POST /products` con un body valido → crea un nuovo prodotto (**201 Created**); nota il `RETURNING id` nel terminale.
7. `POST /products` con `price: -10` → **422 Unprocessable Entity**: Pydantic blocca dati invalidi *prima* che entrino nella tua funzione.
8. `PATCH /products/{id}` con solo `{ "stock": 0 }` → aggiorna solo quel campo; l'`UPDATE` nel terminale tocca solo `stock`.
9. `PUT /products/{id}` con tutti i campi → sostituisce completamente il prodotto.
10. `DELETE /products/{id}` → 204 No Content. Poi rifai `GET` sullo stesso id → 404.
11. **Riavvia il server** → fai di nuovo `GET /products`: i dati ci sono ancora.

### Verifica diretta su PostgreSQL

```bash
psql -U postgres -d products_db -c "SELECT * FROM products;"
```

Vedrai le stesse righe restituite dall'API: questo chiude il cerchio tra
l'ORM e il database reale.

### Schema OpenAPI grezzo

<http://127.0.0.1:8000/openapi.json> mostra lo schema JSON generato
automaticamente da FastAPI: è la "fonte di verità" che tool esterni
(client autogenerati, Postman, ecc.) usano per integrarsi con la tua API.

---

## Cosa imparerai leggendo il codice

1. I metodi HTTP fondamentali (`GET`, `POST`, `PUT`, `PATCH`, `DELETE`) e quando usarli.
2. Gli **status code HTTP** corretti (200, 201, 204, 404, 422...).
3. La differenza tra **path parameters**, **query parameters** e **request body**.
4. Come **Pydantic** valida i dati in ingresso e perché conviene usare modelli diversi per input e output.
5. Come gestire errori con `HTTPException`.
6. Come FastAPI genera automaticamente la documentazione OpenAPI.
7. Come definire un **modello ORM SQLAlchemy** e mapparlo su una tabella PostgreSQL.
8. Il pattern **Session + Depends(get_db)**: iniettare la connessione al DB senza gestirla a mano.
9. Come le operazioni CRUD si traducono in query SQL (visibili sul terminale con `echo=True`).
10. Il pattern **lifespan** per eseguire codice di setup/teardown all'avvio del server.
11. Come funziona il **dirty tracking** di SQLAlchemy: solo le colonne modificate finiscono nell'`UPDATE`.
12. La differenza tra `default` Python-side e `server_default` SQL-side in una colonna ORM.

---

## Note

- Non c'è autenticazione né test suite: sono buoni esercizi successivi.
- Per gestire modifiche allo schema del DB in modo versionato, il passo successivo è **Alembic** (tool di migrazione per SQLAlchemy).
