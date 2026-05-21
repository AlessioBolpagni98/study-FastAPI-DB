# Concetti: Docker e Docker Compose

Concetti trattati in questo progetto relativi alla containerizzazione e all'orchestrazione.

---

## Container vs VM — la differenza fondamentale

Una **Virtual Machine** emula un intero computer: ha il suo kernel, il suo OS, i suoi driver. È pesante (GB di spazio, secondi per avviarsi).

Un **container** è qualcosa di molto più leggero: condivide il kernel del sistema operativo host e isola solo il processo, il filesystem e la rete. Si avvia in millisecondi e pesa MB.

```
┌──────────────────────────────────┐    ┌──────────────────────────────────┐
│           VIRTUAL MACHINE        │    │             CONTAINER            │
│  ┌────────┐  ┌────────┐          │    │  ┌────────┐  ┌────────┐          │
│  │ App A  │  │ App B  │          │    │  │ App A  │  │ App B  │          │
│  ├────────┤  ├────────┤          │    │  ├────────┤  ├────────┤          │
│  │ Guest  │  │ Guest  │          │    │  │ Libs A │  │ Libs B │          │
│  │   OS   │  │   OS   │          │    │  └────────┘  └────────┘          │
│  └────────┘  └────────┘          │    │         Docker Engine            │
│         Hypervisor               │    │            Host OS               │
│           Host OS                │    └──────────────────────────────────┘
└──────────────────────────────────┘
```

**Analogia:** la VM è come affittare un appartamento intero. Il container è come affittare una stanza in un co-living — cucina e connessione condivise, spazio privato garantito.

---

## Immagine Docker

Un'**immagine** è un template immutabile da cui si creano i container (come uno stampino). Pensa a essa come a una "fotografia" del filesystem dell'applicazione: codice, dipendenze, configurazione.

Le immagini sono composte da **layer** (strati) impilati. Ogni istruzione del Dockerfile aggiunge un layer. I layer sono immutabili e vengono riusati tra build diverse → risparmio di spazio e tempo.

```
┌──────────────────────────────┐   layer 4: COPY app/ (il tuo codice)
├──────────────────────────────┤   layer 3: RUN pip install (dipendenze)
├──────────────────────────────┤   layer 2: COPY requirements.txt
├──────────────────────────────┤   layer 1: python:3.14-slim (immagine base)
└──────────────────────────────┘
```

Le immagini vengono distribuite tramite **registry**. Il registry pubblico di default è [Docker Hub](https://hub.docker.com). `FROM python:3.14-slim` scarica l'immagine ufficiale Python da Docker Hub.

```bash
docker images                  # lista immagini locali
docker pull postgres:16        # scarica un'immagine senza avviarla
docker rmi nome-immagine       # rimuove un'immagine locale
```

---

## Dockerfile — istruzione per istruzione

Il [Dockerfile](Dockerfile) di questo progetto:

```dockerfile
FROM python:3.14-slim
```
**Immagine base.** Parte da un'immagine Python 3.14 ufficiale nella variante `slim` — include solo lo stretto necessario per eseguire Python (niente compilatori, niente strumenti di debug). Più piccola e più sicura.

```dockerfile
WORKDIR /app
```
**Directory di lavoro.** Tutte le istruzioni successive (`COPY`, `RUN`, `CMD`) operano relative a questa path. Se non esiste, la crea. È buona pratica usare una directory dedicata invece della root `/`.

```dockerfile
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
```
**Dipendenze prima del codice** — questa è la tecnica più importante del Dockerfile. Vedi la sezione [Layer cache](#layer-cache--il-trucco-più-importante) sotto.

`--no-cache-dir` evita che pip scarichi la cache sul disco → immagine più piccola.

```dockerfile
COPY app/ ./app/
```
**Codice sorgente.** Copia solo la cartella `app/`. Il `.dockerignore` esclude tutto il resto (`.venv`, `.env`, cache, ecc.).

```dockerfile
EXPOSE 8000
```
**Dichiarazione di porta.** È solo documentazione — dice "questo container ascolta sulla porta 8000". Non apre nessuna porta di per sé. Chi apre effettivamente le porte è `docker run -p` oppure docker-compose.

```dockerfile
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```
**Comando di avvio.** Viene eseguito quando il container parte. `--host 0.0.0.0` è fondamentale: senza di esso uvicorn ascolterebbe solo su `127.0.0.1` interno al container, invisibile dall'esterno. Non si usa `fastapi dev` (che è per sviluppo locale con auto-reload).

---

## Layer cache — il trucco più importante

Docker non ribuild ogni layer ad ogni `docker build`. Se un layer non è cambiato (stesso hash), lo riusa dalla cache. **Ma appena un layer cambia, tutti i layer successivi vengono ribuildati.**

```
# ❌ SBAGLIATO — se cambia una riga di codice, pip install riparte da zero
COPY . .
RUN pip install -r requirements.txt

# ✅ GIUSTO — pip install viene rieseguito solo se requirements.txt cambia
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app/ ./app/          ← questo layer cambia spesso, ma è DOPO pip install
```

In pratica: metti le cose che cambiano raramente in cima, quelle che cambiano spesso in fondo.

---

## Container — da immagine a runtime

Un **container** è un'istanza in esecuzione di un'immagine. Dalla stessa immagine si possono avviare N container identici.

```bash
docker build -t product-catalog .        # builda l'immagine dal Dockerfile
docker run -p 8000:8000 product-catalog  # avvia un container

docker ps                                # container in esecuzione
docker ps -a                             # tutti i container (anche fermi)
docker logs <container-id>               # log del container
docker exec -it <container-id> bash      # terminale interattivo nel container
docker stop <container-id>               # ferma un container
docker rm <container-id>                 # rimuove un container fermo
```

La sintassi `-p 8000:8000` significa `porta-host:porta-container`. Il traffico che arriva sulla porta 8000 del tuo Mac viene inoltrato alla porta 8000 dentro il container.

---

## Docker Compose — orchestrazione multi-container

Avviare manualmente due container con le opzioni giuste è noioso e error-prone. **Docker Compose** permette di dichiarare l'intera architettura in un file YAML ed avviarla con un singolo comando.

Il [docker-compose.yml](docker-compose.yml) di questo progetto:

```yaml
services:
  db:
    image: postgres:16                        # usa l'immagine ufficiale, non builda
    environment:
      POSTGRES_PASSWORD: yourpassword
      POSTGRES_DB: products_db
    volumes:
      - postgres_data:/var/lib/postgresql/data  # persistenza dati
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 5                              # considera il db "healthy" dopo 5 check ok

  api:
    build: .                                  # builda dal Dockerfile locale
    ports:
      - "8000:8000"                           # solo l'api espone porte all'host
    environment:
      DATABASE_URL: postgresql+psycopg2://postgres:yourpassword@db:5432/products_db
    depends_on:
      db:
        condition: service_healthy            # aspetta che db sia pronto

volumes:
  postgres_data:                              # volume named dichiarato qui
```

```bash
docker compose up --build     # builda e avvia tutti i servizi (log in foreground)
docker compose up -d          # avvia in background (detached)
docker compose down           # ferma e rimuove i container
docker compose down -v        # come sopra + rimuove anche i volumi
docker compose logs api       # log solo del servizio api
docker compose ps             # stato dei servizi
```

---

## Rete Docker — come i container si parlano

Quando Docker Compose avvia più servizi, crea automaticamente una **rete virtuale privata** a cui tutti i container dello stesso compose file sono connessi.

All'interno di questa rete ogni container è raggiungibile tramite il **nome del servizio** come hostname. Per questo nel `docker-compose.yml` la `DATABASE_URL` usa `@db:5432` e non `@localhost:5432`:

```
┌──────────────────────────────────────────────┐
│              Rete Docker Compose              │
│                                              │
│  ┌─────────────┐         ┌────────────────┐  │
│  │  api        │──────►  │  db            │  │
│  │  :8000      │  @db    │  :5432         │  │
│  └─────────────┘         └────────────────┘  │
│         │                                    │
└─────────┼────────────────────────────────────┘
          │ -p 8000:8000
     Host Mac :8000
```

`localhost` dentro un container fa riferimento al container stesso, non all'host né agli altri container. Il DNS interno di Docker risolve `db` all'IP del container PostgreSQL.

---

## Volumi — persistenza dei dati

Per default, il filesystem di un container è **effimero**: quando il container viene rimosso, tutti i dati scritti dentro vengono persi. PostgreSQL scriverebbe i dati nel container, ma al prossimo `docker compose down` sparirebbe tutto.

I **volumi** risolvono questo problema. Docker gestisce un'area di storage sul filesystem host, montata dentro il container.

```
Host Mac                          Container db
/var/lib/docker/volumes/          /var/lib/postgresql/data
  postgres_data/_data/   ◄───────►  (PostgreSQL scrive qui)
```

Due tipi principali:

| Tipo | Sintassi | Usato per |
|---|---|---|
| **Volume named** | `postgres_data:/var/lib/postgresql/data` | Dati DB, gestiti da Docker |
| **Bind mount** | `./app:/app` | Sviluppo locale con hot reload |

Il progetto usa un **volume named** per il DB: i dati sopravvivono a `docker compose down` ma non a `docker compose down -v` (che cancella esplicitamente i volumi).

---

## Avvio rapido con Docker

```bash
# Prima volta — builda l'immagine e avvia entrambi i container
docker compose up --build

# Volte successive — avvia senza ribuildare
docker compose up -d

# Verifica che tutto funzioni
curl http://localhost:8000/health
# {"status":"ok","database":"connected"}

# Apri la Swagger UI
# http://localhost:8000/docs

# Ferma tutto (i dati restano nel volume)
docker compose down

# Ferma tutto e cancella anche i dati
docker compose down -v
```

---

## Comandi di riferimento

| Comando | Cosa fa |
|---|---|
| `docker build -t nome .` | Builda un'immagine dal Dockerfile nella directory corrente |
| `docker images` | Lista le immagini locali |
| `docker run -p 8000:8000 nome` | Avvia un container dall'immagine |
| `docker ps` | Container in esecuzione |
| `docker logs <id>` | Log di un container |
| `docker exec -it <id> bash` | Shell interattiva nel container |
| `docker stop <id>` | Ferma un container |
| `docker rm <id>` | Rimuove un container fermo |
| `docker rmi nome` | Rimuove un'immagine |
| `docker compose up --build` | Builda e avvia tutti i servizi |
| `docker compose up -d` | Avvia in background |
| `docker compose down` | Ferma e rimuove i container |
| `docker compose down -v` | Come sopra + rimuove i volumi |
| `docker compose logs <servizio>` | Log di un singolo servizio |
| `docker compose ps` | Stato dei servizi |
| `docker volume ls` | Lista i volumi Docker |
