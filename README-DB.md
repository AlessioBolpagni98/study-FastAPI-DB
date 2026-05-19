# Concetti: Database, SQLAlchemy, PostgreSQL

Concetti trattati in questo progetto relativi al layer di persistenza.

---

## Cos'è un ORM e perché usarlo

Un **ORM** (Object-Relational Mapper) traduce automaticamente tra due mondi:

| Mondo Python | Mondo SQL |
|---|---|
| Classe (`ProductORM`) | Tabella (`products`) |
| Istanza (`product`) | Riga |
| Attributo (`product.price`) | Colonna (`price`) |
| Metodo (`db.add`, `db.delete`) | Statement (`INSERT`, `DELETE`) |

Senza ORM dovresti scrivere SQL a mano — stringhe come `"SELECT * FROM products WHERE price > %s"` — e poi convertire manualmente le tuple in oggetti Python. L'ORM fa tutto questo, mantenendo il codice in Python puro e tipato.

**Il prezzo:** una query molto complessa può richiedere SQL grezzo (`db.execute(text(...))`) perché l'ORM non riesce a generarla in modo efficiente. Con `echo=True` vedi esattamente quale SQL viene generato: è il modo migliore per capire cosa fa l'ORM sotto il cofano.

---

## SQLAlchemy — concetti chiave

Questo progetto usa la sintassi moderna **SQLAlchemy 2.x** con `Mapped[T]` e `mapped_column()`.

### `DeclarativeBase` — [orm.py](app/orm.py)

Classe base da cui ereditano tutti i modelli ORM. SQLAlchemy usa la sua metaclasse per leggere le annotazioni Python e costruire la mappatura classe ↔ tabella. `Base.metadata` contiene la mappa di tutte le tabelle del progetto.

```python
class Base(DeclarativeBase):
    pass

class ProductORM(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
```

### `Mapped[T]` — [orm.py](app/orm.py)

Il tipo annota la colonna e il suo nullability:

| Annotazione | Significato SQL |
|---|---|
| `Mapped[str]` | `NOT NULL` |
| `Mapped[str \| None]` | colonna nullable |

Il type checker (mypy, pyright) ragiona sugli attributi ORM come su normali attributi Python.

### Tipi SQL usati nel progetto

| SQLAlchemy | PostgreSQL | Python |
|---|---|---|
| `Integer` | `INTEGER` / `SERIAL` (se PK) | `int` |
| `String(n)` | `VARCHAR(n)` | `str` |
| `Text` | `TEXT` | `str` |
| `Float` | `DOUBLE PRECISION` | `float` |

### `default` vs `server_default`

```python
# default Python-side: il valore è impostato da SQLAlchemy prima dell'INSERT
stock: Mapped[int] = mapped_column(Integer, default=0)

# server_default SQL-side: il DB imposta il valore (DDL: DEFAULT 0)
# stock: Mapped[int] = mapped_column(Integer, server_default="0")
```

### `sessionmaker` e `Session` — [database.py](app/database.py)

La **sessione** è l'unità di lavoro di SQLAlchemy. Tiene traccia di tutti gli oggetti caricati (_identity map_) e di tutte le modifiche pendenti. Al `commit()` le traduce in SQL.

```python
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
```

- `autocommit=False` → dobbiamo chiamare `db.commit()` esplicitamente (controllo sulle transazioni).
- `autoflush=False` → SQLAlchemy non scrive sul DB prima di ogni query.

### Dirty tracking — [database.py](app/database.py)

SQLAlchemy confronta lo stato corrente degli attributi con lo snapshot al momento del caricamento. Al `commit()` genera un `UPDATE` con **solo le colonne cambiate**, senza che tu debba costruire la query:

```python
product = db.get(ProductORM, product_id)
product.price = 19.90          # SQLAlchemy registra la modifica
db.commit()                    # genera: UPDATE products SET price=19.90 WHERE id=...
```

### `db.get()` e identity map

`db.get(ProductORM, id)` è lo shortcut per la lookup per primary key. Se l'oggetto è già stato caricato in questa sessione, lo restituisce **senza fare una query al DB** (identity map).

### `echo=True` — [database.py](app/database.py)

```python
engine = create_engine(DATABASE_URL, echo=True)
```

Stampa ogni SQL generato sul terminale. Fondamentale durante lo studio — toglilo in produzione.

---

## Ciclo di vita CRUD in SQLAlchemy

### CREATE

```python
product = ProductORM(**data)
db.add(product)      # aggiunge alla "pending area" della sessione
db.commit()          # esegue INSERT; PostgreSQL assegna l'id via SERIAL
db.refresh(product)  # rilegge la riga dal DB: ora product.id è popolato
```

### READ con filtri dinamici

```python
stmt = select(ProductORM)
if category:
    stmt = stmt.where(ProductORM.category == category)
if min_price:
    stmt = stmt.where(ProductORM.price >= min_price)
return db.execute(stmt).scalars().all()
```

La clausola `WHERE` viene costruita solo per i filtri effettivamente passati.

### UPDATE (dirty tracking)

```python
for key, value in patch.items():
    setattr(product, key, value)
db.commit()   # UPDATE con solo le colonne cambiate
db.refresh(product)
```

### DELETE

```python
db.delete(product)
db.commit()   # DELETE FROM products WHERE id = ...
```

---

## DDL automatico

```python
Base.metadata.create_all(bind=engine)
```

Crea la tabella `products` se non esiste ancora. In un progetto maturo si usa **Alembic** per gestire le migrazioni in modo versionato (come git per lo schema del DB).

---

## PostgreSQL — perché questo database

- **ACID** — le transazioni sono Atomiche, Consistenti, Isolate e Durevoli. Se il server crasha a metà di un `commit()`, il DB torna allo stato precedente.
- **SERIAL / sequenze** — un id con `primary_key=True` diventa una sequenza `SERIAL`. PostgreSQL assegna id univoci anche sotto concorrenza, senza race condition.
- **Tipi ricchi** — `VARCHAR(n)`, `TEXT`, `FLOAT`, `INTEGER` corrispondono ai tipi SQLAlchemy usati in [orm.py](app/orm.py).
- **Persistenza reale** — i dati sopravvivono al riavvio del server. La versione precedente con il dict Python perdeva tutto ad ogni riavvio.
- **psycopg2** — il driver Python che fa da ponte tra SQLAlchemy e PostgreSQL. Appare nella `DATABASE_URL`: `postgresql+psycopg2://...`.

### PostgreSQL vs alternative

| Database | Quando usarlo |
|---|---|
| **SQLite** | Test e prototipazione; non supporta concorrenza reale |
| **MySQL/MariaDB** | Popolare, ma PostgreSQL ha uno standard SQL più rigoroso |
| **MongoDB** | Dati schema-less; qui i dati hanno schema fisso → DB relazionale corretto |
