"""
Store in memoria per i "job" asincroni.

Cos'è un job?
-------------
Quando un'operazione è troppo lenta per rispondere in modo sincrono
(es. chiamare un API esterna, elaborare un file, addestrare un modello),
il pattern standard è:

  1. Il server accetta la richiesta e risponde subito con 202 Accepted.
  2. L'operazione viene eseguita in background.
  3. Il client può interrogare lo stato con un secondo endpoint (polling).

Qui usiamo un semplice dict come store. In produzione si userebbe
Redis, una coda (Celery, RQ, ARQ) o un sistema di job scheduling.
"""

from uuid import uuid4

_jobs: dict[str, dict] = {}


def create_job() -> str:
    """Crea un nuovo job in stato 'pending' e restituisce il suo id."""
    job_id = str(uuid4())
    _jobs[job_id] = {"status": "pending", "result": None}
    return job_id


def complete_job(job_id: str, result: dict) -> None:
    """Segna un job come completato salvando il risultato."""
    if job_id in _jobs:
        _jobs[job_id] = {"status": "completed", "result": result}


def fail_job(job_id: str, error: str) -> None:
    """Segna un job come fallito (utile per errori nel background task)."""
    if job_id in _jobs:
        _jobs[job_id] = {"status": "failed", "result": {"error": error}}


def get_job(job_id: str) -> dict | None:
    """Restituisce lo stato di un job, o None se non esiste."""
    return _jobs.get(job_id)
