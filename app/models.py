"""
Modelli Pydantic per la risorsa "Product".

Cos'è Pydantic?
---------------
Pydantic è una libreria che permette di definire la "forma" dei dati
come normali classi Python con type hint. FastAPI la usa per:

  1. Validare automaticamente il JSON in ingresso (request body).
  2. Convertire (parse) il JSON in oggetti Python tipati.
  3. Serializzare gli oggetti Python in JSON in uscita (response).
  4. Generare lo schema OpenAPI mostrato in /docs.

Se il JSON ricevuto non rispetta il modello, FastAPI risponde
automaticamente con `422 Unprocessable Entity` e un messaggio di errore
dettagliato — senza che noi scriviamo una sola riga di validazione.

Perché modelli diversi per Create / Update / Read?
--------------------------------------------------
- `ProductCreate`: l'id NON deve arrivare dal client (lo genera il server).
- `ProductUpdate`: tutti i campi opzionali, così PATCH può aggiornarne uno solo.
- `ProductRead`:   include l'id, è ciò che restituiamo al client.

Separare input e output è una buona pratica: evita di esporre campi
"interni" (es. password hash, timestamp di creazione) e rende lo schema
auto-documentato.
"""

from pydantic import BaseModel, Field


class ProductBase(BaseModel):
    """Campi comuni a tutte le rappresentazioni di un prodotto.

    `Field(...)` permette di aggiungere vincoli di validazione e
    metadati (descrizione, esempio) che finiranno nella documentazione.
    """

    name: str = Field(min_length=1, max_length=100, description="Nome del prodotto")
    description: str | None = Field(
        default=None, max_length=500, description="Descrizione opzionale"
    )
    # gt=0 → must be greater than zero. Pydantic rifiuta prezzi negativi o nulli.
    price: float = Field(gt=0, description="Prezzo in euro, deve essere > 0")
    category: str = Field(min_length=1, max_length=50)
    # ge=0 → greater or equal to zero. Lo stock può essere 0 ma non negativo.
    stock: int = Field(ge=0, default=0, description="Pezzi disponibili")


class ProductCreate(ProductBase):
    """Schema accettato da POST /products. Niente id: lo assegna il server."""

    # Esempio mostrato in Swagger UI nel pannello "Try it out".
    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "Tastiera meccanica",
                "description": "Switch tattili, layout ITA",
                "price": 89.90,
                "category": "informatica",
                "stock": 15,
            }
        }
    }


class ProductUpdate(BaseModel):
    """Schema per PATCH: tutti i campi opzionali.

    Non eredita da ProductBase perché lì i campi sono obbligatori.
    Qui invece vogliamo permettere update parziali — es. inviare solo
    `{"stock": 0}` per esaurire la disponibilità senza ritrasmettere tutto.
    """

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    price: float | None = Field(default=None, gt=0)
    category: str | None = Field(default=None, min_length=1, max_length=50)
    stock: int | None = Field(default=None, ge=0)


class ProductRead(ProductBase):
    """Schema restituito al client. Aggiunge l'id assegnato dal server."""

    id: int


class RestockPayload(BaseModel):
    """Body per POST /products/{id}/restock.

    Rappresenta una richiesta di rifornimento scorte: il client indica
    quanti pezzi aggiungere. Il server elabora la richiesta in background
    e risponde subito con 202 Accepted.
    """

    quantity: int = Field(gt=0, description="Quantità da aggiungere allo stock (> 0)")
