"""
Endpoint per consultare lo stato dei job asincroni.

Perché `def` e non `async def`?
--------------------------------
`get_job_status` non fa nessun I/O: legge un dict in memoria (`_jobs`),
operazione puramente CPU/memoria che dura microsecondi. FastAPI esegue i
`def` normali in un threadpool separato, ma per operazioni così brevi il
costo del context-switch al threadpool supera il beneficio. Si usa `def`
solo qui, dove è corretto.

Regola pratica:
  - I/O (DB, HTTP, file, socket)  → `async def` + libreria async
  - CPU / memoria pura            → `def` (o `async def` se già nell'event loop)

Pattern "polling":
------------------
Il client fa polling su GET /jobs/{job_id} finché lo status non diventa
"completed" o "failed". Alternative più sofisticate per produzione:
  - Server-Sent Events (SSE): connessione persistente, push dal server.
  - WebSocket: canale bidirezionale full-duplex.
  - Webhook: il server fa POST al client quando ha finito.
"""

from fastapi import APIRouter, HTTPException, status

from app import jobs

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
def get_job_status(job_id: str):
    """
    Controlla lo stato di un job asincrono.

    - **pending**: il task è ancora in esecuzione
    - **completed**: il task ha finito, `result` contiene il risultato
    - **failed**: il task è fallito, `result.error` contiene il messaggio
    """
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found",
        )
    return job
