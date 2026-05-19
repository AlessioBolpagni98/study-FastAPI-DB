"""
Layer di accesso al database — PostgreSQL via SQLAlchemy.

Struttura del file
------------------
A. Engine & SessionLocal  — la "connessione" al DB e la factory delle sessioni.
B. get_db()               — dependency FastAPI: fornisce una Session per ogni
                            request e garantisce che venga chiusa a fine request.
C. Funzioni CRUD          — stessa interfaccia di prima (list_all, get, create,
                            replace, update, delete), ora usano SQLAlchemy.
D. seed_db()              — inserisce 3 prodotti di esempio se la tabella è vuota.

Cosa è cambiato rispetto alla versione con il dict?
----------------------------------------------------
- I dati sopravvivono al riavvio del server (persistenza reale).
- I filtri diventano clausole WHERE nella query SQL (non più list comprehension).
- Gli id sono assegnati da PostgreSQL tramite SERIAL (non più itertools.count).
- La gestione della concorrenza è delegata al DB (transaction isolation).
"""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.orm import Base, ProductORM

# ---------------------------------------------------------------------------
# A. Engine & SessionLocal
# ---------------------------------------------------------------------------

# Carica DATABASE_URL dal file .env nella cartella del progetto.
load_dotenv()
DATABASE_URL = os.environ["DATABASE_URL"]

# `create_engine` crea il motore: gestisce il connection pool e la
# comunicazione con PostgreSQL.
#
# echo=True  →  ogni SQL generato da SQLAlchemy viene stampato su stdout.
#               Fondamentale durante lo studio: si vede esattamente quale
#               query viene eseguita per ogni operazione ORM.
#               In produzione si mette echo=False.
engine = create_engine(DATABASE_URL, echo=True)

# `sessionmaker` restituisce una *factory* di sessioni (non una sessione).
# Ogni chiamata a SessionLocal() crea una nuova sessione indipendente.
#
# autocommit=False  →  dobbiamo chiamare db.commit() esplicitamente.
#                      Questo ci dà controllo sulle transazioni.
# autoflush=False   →  SQLAlchemy non scrive sul DB prima di ogni query;
#                      siamo noi a decidere quando fare flush/commit.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# DDL automatico: crea la tabella "products" se non esiste ancora.
# Legge la struttura da Base.metadata (popolata da orm.py al momento
# dell'import di ProductORM).
#
# Nota: in un progetto più maturo si userebbe Alembic per gestire le
# migrazioni in modo versionato (come git per lo schema del DB).
Base.metadata.create_all(bind=engine)


# ---------------------------------------------------------------------------
# B. Dependency FastAPI
# ---------------------------------------------------------------------------

def get_db():
    """
    Dependency che fornisce una Session SQLAlchemy per ogni request.

    Come funziona il pattern "generator dependency"
    ------------------------------------------------
    1. FastAPI chiama get_db() e avanza fino al `yield`.
    2. Il valore yielded (db) viene iniettato nell'endpoint come argomento.
    3. Dopo che l'endpoint ha terminato (sia in caso di successo che di
       eccezione), FastAPI riprende l'esecuzione del generator dal punto
       dopo il yield — eseguendo sempre il blocco `finally`.
    4. `db.close()` restituisce la connessione al connection pool.

    Nei router si usa così:
        def my_endpoint(db: Session = Depends(get_db)):
            result = database.get(db, 42)
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# C. Funzioni CRUD
# ---------------------------------------------------------------------------

def list_all(
    db: Session,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
) -> list[ProductORM]:
    """
    Prima (dict):  result = list(_products.values())
                   result = [p for p in result if p["category"] == category]

    Ora (SQL):     SELECT * FROM products
                   WHERE category = :category      -- se passato
                   AND   price >= :min_price        -- se passato
                   AND   price <= :max_price        -- se passato

    `select(ProductORM)` costruisce la query; i `.where(...)` aggiungono
    clausole solo se il filtro è stato fornito (query dinamica).
    `.scalars().all()` esegue la query e restituisce una lista di istanze ORM.
    """
    stmt = select(ProductORM)
    if category is not None:
        stmt = stmt.where(ProductORM.category == category)
    if min_price is not None:
        stmt = stmt.where(ProductORM.price >= min_price)
    if max_price is not None:
        stmt = stmt.where(ProductORM.price <= max_price)
    return db.execute(stmt).scalars().all()


def get(db: Session, product_id: int) -> ProductORM | None:
    """
    Prima (dict):  return _products.get(product_id)

    Ora (SQL):     SELECT * FROM products WHERE id = :id  LIMIT 1

    `db.get()` è lo shortcut di SQLAlchemy per la lookup per PK.
    Usa l'identity map della sessione: se l'oggetto è già stato caricato
    in questa sessione, lo restituisce senza fare una query al DB.
    """
    return db.get(ProductORM, product_id)


def create(db: Session, data: dict) -> ProductORM:
    """
    Prima (dict):  new_id = next(_id_seq)
                   _products[new_id] = {"id": new_id, **data}

    Ora (SQL):     INSERT INTO products (name, description, price,
                                         category, stock)
                   VALUES (:name, :description, :price, :category, :stock)
                   RETURNING id

    `db.add(product)`  →  aggiunge l'oggetto alla "pending area" della sessione.
    `db.commit()`      →  esegue INSERT e rende il record permanente.
    `db.refresh()`     →  rilegge la riga dal DB; ora product.id è popolato
                           (assegnato da PostgreSQL tramite SERIAL).
    """
    product = ProductORM(**data)
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


def replace(db: Session, product_id: int, data: dict) -> ProductORM | None:
    """
    Prima (dict):  if product_id not in _products: return None
                   _products[product_id] = {"id": product_id, **data}

    Ora (SQL):     UPDATE products
                   SET name=:n, description=:d, price=:p, category=:c, stock=:s
                   WHERE id = :id

    SQLAlchemy traccia le modifiche agli attributi (dirty tracking):
    quando chiamiamo db.commit(), genera automaticamente un UPDATE con
    solo le colonne che sono cambiate rispetto allo snapshot iniziale.
    """
    product = db.get(ProductORM, product_id)
    if product is None:
        return None
    for key, value in data.items():
        setattr(product, key, value)
    db.commit()
    db.refresh(product)
    return product


def update(db: Session, product_id: int, patch: dict) -> ProductORM | None:
    """
    Identico a `replace`, ma `patch` contiene solo i campi da toccare.

    Prima (dict):  existing.update(patch)

    Ora (SQL):     UPDATE products SET <solo i campi in patch> WHERE id = :id

    Il meccanismo SQLAlchemy è lo stesso di replace: setattr + commit.
    La differenza è che `patch` arriva già filtrato dal router con
    `model_dump(exclude_unset=True)`, quindi solo i campi inviati dal client
    vengono toccati.
    """
    product = db.get(ProductORM, product_id)
    if product is None:
        return None
    for key, value in patch.items():
        setattr(product, key, value)
    db.commit()
    db.refresh(product)
    return product


def delete(db: Session, product_id: int) -> bool:
    """
    Prima (dict):  return _products.pop(product_id, None) is not None

    Ora (SQL):     DELETE FROM products WHERE id = :id
    """
    product = db.get(ProductORM, product_id)
    if product is None:
        return False
    db.delete(product)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# D. Seed
# ---------------------------------------------------------------------------

def seed_db(db: Session) -> None:
    """Inserisce 3 prodotti di esempio solo se la tabella è vuota.

    Chiamata da main.py nel lifespan dell'app (all'avvio del server).
    """
    if db.execute(select(ProductORM)).first() is not None:
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
        create(db, data)
