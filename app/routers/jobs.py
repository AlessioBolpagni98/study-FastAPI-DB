"""
Endpoint per consultare lo stato dei job asincroni.

Perché un router separato?
--------------------------
I job non sono una risorsa "prodotto": sono un concetto trasversale
usabile da qualsiasi risorsa futura (ordini, fatture, export, ...).
Tenerli in un router separato rispetta il principio di separazione
delle responsabilità e li rende facilmente estendibili.

Pattern "polling":
------------------
Il client fa polling su GET /jobs/{job_id} finché lo status
non diventa "completed" o "failed". È il pattern più semplice.
Alternative più sofisticate:
  - Webhook: il server notifica il client quando ha finito.
  - WebSocket / SSE: connessione persistente, il server "pushes" lo stato.
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
