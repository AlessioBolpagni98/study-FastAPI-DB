"""
Modello ORM SQLAlchemy: la classe Python che rappresenta la tabella SQL.

Perché un file separato da models.py?
--------------------------------------
`models.py` contiene modelli PYDANTIC  →  validano e serializzano JSON ↔ Python.
`orm.py`    contiene modelli SQLALCHEMY →  mappano oggetti Python ↔ righe SQL.

Sono due sistemi con scopi diversi e li teniamo separati per chiarezza:
  - Pydantic  : "questo JSON è valido? come lo mostro al client?"
  - SQLAlchemy: "come salvo/leggo questi dati nel database?"

Concetti chiave mostrati qui
-----------------------------
1. DeclarativeBase  — classe base da cui ereditano tutti i modelli ORM.
                      SQLAlchemy usa la sua metaclasse per leggere le
                      annotazioni e costruire la mappatura tabella ↔ classe.

2. __tablename__    — nome della tabella nel DB. Se non lo specifichi,
                      SQLAlchemy usa il nome della classe in minuscolo.

3. Mapped[T]        — annotazione che dichiara il tipo Python della colonna.
                      `Mapped[str]`      → colonna NOT NULL
                      `Mapped[str|None]` → colonna nullable

4. mapped_column()  — definisce i vincoli SQL della colonna:
                      primary_key, nullable, default, ...

5. Integer PK       — con primary_key=True su una colonna Integer, PostgreSQL
                      crea automaticamente una sequenza SERIAL. L'id viene
                      assegnato dal DB, non da noi.
"""

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Classe base per tutti i modelli ORM del progetto."""
    pass


class ProductORM(Base):
    __tablename__ = "products"

    # primary_key=True → colonna PK con SERIAL in PostgreSQL.
    # Il DB assegna l'id automaticamente (autoincrement implicito).
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # String(100) → VARCHAR(100) nel DDL SQL generato.
    # Mapped[str] (senza Optional) → nullable=False di default.
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Mapped[str | None] → colonna nullable (può contenere NULL).
    # Text → tipo TEXT di Postgres: stringa a lunghezza illimitata.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Float → DOUBLE PRECISION in PostgreSQL.
    price: Mapped[float] = mapped_column(Float, nullable=False)

    category: Mapped[str] = mapped_column(String(50), nullable=False)

    # default=0 → valore di default Python-side (non a livello SQL).
    # Per un default SQL-side si userebbe server_default="0".
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
