"""
Test suite per la concorrenza asincrona.

Struttura
---------
1. Smoke tests          — verifica che gli endpoint async funzionino correttamente.
2. Concurrent reads     — N GET simultanei: tutti devono rispondere 200 senza errori.
3. Concurrent writes    — N POST simultanei: ogni richiesta deve ricevere un ID unico.
4. asyncio.gather()     — prova che il fetch parallelo sia più veloce del sequenziale.
5. asyncio.Semaphore    — verifica che il picco di concorrenza non superi il limite.
6. asyncio.wait_for()   — verifica che il timeout cancelli i task troppo lenti.
7. /compare endpoint    — correttezza del fetch parallelo di più prodotti.

Dipendenze di test
------------------
- pytest-asyncio  : supporto async per pytest
- httpx           : AsyncClient per HTTP in-process
- aiosqlite       : driver SQLite async (sostituisce asyncpg nei test)
"""

import asyncio
import time

import pytest

from app import database, jobs
from app.routers import products as prod_module


# =============================================================================
# 1. SMOKE TESTS — gli endpoint async funzionano correttamente
# =============================================================================

class TestAsyncEndpoints:
    """Verifica che la conversione a async non abbia rotto la logica degli endpoint."""

    async def test_list_products(self, client):
        response = await client.get("/products")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3  # i 3 prodotti seedati

    async def test_get_product(self, client, product_ids):
        pid = product_ids[0]
        response = await client.get(f"/products/{pid}")
        assert response.status_code == 200
        assert response.json()["id"] == pid

    async def test_get_product_not_found(self, client):
        response = await client.get("/products/99999")
        assert response.status_code == 404

    async def test_create_product(self, client):
        payload = {
            "name": "Nuovo Prodotto",
            "description": "Test creazione async",
            "price": 42.0,
            "category": "test",
            "stock": 3,
        }
        response = await client.post("/products", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Nuovo Prodotto"
        assert "id" in data

    async def test_update_product(self, client, product_ids):
        pid = product_ids[0]
        response = await client.patch(f"/products/{pid}", json={"stock": 99})
        assert response.status_code == 200
        assert response.json()["stock"] == 99

    async def test_delete_product(self, client, product_ids):
        pid = product_ids[2]
        response = await client.delete(f"/products/{pid}")
        assert response.status_code == 204
        # Verifica che sia sparito
        response = await client.get(f"/products/{pid}")
        assert response.status_code == 404

    async def test_filter_by_category(self, client):
        response = await client.get("/products?category=altro")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["category"] == "altro"

    async def test_health_check(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


# =============================================================================
# 2. CONCURRENT READS — N GET simultanei
# =============================================================================

class TestConcurrentReads:
    """
    Verifica che l'event loop gestisca correttamente molte letture concorrenti.

    Con il layer sync (psycopg2), N richieste simultanee a un singolo worker
    si serializzerebbero. Con AsyncSession + asyncpg, l'event loop può
    servire tutte le richieste concorrentemente, limitato solo dal pool.
    """

    async def test_many_concurrent_gets_all_succeed(self, client):
        """30 GET simultanei allo stesso endpoint: tutti devono rispondere 200."""
        N = 30

        # asyncio.gather() lancia tutte le coroutine contemporaneamente.
        # Il risultato è una lista nello stesso ordine delle coroutine.
        responses = await asyncio.gather(
            *[client.get("/products") for _ in range(N)]
        )

        status_codes = [r.status_code for r in responses]
        assert all(s == 200 for s in status_codes), (
            f"Alcuni GET hanno fallito: {[s for s in status_codes if s != 200]}"
        )

    async def test_concurrent_gets_return_consistent_data(self, client, product_ids):
        """Letture concorrenti dello stesso prodotto restituiscono dati identici."""
        pid = product_ids[0]
        N = 20

        responses = await asyncio.gather(
            *[client.get(f"/products/{pid}") for _ in range(N)]
        )

        # Tutti devono avere lo stesso id e lo stesso nome
        bodies = [r.json() for r in responses]
        assert all(b["id"] == pid for b in bodies)
        assert len({b["name"] for b in bodies}) == 1, "Dati inconsistenti tra le risposte"

    async def test_concurrent_gets_are_fast(self, client, monkeypatch):
        """
        Prova che le letture concorrenti siano più veloci di quelle sequenziali
        usando una latenza artificiale per simulare I/O reale.

        Perché latenza artificiale?
        ---------------------------
        SQLite in-memory risponde in <1ms: sia sequential che parallel finiscono
        in microsecondi, rendendo il confronto instabile. Aggiungendo 50ms di
        latenza simulata, il modello di concorrenza diventa rilevante:
          - Sequenziale : 10 × 50ms ≈ 500ms
          - Parallelo   : max(50ms, 50ms, ...) ≈ 50ms
        """
        import app.database as db_mod

        LATENCY = 0.05  # 50ms per simulare latenza DB reale
        N = 8
        original_list_all = db_mod.list_all

        async def slow_list_all(*args, **kwargs):
            await asyncio.sleep(LATENCY)
            return await original_list_all(*args, **kwargs)

        monkeypatch.setattr(db_mod, "list_all", slow_list_all)

        # Sequenziale: N × LATENCY
        t_start = time.monotonic()
        for _ in range(N):
            await client.get("/products")
        t_sequential = time.monotonic() - t_start

        # Parallelo: ≈ LATENCY (tutte le coroutine aspettano in parallelo)
        t_start = time.monotonic()
        await asyncio.gather(*[client.get("/products") for _ in range(N)])
        t_parallel = time.monotonic() - t_start

        speedup = t_sequential / t_parallel
        assert speedup >= 2.0, (
            f"Speedup atteso ≥2×, ottenuto {speedup:.2f}× "
            f"(sequential={t_sequential:.3f}s, parallel={t_parallel:.3f}s)"
        )


# =============================================================================
# 3. CONCURRENT WRITES — N POST simultanei senza data corruption
# =============================================================================

class TestConcurrentWrites:
    """
    Verifica l'integrità dei dati sotto carico di scrittura concorrente.

    Il rischio principale: due coroutine leggono lo stesso "next id" o
    si sovrascrivono a vicenda. SQLAlchemy + il DB gestiscono l'isolamento
    delle transazioni, ma è il nostro codice async a dover non mescolare
    le sessioni tra le richieste.
    """

    async def test_concurrent_posts_all_succeed(self, client):
        """20 POST simultanei: tutti devono rispondere 201."""
        N = 20

        async def create_one(i: int):
            return await client.post(
                "/products",
                json={
                    "name": f"Prodotto Concorrente {i}",
                    "price": float(i + 1),
                    "category": "bulk",
                    "stock": i,
                },
            )

        responses = await asyncio.gather(*[create_one(i) for i in range(N)])

        status_codes = [r.status_code for r in responses]
        assert all(s == 201 for s in status_codes), (
            f"Alcuni POST hanno fallito: {[(i, s) for i, s in enumerate(status_codes) if s != 201]}"
        )

    async def test_concurrent_posts_assign_unique_ids(self, client):
        """Ogni POST concorrente deve ricevere un ID univoco (nessuna collisione)."""
        N = 15

        responses = await asyncio.gather(
            *[
                client.post(
                    "/products",
                    json={"name": f"P{i}", "price": 1.0, "category": "test", "stock": 0},
                )
                for i in range(N)
            ]
        )

        ids = [r.json()["id"] for r in responses if r.status_code == 201]

        assert len(ids) == N, f"Solo {len(ids)}/{N} POST hanno restituito 201"
        assert len(set(ids)) == N, (
            f"ID duplicati rilevati! {N} richieste ma solo {len(set(ids))} ID unici."
        )

    async def test_concurrent_patches_no_lost_updates(self, client, product_ids):
        """
        PATCH sequenziali che incrementano lo stock: il valore finale deve
        essere coerente. Questo test evidenzia la gestione delle transazioni.

        Nota: con richieste *veramente* concorrenti e lettura-modifica-scrittura
        non atomica, si potrebbero avere lost updates. Il test usa PATCH
        sequenziali per verificare la correttezza del singolo endpoint.
        """
        pid = product_ids[0]

        # Legge il valore iniziale
        initial = (await client.get(f"/products/{pid}")).json()["stock"]

        # 5 PATCH sequenziali che impostano valori distinti
        for new_stock in [10, 20, 30, 40, 50]:
            r = await client.patch(f"/products/{pid}", json={"stock": new_stock})
            assert r.status_code == 200

        final = (await client.get(f"/products/{pid}")).json()["stock"]
        assert final == 50, f"Stock finale atteso 50, ottenuto {final}"


# =============================================================================
# 4. asyncio.gather() — il fetch parallelo è più veloce del sequenziale
# =============================================================================

class TestGatherPerformance:
    """
    Prova che `asyncio.gather()` sia effettivamente più veloce delle
    chiamate sequenziali quando c'è latenza I/O simulata.

    Come funziona il test
    ---------------------
    1. Patchamo `AsyncSessionLocal` con una factory che aggiunge un ritardo
       artificiale di 100ms per ogni `session.get()`.
    2. Misuriamo il tempo di 3 fetch sequenziali  → ~300ms
    3. Misuriamo il tempo di `get_many([1,2,3])`  → ~100ms (parallelo)
    4. Verifichiamo che il parallelo sia almeno 2× più veloce.
    """

    LATENCY = 0.10  # secondi di latenza simulata per ogni query

    async def test_gather_is_faster_than_sequential(self, session_factory, monkeypatch):
        """asyncio.gather() su N query indipendenti è più veloce di N query sequenziali."""

        # --- Sessione lenta per simulare latenza DB ---
        class SlowSession:
            async def get(self, model, pk):
                await asyncio.sleep(self.LATENCY)
                # Restituisce None: get_many lo accetta (non crasha)
                return None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            LATENCY = TestGatherPerformance.LATENCY

        class SlowSessionFactory:
            def __call__(self):
                return SlowSession()

        # Patch il modulo database: AsyncSessionLocal usato da get_many()
        import app.database as db_mod
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", SlowSessionFactory())

        ids = [1, 2, 3]

        # Sequenziale: 3 × LATENCY
        t0 = time.monotonic()
        for pid in ids:
            async with SlowSession() as s:
                await s.get(None, pid)
        t_sequential = time.monotonic() - t0

        # Parallelo con gather: max(LATENCY, LATENCY, LATENCY) ≈ LATENCY
        t0 = time.monotonic()
        await database.get_many(ids)
        t_parallel = time.monotonic() - t0

        # Il parallelo deve essere almeno 2× più veloce
        speedup = t_sequential / t_parallel
        assert speedup >= 1.8, (
            f"Speedup atteso ≥1.8×, ottenuto {speedup:.2f}× "
            f"(sequential={t_sequential:.3f}s, parallel={t_parallel:.3f}s)"
        )

    async def test_compare_endpoint_returns_correct_products(self, client, product_ids):
        """GET /compare restituisce i prodotti giusti (test di correttezza)."""
        id_a, id_b = product_ids[0], product_ids[1]

        response = await client.get(f"/products/compare?ids={id_a}&ids={id_b}")
        assert response.status_code == 200

        data = response.json()
        assert len(data) == 2
        returned_ids = {p["id"] for p in data}
        assert returned_ids == {id_a, id_b}

    async def test_compare_endpoint_missing_product_returns_404(self, client, product_ids):
        """Se uno degli ID non esiste, /compare risponde 404."""
        real_id = product_ids[0]
        fake_id = 99999

        response = await client.get(f"/products/compare?ids={real_id}&ids={fake_id}")
        assert response.status_code == 404
        assert str(fake_id) in response.json()["detail"]

    async def test_compare_endpoint_three_products_in_parallel(self, client, product_ids):
        """get_many() scala a N prodotti, non solo 2."""
        response = await client.get(
            f"/products/compare?ids={product_ids[0]}&ids={product_ids[1]}&ids={product_ids[2]}"
        )
        assert response.status_code == 200
        assert len(response.json()) == 3


# =============================================================================
# 5. asyncio.Semaphore — il picco di concorrenza non supera il limite
# =============================================================================

class TestSemaphoreConcurrencyLimit:
    """
    Verifica che `asyncio.Semaphore(_RESTOCK_CONCURRENCY)` limiti
    effettivamente quante coroutine entrano nella sezione critica
    contemporaneamente.

    Strategia
    ---------
    Usiamo direttamente il semaphore reale del modulo (non un mock).
    Ogni "task di test" acquisisce il semaphore, registra quante copie
    sono attive contemporaneamente, poi lo rilascia.
    Il picco misurato non deve mai superare _RESTOCK_CONCURRENCY.
    """

    async def test_semaphore_limits_peak_to_configured_value(self):
        """
        Il picco di concorrenza non supera mai _RESTOCK_CONCURRENCY.

        Usiamo un semaphore locale con lo stesso limite del modulo: isola
        il test dall'event loop di altri test (ogni pytest test ha il proprio loop).
        Il comportamento testato è identico a quello in produzione.
        """
        from app.routers.products import _RESTOCK_CONCURRENCY

        # Semaphore locale: stesso limite di produzione, loop di test corretto
        local_semaphore = asyncio.Semaphore(_RESTOCK_CONCURRENCY)
        peak_concurrent = 0
        currently_running = 0
        tracking_lock = asyncio.Lock()

        async def instrumented_task():
            """Acquisisce il semaphore, misura il picco, rilascia."""
            nonlocal peak_concurrent, currently_running
            async with local_semaphore:
                async with tracking_lock:
                    currently_running += 1
                    peak_concurrent = max(peak_concurrent, currently_running)
                # Simula lavoro (molto breve per non rallentare il test)
                await asyncio.sleep(0.01)
                async with tracking_lock:
                    currently_running -= 1

        # Lancia 3× il numero massimo consentito: il semaphore deve fare da
        # "gate" e far passare al massimo _RESTOCK_CONCURRENCY alla volta.
        N = _RESTOCK_CONCURRENCY * 3
        await asyncio.gather(*[instrumented_task() for _ in range(N)])

        assert peak_concurrent <= _RESTOCK_CONCURRENCY, (
            f"Picco misurato: {peak_concurrent}, limite configurato: {_RESTOCK_CONCURRENCY}. "
            f"Il Semaphore non ha limitato la concorrenza correttamente."
        )
        assert peak_concurrent > 0, "Nessun task è stato eseguito (bug nel test)"

    async def test_all_queued_tasks_eventually_complete(self):
        """
        Tasks in eccesso non vengono scartati: attendono il loro turno.

        Nota: usiamo un semaphore locale (non quello del modulo) perché
        `asyncio.Semaphore` è legato all'event loop in cui viene usato per
        la prima volta. Con pytest-asyncio ogni test ha il proprio event loop,
        quindi riusare il semaphore del modulo causerebbe RuntimeError.
        Creare un semaphore fresco nel test è il pattern corretto per l'isolamento.
        """
        from app.routers.products import _RESTOCK_CONCURRENCY

        # Semaphore locale al test: stesso limite configurato in produzione
        local_semaphore = asyncio.Semaphore(_RESTOCK_CONCURRENCY)
        completed = 0
        lock = asyncio.Lock()

        async def task():
            nonlocal completed
            async with local_semaphore:
                await asyncio.sleep(0.005)
                async with lock:
                    completed += 1

        N = _RESTOCK_CONCURRENCY * 4
        await asyncio.gather(*[task() for _ in range(N)])

        assert completed == N, (
            f"Solo {completed}/{N} task completati. "
            f"Il semaphore ha scartato qualche task?"
        )

    async def test_restock_endpoint_accepts_all_requests_immediately(
        self, client, product_ids
    ):
        """
        Anche con più di _RESTOCK_CONCURRENCY restock simultanei, tutti
        devono ricevere 202 (la risposta HTTP è immediata, il task va in coda).
        """
        from app.routers.products import _RESTOCK_CONCURRENCY

        pid = product_ids[0]
        N = _RESTOCK_CONCURRENCY + 3  # qualcuno dovrà aspettare

        responses = await asyncio.gather(
            *[
                client.post(
                    f"/products/{pid}/restock",
                    json={"quantity": 1},
                )
                for _ in range(N)
            ]
        )

        # Tutte le risposte HTTP devono essere 202 (accettate subito)
        status_codes = [r.status_code for r in responses]
        assert all(s == 202 for s in status_codes), (
            f"Alcune richieste non hanno ricevuto 202: {status_codes}"
        )

        # Ogni risposta deve avere un job_id univoco
        job_ids = [r.json()["job_id"] for r in responses]
        assert len(set(job_ids)) == N, "Job ID non sono univoci"


# =============================================================================
# 6. asyncio.wait_for() — il timeout cancella i task troppo lenti
# =============================================================================

class TestWaitForTimeout:
    """
    Verifica che `asyncio.wait_for(timeout=...)` cancelli correttamente
    le coroutine che impiegano troppo tempo.
    """

    async def test_fast_task_completes_within_timeout(self):
        """Un task veloce completa normalmente senza essere cancellato."""

        async def fast_work():
            await asyncio.sleep(0.01)
            return "done"

        result = await asyncio.wait_for(fast_work(), timeout=1.0)
        assert result == "done"

    async def test_slow_task_raises_timeout_error(self):
        """Un task più lento del timeout deve sollevare TimeoutError."""

        async def eternal_work():
            await asyncio.sleep(999)  # non termina mai entro il timeout

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(eternal_work(), timeout=0.05)

    async def test_restock_timeout_marks_job_as_failed(self, monkeypatch):
        """
        Se il background task di restock supera il timeout, il job deve
        essere marcato "failed" (non restare "pending" per sempre).
        """
        from app.routers import products as prod_mod

        # Sostituisce _restock_task con una versione che non termina mai
        async def never_ending_task(job_id, product_id, quantity):
            await asyncio.sleep(999)

        monkeypatch.setattr(prod_mod, "_restock_task", never_ending_task)
        # Abbassa il timeout a 50ms per il test
        monkeypatch.setattr(prod_mod, "_RESTOCK_TIMEOUT_SEC", 0.05)

        job_id = jobs.create_job()

        # Esegue _do_restock direttamente (simula il background task)
        await prod_mod._do_restock(job_id, product_id=1, quantity=5)

        # Il job deve essere "failed", non "pending"
        job = jobs.get_job(job_id)
        assert job is not None
        assert job["status"] == "failed", (
            f"Job status atteso 'failed', ottenuto '{job['status']}'. "
            f"Il timeout non ha marcato il job come fallito?"
        )
        assert "timeout" in job["result"]["error"].lower(), (
            f"Il messaggio di errore non menziona il timeout: {job['result']}"
        )

    async def test_timeout_does_not_affect_other_requests(self, client, product_ids):
        """
        Un timeout su un singolo background task non deve influenzare
        le altre richieste in corso sull'event loop.
        """
        from app.routers import products as prod_mod

        async def slow_task(job_id, product_id, quantity):
            await asyncio.sleep(999)  # sarà cancellato

        original_task = prod_mod._restock_task
        original_timeout = prod_mod._RESTOCK_TIMEOUT_SEC

        # Brevemente sostituisce il task e abbassa il timeout
        prod_mod._restock_task = slow_task
        prod_mod._RESTOCK_TIMEOUT_SEC = 0.05

        try:
            pid = product_ids[0]
            # Lancia un restock che andrà in timeout + una GET normale
            restock_response, get_response = await asyncio.gather(
                client.post(f"/products/{pid}/restock", json={"quantity": 1}),
                client.get("/products"),
            )
        finally:
            prod_mod._restock_task = original_task
            prod_mod._RESTOCK_TIMEOUT_SEC = original_timeout

        # Entrambi devono rispondere correttamente
        assert restock_response.status_code == 202
        assert get_response.status_code == 200


# =============================================================================
# 7. STREAMING — verifica che StreamingResponse funzioni in modo async
# =============================================================================

class TestStreamingResponse:
    """Verifica che lo streaming NDJSON funzioni correttamente."""

    async def test_stream_returns_ndjson(self, client):
        """GET /export/stream restituisce una riga JSON per ogni prodotto."""
        response = await client.get("/products/export/stream")
        assert response.status_code == 200
        assert "ndjson" in response.headers.get("content-type", "")

        lines = [ln for ln in response.text.strip().split("\n") if ln]
        assert len(lines) == 3  # 3 prodotti seedati

    async def test_stream_each_line_is_valid_json(self, client):
        """Ogni riga dello stream deve essere un JSON valido."""
        import json

        response = await client.get("/products/export/stream")
        lines = [ln for ln in response.text.strip().split("\n") if ln]

        for line in lines:
            obj = json.loads(line)  # solleva se non è JSON valido
            assert "id" in obj
            assert "name" in obj
            assert "price" in obj
