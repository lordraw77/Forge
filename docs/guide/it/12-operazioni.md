# 12. Installare e gestire Forge

[Indice](README.md) · Precedente: [11. Console Super Admin](11-amministrazione.md)

Questo capitolo è per chi **installa, aggiorna, protegge e controlla** Forge. È un percorso guidato: ogni attività dice cosa eseguire, cosa dovresti vedere e cosa fare se non lo vedi. Tutto si fa in un terminale, quindi non ci sono schermate; i passaggi nell'interfaccia web dopo l'installazione sono nei capitoli precedenti.

> **A chi serve?** Ad amministratori di sistema e DevOps. Ti servono: un host Linux con Docker (Compose v2), GNU Make e OpenSSL per l'installazione su una sola macchina; oppure un cluster Kubernetes/OpenShift e Helm per l'installazione in produzione. Prevedi almeno **8 GB di RAM** e diversi core per un host di laboratorio che esegue tutto su una macchina (OpenSearch da solo usa 1 GB).

## 12.1 Di cosa è fatto Forge

| Componente | Ruolo | Se si ferma |
| --- | --- | --- |
| **web** | L'interfaccia web (nginx); inoltra `/api` e `/v1` all'API. | Niente interfaccia. |
| **api** | L'API REST e l'API compatibile con OpenAI; esegue anche le pianificazioni dei trigger. | Non funziona nulla. |
| **worker-runs** | Esegue casi d'uso e agenti (worker Temporal). | Le esecuzioni restano in coda. |
| **worker-ingest** | Legge, divide e crea gli embedding dei documenti; esegue le sincronizzazioni delle fonti. | I documenti restano *In corso*. |
| **PostgreSQL + pgvector** | Tutta la configurazione, i contenuti, le esecuzioni, il registro di audit, i vettori. | La piattaforma si ferma. **Il componente più prezioso.** |
| **OpenSearch** | Indice per parole chiave (BM25) del RAG. | I risultati per parole chiave si perdono; si ricostruisce da PostgreSQL. |
| **Temporal** | Esecuzione durevole di esecuzioni e ingestioni. | Esecuzioni e ingestioni si fermano, poi riprendono. |
| **Valkey** | Sessioni, limiti di frequenza, quote, cache. | Tutti vengono disconnessi. |
| **Storage S3** (SeaweedFS o il tuo S3) | Documenti caricati e file delle esecuzioni. | I documenti non si aprono né si rielaborano. |
| *Facoltativi* | stack di osservabilità, runner sandbox, vault, backup (sotto). | Solo la loro funzione. |

Forge **non ospita mai un modello**: ogni modello è un endpoint esterno (un'API cloud, oppure un server che gestisci tu, come vLLM o Ollama).

## 12.2 Installare su una macchina (Docker Compose)

Per laboratori, dimostrazioni e piccoli team.

1. **Ottieni i sorgenti** e apri un terminale nella loro cartella.
2. **Genera la configurazione**:

   ```bash
   make env
   ```

   Crea `.env` (le impostazioni di avvio e la **chiave principale**) e `deploy/compose/infra.env` (le password generate dell'infrastruttura). Entrambi sono privati (modo `600`) e non vengono mai committati. I file esistenti vengono mantenuti.
3. **Costruisci e avvia**:

   ```bash
   make docker/build      # costruisce forge-backend e forge-web (la prima volta richiede alcuni minuti)
   make docker/run        # avvia tutto e attende che ogni servizio sia sano
   make docker/smoke      # verifica end-to-end facoltativa
   ```

   Se `make docker/run` si ferma con *unhealthy*, esegui `make ps`, poi `make logs SERVICE=<nome>`.
4. **Apri** `http://localhost:8080` (porta `FORGE_HTTP_PORT`).
5. **Primo accesso.** Con il database vuoto Forge crea un Super Admin da `FORGE_ADMIN_USERNAME`, `FORGE_ADMIN_EMAIL` e `FORGE_ADMIN_PASSWORD` in `.env`. Accedi, scegli una nuova password (12+ caratteri), accetta le condizioni (capitolo 1) — poi **elimina `FORGE_ADMIN_PASSWORD` da `.env`**: viene letta solo per creare il primo account.
6. **Fai un backup separato di `FORGE_MASTER_KEY`.** Cifra ogni segreto nel database. Se si perde, le chiavi dei provider e i token memorizzati non si possono recuperare.

### Aggiungere i modelli

Apri **Console Super Admin → Provider di modelli** (capitolo 11), aggiungi il tuo provider e i modelli, abilitali per un progetto e punta gli alias `default-chat`, `fast`, `embedding`, `guard`, `rerank` su di essi. Per un laboratorio, `make providers/import` registra ogni provider la cui chiave è presente in `.env` (`OLLAMA_HOST`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, …).

### Comandi di ogni giorno

| Comando | Cosa fa |
| --- | --- |
| `make ps` | Stato dei servizi. |
| `make logs SERVICE=api` | Segue i log di un servizio. |
| `make stop` | Ferma i container, conserva i dati. |
| `make docker/down` | Rimuove container **e volumi** — distrugge i dati locali. |
| `make migrate` | Applica le migrazioni del database. |
| `make admin/reset` | Reimposta o crea un Super Admin e lo sblocca (chiede una password temporanea; richiede accesso shell all'host). `make admin/reset ADMIN_USER=alice` per un altro utente. |

### Componenti facoltativi (profili Compose)

| Comando | Aggiunge |
| --- | --- |
| `make obs/up` | Osservabilità: collector OpenTelemetry, Prometheus, Loki, Grafana (Grafana su `127.0.0.1:3000`). |
| `make sandbox/up` | Il **runner sandbox** per strumenti Python e nodi `code` (capitolo 5). Tiene il socket del motore dei container, quindi eseguilo solo su host dove è accettabile. |
| `make pitr/up` | Backup continui del database (sezione 12.6). |
| `make vault/up` | Un **OpenBao** da laboratorio; poi `scripts/vault-setup.sh` e configuralo in **Super Admin → Vault dei segreti** (capitolo 11). |
| Profilo Langfuse | Uno strumento facoltativo di osservabilità degli LLM alimentato da OpenTelemetry; spento per impostazione predefinita. |

## 12.3 La configurazione che ti servirà

Forge legge la configurazione solo da tre posti:

| Dove | Contenuto |
| --- | --- |
| `.env` | Avvio: primo admin, `FORGE_MASTER_KEY`, `FORGE_PUBLIC_URL`, proxy. |
| `deploy/compose/infra.env` | Password e chiavi dell'infrastruttura, generate da `make env`. |
| Il database, modificato nell'interfaccia web | Modelli, autenticazione, email, guardrail, limiti, vault, conservazione… (capitolo 11). |

Le impostazioni su cui gli amministratori chiedono di più:

| Esigenza | Impostazione |
| --- | --- |
| **Proxy aziendale** | `FORGE_HTTP_PROXY`, `FORGE_HTTPS_PROXY`, `FORGE_NO_PROXY` in `.env` (i servizi di Forge sono sempre esclusi). Se il proxy ispeziona il TLS, metti il certificato radice aziendale in `deploy/compose/extra-ca/` e imposta `FORGE_EXTRA_CA_FILE`. LDAP non usa il proxy HTTP. |
| **Reverse proxy davanti a Forge** | `FORGE_TRUSTED_PROXIES` (così gli indirizzi dei visitatori dei widget e i limiti di frequenza si leggono dagli header giusti) e `FORGE_PUBLIC_URL`. |
| **Email per le approvazioni** | Nella console (**Email**, capitolo 11), oppure le variabili `FORGE_SMTP_*`. |
| **MCP via stdio** | Spento per impostazione predefinita; `FORGE_MCP_STDIO_ALLOWED` elenca i comandi consentiti (capitolo 5). |
| **API interne su indirizzi privati** | Bloccate per impostazione predefinita per strumenti e fonti; `FORGE_TOOLS_ALLOW_PRIVATE_HOSTS` le consente per l'intera installazione (capitolo 5). |
| **Fonti da cartella (SMB/NFS montati sul server)** | `FORGE_SOURCE_ROOTS`, una voce per ogni percorso montato con i progetti autorizzati a leggerlo (capitolo 4). |
| **Air-gapped / offline** | Le immagini predefinite portano con sé i modelli. `MODELS=download` costruisce immagini più piccole che li scaricano al primo avvio. |

## 12.4 Installare su Kubernetes o OpenShift (Helm)

Il chart `deploy/helm/forge` è lo stesso stack per un cluster.

```bash
helm install forge deploy/helm/forge -n forge --create-namespace \
  --set ingress.enabled=true --set ingress.host=forge.example.com
helm -n forge test forge
```

Senza valori il chart installa **tutto dentro la release** (una replica ciascuno) e **genera tutti i segreti**. Le note stampate alla fine ti dicono come leggere la password dell'amministratore e la **chiave principale: fai un backup della chiave principale**. I segreti e i volumi vengono mantenuti all'`helm uninstall` di proposito.

**Usa i tuoi servizi** in produzione: imposta `postgresql.enabled=false` e `external.postgresql.*` (PostgreSQL 17 con l'estensione `vector` e i ruoli di `files/init-databases.sh`), e allo stesso modo `external.valkey.*`, `external.s3.*`, `external.opensearch.*` e `external.temporal.*`. L'alta affidabilità di questi servizi è tua: dentro il chart ciascuno è un singolo pod.

| Tema | Come |
| --- | --- |
| Secret esistente | `existingSecret: nome` (chiavi elencate nel README del chart). |
| OpenShift | `openshift=true`, `route.enabled=true`, e PostgreSQL, OpenSearch e Temporal esterni (le loro immagini richiedono id utente fissi). **Non ancora provato su un vero cluster OpenShift.** |
| Proxy e CA privata | `proxy.*` ed `extraCA`. |
| Metriche | `PodMonitor` facoltativo per l'API e i worker (porta 9100). |
| Sandbox | Non distribuita dal chart: esegui un runner altrove e imposta `sandbox.url` e `sandbox.token`. |

> **Stato reale.** Il chart è verificato con lint e validazione, ma un'installazione su un vero cluster non è ancora stata verificata. Provalo prima in un namespace di test.

### Installazione air-gapped

```bash
scripts/airgap-bundle.sh ./airgap                         # su una macchina connessa: immagini, chart, checksum
scripts/airgap-load.sh registry.local:5000 ./airgap       # dentro la rete: verifica, carica, pubblica
helm install forge ./airgap/forge-*.tgz --set global.imageRegistry=registry.local:5000
```

## 12.5 Aggiornare

1. **Fai un backup** (sezione 12.6) e leggi le note di rilascio (`CHANGELOG.md`).
2. **Compose:** ottieni i nuovi sorgenti, poi `make docker/build` e `make docker/run`. Il servizio `migrate` applica le migrazioni del database prima che parta l'API.
3. **Helm:** `helm upgrade`. I pod dell'API migrano il database in un init container (un lock rende sicuri gli avvii paralleli) e i worker attendono l'API.
4. Controlla che l'**etichetta API** (in alto a destra di ogni pagina) mostri la nuova versione.

**Tornare indietro** con il software non fa tornare indietro il database: per annullare una migrazione sbagliata, ripristina da un backup a un punto nel tempo (12.6). Distribuisci sempre la **stessa versione** delle immagini backend e frontend.

## 12.6 Backup e ripristino

**Cosa proteggere, in ordine di importanza:**

| Elemento | Perché | Protezione |
| --- | --- | --- |
| **PostgreSQL** | Contiene tutto ciò che conta. | Archiviazione continua dei WAL più backup base giornalieri cifrati (sotto). |
| **`FORGE_MASTER_KEY`** | Senza di essa i segreti memorizzati sono illeggibili. | Una copia fuori dal cluster. |
| **`PITR_ENCRYPTION_KEY`** | Senza di essa i backup non si leggono. | Una copia fuori dal cluster. |
| **Storage S3** | I documenti caricati. | Versionamento o replica del tuo storage (non fa parte di Forge). |
| OpenSearch | Solo l'indice per parole chiave. | Si ricostruisce: `forge search reindex`. |
| Valkey | Sessioni e cache. | Nessuna necessaria. |

**Due chiavi decidono se un ripristino è possibile: la chiave principale e la chiave dei backup. Conservale entrambe, separate dai backup.**

### Attivare i backup

- **Compose:** `make pitr/up`. PostgreSQL archivia ogni segmento del log delle transazioni entro un minuto (**RPO di circa uno-due minuti**, obiettivo 15) e fa un backup base ogni 24 ore, conservando gli ultimi 7, tutto **compresso e cifrato** prima di lasciare l'host.
- **Kubernetes:** `postgresql.pitr.enabled=true`.
- Metti il bucket dei backup su **uno storage diverso da quello dei documenti** (`FORGE_BACKUP_S3_*` oppure `postgresql.pitr.s3.*`): un backup sullo stesso disco protegge dagli errori, non dalla perdita della macchina.
- **Sorveglialo:** il container di backup registra `base backup done` o `FAILED`; `SELECT last_archived_time, last_failed_time FROM pg_stat_archiver` deve continuare a muoversi; `wal-g backup-list` elenca i backup.

### Ripristinare (Compose)

Decidi se vuoi lo stato **più recente** o **un momento prima di un errore** (UTC):

```bash
make pitr/restore TIME='2026-10-22 09:30:00+00' CONFIRM=yes     # TIME vuoto = più recente
```

Lo script ferma lo stack, **conserva i dati attuali in un volume di sicurezza**, ripristina l'ultimo backup base, riapplica il log fino al momento scelto e riavvia tutto. Poi:

1. Accedi e controlla che sia tornato ciò che ti aspettavi (il registro di audit mostra l'ultima azione prima del momento scelto).
2. Ricostruisci l'indice per parole chiave: `docker compose … run --rm api forge search reindex`.
3. Tutti accedono di nuovo (Valkey non viene ripristinato); le esecuzioni in corso riprendono dallo stato che avevano.
4. Quando sei soddisfatto, rimuovi il volume di sicurezza.

Un ripristino su una **macchina nuova** richiede lo stesso `.env` (chiave principale!), la stessa chiave dei backup e le stesse impostazioni del bucket dei backup. La procedura per Kubernetes è scritta in `docs/operations/dr-runbook.md` ma **non è stata eseguita**. Il runbook elenca anche altre perdite (OpenSearch, storage, chiavi) e la prova misurata: ritardo 55 s, ripristino di 100 MB in 5 s.

> **Fai una prova di ripristino** (`make pitr/drill`) prima di averne bisogno. Un backup non provato è una speranza, non un backup.

## 12.7 Monitoraggio

- **Salute:** `/healthz` (è vivo) e `/readyz` (database, Valkey e ricerca sono raggiungibili) sull'API; `make ps` per i servizi.
- **Metriche:** ogni processo espone metriche Prometheus su una porta interna (9100 di default, non instradata dal server web). `make obs/up` avvia un Prometheus e un Grafana già pronti.
- **Trace e log:** trace OpenTelemetry (ogni esecuzione ha un *ID di traccia* mostrato nella vista dell'esecuzione, capitolo 6) e log JSON con id di traccia, id dell'esecuzione e id del progetto. Segreti e token sono oscurati nei log.
- **Segnali di prodotto:** **Analytics** e **Model Insights** (capitoli 9 e 11) mostrano errori, fallback, latenza e costi; il **registro di audit** mostra chi ha fatto cosa.

Cosa tenere d'occhio: la prontezza dell'API; il battito dei worker Temporal; i log dei backup; lo spazio su disco di PostgreSQL, OpenSearch e S3; i codici di errore in Analytics; la quota di fallback.

## 12.8 Capacità e prestazioni

Misurato su un host di laboratorio condiviso da 4 vCPU (i tuoi risultati varieranno): il gateway aggiunge circa **30 ms al 95° percentile** a una chiamata a un modello; un processo API ha sostenuto circa 50 chiamate al secondo; il recupero su 800 chunk ha richiesto 27 ms al 95° percentile; 200 esecuzioni di agenti concorrenti sono terminate tutte in 51 secondi; l'ingestione ha raggiunto circa 100 pagine al secondo per documenti da 10 pagine ma circa 10 pagine al secondo per documenti da una pagina (un costo fisso per documento). Un milione di chunk **non** è stato misurato. Si scala eseguendo più repliche dell'API e più worker, e dando a PostgreSQL e OpenSearch memoria sufficiente. `scripts/loadtest-env.sh` ripete i test di carico sul tuo ambiente.

## 12.9 Lista di controllo per la sicurezza

- Tieni **pochi Super Admin**, con autenticazione a due fattori obbligatoria (capitolo 11, *Impostazioni*).
- Usa **HTTPS** davanti a Forge (un reverse proxy o l'ingress del cluster) e imposta `FORGE_PUBLIC_URL` e `FORGE_TRUSTED_PROXIES`.
- Preferisci **LDAP o OpenID Connect** e spegni il login locale per i non amministratori.
- Tieni la **chiave principale** e la **chiave dei backup** fuori dal cluster, in un gestore di segreti; valuta il **vault** per la chiave principale (capitolo 11).
- Limita gli **host in uscita** per progetto (capitolo 5) e lascia spento l'accesso agli indirizzi privati salvo necessità.
- Rivedi con regolarità il **registro di audit** e i **token API** dei progetti; revoca ciò che non si usa.
- Aggiorna le immagini con regolarità: `make release` le costruisce e le pubblica, e un controllo delle licenze (`make licenses`) verifica le dipendenze.

## 12.10 Risoluzione dei problemi

| Sintomo | Cosa controllare |
| --- | --- |
| `make docker/run` si ferma con un servizio non sano | `make ps`, poi `make logs SERVICE=<nome>`. |
| `migrate` fallisce con `role "forge_app" does not exist` | Il volume del database è più vecchio dello script di inizializzazione: ricrealo con `make docker/down` (**cancella i dati locali**). |
| `temporal` si riavvia di continuo | `make logs SERVICE=temporal`. I suoi database vengono creati solo alla prima inizializzazione del volume. |
| L'interfaccia dice *API non raggiungibile* | `curl localhost:8080/api/v1/version`; controlla la salute di `api`. |
| L'admin non riesce ad accedere, o è bloccato | `make admin/reset`. |
| `search` non è sano | `make logs SERVICE=search`. Errori di certificati: esegui di nuovo `make env` con lo stesso utente. Dopo aver cambiato le password, il file degli utenti si legge solo al primo avvio: ricrea il volume `search-data`. |
| I documenti restano *In corso* | `worker-ingest` è fermo, oppure il modello di embedding non è raggiungibile (leggi l'errore nella riga del file, capitolo 4). |
| Le esecuzioni restano *In coda* | `worker-runs` o Temporal sono fermi: `make ps`. |
| Gli strumenti Python falliscono | Il runner sandbox non gira (`make sandbox/up`) o non è configurato. |
| Tutti sono stati disconnessi | Valkey si è riavviato; è normale. |
| Un segreto memorizzato non si legge | La chiave principale è cambiata oppure il vault è irraggiungibile (capitolo 11). |

---

Qui finisce la guida. I capitoli si possono leggere in qualsiasi ordine; l'**Indice** li elenca tutti. Se dopo un aggiornamento una schermata non corrisponde più alle immagini, le immagini si possono rigenerare nelle due lingue con `make docs/screenshots` (vedi il [README della guida](../README.md)).
