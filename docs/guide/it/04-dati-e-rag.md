# 4. Dati e RAG Studio

[Indice](README.md) · Precedente: [3. Prompt Studio](03-prompt-studio.md) · Prossimo: [5. Tools Studio](05-strumenti.md)

I modelli linguistici non conoscono i documenti della tua azienda. Il **RAG** (*retrieval-augmented generation*, generazione aumentata dal recupero) risolve il problema: quando qualcuno fa una domanda, Forge **cerca prima nei tuoi documenti** i passaggi più pertinenti, poi chiede al modello di rispondere **usando solo quei passaggi** e di **citarli**. Questo capitolo copre tutto il percorso:

1. le **Data Collection** contengono i tuoi file (sezione 4.2);
2. le **Fonti** tengono una collezione allineata a un sistema esterno (sezione 4.4);
3. **RAG Studio** definisce le *pipeline* che interrogano una collezione e danno risposte citate (sezioni da 4.5 a 4.8).

> **A chi serve?** A Builder e Project Admin. Collezioni e fonti sono in **Projects Studio**; le pipeline sono in **RAG Studio**.

## 4.1 Come funziona, in parole semplici

| Passo | Cosa succede | Dove lo vedi |
| --- | --- | --- |
| **Caricamento** | Un file viene memorizzato. Forge ne riconosce il tipo dal suo *contenuto* (un file rinominato non lo inganna). | Collezione → File |
| **Lettura** | Il testo viene estratto: file PDF, Word, PowerPoint ed Excel, pagine web, Markdown, testo semplice e immagini (con riconoscimento ottico dei caratteri). | Stato *In corso* |
| **Suddivisione** | Il testo viene tagliato in passaggi («chunk») di qualche centinaio di token, di solito seguendo titoli e paragrafi. | Pipeline → Chunking |
| **Embedding** | Ogni chunk viene trasformato in un elenco di numeri (un *embedding*) che ne rappresenta il significato, da un modello di embedding. | Collezione → Indici |
| **Recupero** | Una domanda viene confrontata con i chunk; i migliori restano. | Pipeline → Retrieval |
| **Risposta** | Il modello scrive la risposta dai chunk e segna ogni affermazione con un numero `[1]`, `[2]`… che rimanda a una fonte. | Pagina di prova della RAG |

Tutto fino all'*Embedding* avviene automaticamente dopo il caricamento, in background. I file che non sono cambiati non vengono mai elaborati due volte: un file con contenuto identico è segnalato come *invariato*.

## 4.2 Data Collection

Apri **Projects Studio → Dati**.

![Le collezioni di dati](../images/it/04-collections.png)

Una **collezione** è una cartella con un nome che raccoglie file che stanno insieme, per esempio *Company policies*. La tabella mostra lo **Stato** (*Completato*, *In corso*, *Indicizzazione*, *Non riuscita* o *Vuota*), il numero di **File** (con il numero di quelli falliti), la **Dimensione**, il numero di **Indici** e l'ultimo **Aggiornato**. Premi il nome per aprirla.

### Creare una collezione

1. Premi **Nuova collezione** (indicatore 1).

   ![Nuova collezione](../images/it/04-new-collection.png)

2. Scrivi un **Nome** (unico nel progetto) e una **Descrizione** facoltativa, poi **Crea**.

**Rinomina** ed **Elimina** sono nella riga. Eliminare una collezione elimina i suoi file, i suoi chunk e i suoi indici, dopo conferma; è rifiutato finché una pipeline RAG la usa («RAG pipelines use this collection; delete them first»).

### Caricare file

Apri la collezione.

![Una collezione con i suoi file e indici](../images/it/04-collection.png)

1. Prima di caricare, decidi **chi può leggere** i file (indicatore 1, accanto ai pulsanti): scegli gruppi e/o ruoli, oppure lascia vuoti entrambi per renderli leggibili da **tutto il progetto** (vedi sezione 4.3).
2. Premi **Carica file** per scegliere uno o più file, oppure **Carica cartella** per caricare un'intera directory (le sottocartelle si conservano come percorsi). Puoi caricare fino a **100 MB per caricamento**; selezioni più grandi vengono inviate automaticamente in più caricamenti.
3. Compare un riepilogo: *N accettati, N invariati, N rifiutati*. Un file rifiutato è elencato con il motivo indicato dal server (per esempio un tipo non supportato).

Tipi accettati: **PDF, DOCX, PPTX, XLSX, HTML, Markdown, testo e immagini PNG/JPG** (il testo delle immagini viene letto con OCR). Gli archivi ZIP non sono accettati.

### La tabella dei file

![I file di una collezione](../images/it/04-documents.png)

| Colonna | Significato |
| --- | --- |
| **File** | Nome, percorso della cartella e numero di pagine. |
| **Tipo** | Il tipo rilevato dal contenuto. |
| **Dimensione** | In KB. |
| **Stato** | **Completato**, **In corso** o **Non riuscita**. Per i file completati una riga indica quale modello di embedding li ha indicizzati e quanti chunk (e quanti embedding sono stati riusati). Per i file falliti sono mostrati il motivo e l'ora. |
| **Gruppi di accesso** | I gruppi e i ruoli autorizzati a leggere il file, oppure **Tutto il progetto**. Le etichette blu sono gruppi; quelle viola sono ruoli. |
| **Caricato** | Data e ora. |

Azioni della riga: **Scarica** (i PDF e le immagini si aprono nel browser; il testo si apre come testo semplice; i file Office vengono scaricati), **Riprova** (solo per i file falliti: li rielabora), **Accesso** (cambia chi può leggere) ed **Elimina**.

> **Se un file resta *Non riuscita*.** Leggi il motivo nella riga. Cause tipiche: un PDF protetto da password, un file Office danneggiato, oppure un modello di embedding non raggiungibile. Rimuovi la causa e premi **Riprova**.

### Indici

![Gli indici di una collezione](../images/it/04-indexes.png)

Un **indice** (il nome tecnico è *knowledge base*) è l'insieme dei chunk della collezione per un *modello di embedding* e un'*impostazione di chunking*. Gli indici non si creano a mano: una pipeline RAG chiede quello che le serve, e le pipeline con le stesse impostazioni lo condividono. La scheda elenca, per ogni indice, il **Modello di embedding**, il **Chunking** (parser, dimensione e sovrapposizione), lo **Stato**, il numero di **Chunk** e la data di creazione. Se cambi il modello di embedding o il chunking di una pipeline, un nuovo indice viene costruito in background; il precedente continua a rispondere finché il nuovo non è pronto.

## 4.3 Chi può leggere un documento

Non ogni documento è per tutti. Quando la RAG costruisce una risposta, **vengono usati solo i passaggi che l'utente che chiede può leggere**, quindi un documento riservato non può mai finire nella risposta di chi non ha accesso.

- Un file senza gruppi e senza ruoli è leggibile da **tutto il progetto**.
- Altrimenti un membro può leggerlo se appartiene a **uno dei suoi gruppi** *oppure* ha **uno dei suoi ruoli**.
- Si imposta al caricamento, o dopo con **Accesso** nella tabella dei file:

  ![Cambiare chi può leggere un file](../images/it/04-access.png)

  Scegli gruppi e ruoli e premi **Salva**. Lascia vuoti entrambi per rendere il file leggibile da tutto il progetto.
- I gruppi si creano in **Projects Studio → Gruppi** (capitolo 2). I token API vedono solo i file leggibili da tutto il progetto.

## 4.4 Fonti: tenere aggiornata una collezione

Una **sorgente** copia documenti da fuori Forge in una collezione **a intervalli**. Vengono scaricati di nuovo solo i file cambiati, e i file rimossi all'origine vengono rimossi anche dalla collezione. Un documento che arriva da una sorgente è un documento normale: stesse regole di accesso, stessa indicizzazione, stesse citazioni.

Apri **Projects Studio → Fonti**.

![Le fonti](../images/it/04-sources.png)

La tabella mostra di ogni sorgente **Nome**, **Tipo**, **Collezione**, **Pianificazione**, **Ultima sincronizzazione** e **Documenti**. Una sorgente può essere **In pausa** (disattivata) o **Manuale** (senza pianificazione). Azioni della riga: **Sincronizza ora**, **Esecuzioni** (lo storico: quando è girata, quanti documenti sono stati *Aggiunti*, *Aggiornati*, *Invariati*, *Eliminati*, *Saltati* o *Non riusciti*), **Modifica** ed **Elimina**.

### Creare una sorgente

1. Premi **Nuova sorgente**.

   ![Nuova sorgente](../images/it/04-new-source.png)

2. Scegli un **Nome**, la **Collezione** che riceve i documenti e il **Tipo**:

   | Tipo | Legge da | Credenziali |
   | --- | --- | --- |
   | `s3` | Un bucket (e prefisso) su qualsiasi storage compatibile con S3. | chiave di accesso e chiave segreta |
   | `url` | Pagine web: parte dagli indirizzi indicati e segue i link sugli stessi siti (fino a 500 pagine, profondità 5). | bearer token o header |
   | `rest` | Un'API JSON: un record diventa un documento. | bearer token o header |
   | `sql` | Una `SELECT` di sola lettura su un'origine dati del progetto: una riga diventa un documento (fino a 1.000 righe). | nessuna: usa quelle dell'origine dati |
   | `git` | Un repository Git via HTTPS (un branch e una sottocartella). | token, oppure nome utente e password |
   | `filesystem` | Una directory che gli amministratori hanno montato sul server (per esempio una condivisione di rete). | nessuna |

3. Compila la **Configurazione (JSON)**. Selezionando un tipo compare un esempio da adattare, come `{"start_urls": ["https://docs.example.com/"], "max_pages": 100, "max_depth": 2}`.
4. Se serve, compila le **Credenziali (JSON, solo scrittura)**: sono memorizzate cifrate e non vengono più mostrate. Quando modifichi una sorgente, le credenziali salvate restano invariate se non ne scrivi di nuove.
5. **Ogni (minuti, vuoto = solo manuale)**: l'intervallo tra le sincronizzazioni, almeno 5 minuti. Lascia vuoto per sincronizzare solo con **Sincronizza ora**.
6. Lascia spuntato **Attiva** per eseguire a orari prestabiliti. Lascia spuntato **Elimina i documenti che spariscono dalla fonte** se la collezione deve rispecchiare l'origine.
7. Scegli i **gruppi e i ruoli** che possono leggere i documenti, poi **Salva**.

**Rete di sicurezza.** Se una sincronizzazione scopre che *più della metà* dei documenti noti è sparita (o che l'elenco è vuoto), presume un errore — una condivisione non montata, un indirizzo sbagliato — e **non elimina nulla**. L'esecuzione mostra un avviso, e puoi confermare con **Sincronizza ed elimina** se la rimozione è davvero voluta.

**Regole di rete.** Le sorgenti che escono verso la rete seguono l'elenco degli host consentiti del progetto in Tools Studio (capitolo 5). Se un host non è consentito, l'esecuzione fallisce con un messaggio chiaro. Quando elimini una sorgente scegli **Mantieni i documenti** (predefinito) oppure **Elimina anche i documenti**.

## 4.5 RAG Studio: le pipeline

Una **pipeline RAG** è una ricetta salvata: *quale collezione cercare, come cercarla, quale modello scrive la risposta e come*. Come i prompt, le pipeline sono versionate e si possono pubblicare.

Apri **RAG Studio**. La **Libreria RAG** elenca le pipeline.

![La libreria RAG](../images/it/04-rag-library.png)

I controlli funzionano come nella Libreria dei prompt (capitolo 3): cerca per nome (indicatore 1), filtra per stato (indicatore 2) e per periodo (indicatore 3), **Nuova RAG** (indicatore 4) e la tabella (indicatore 5) con **Nome**, **Stato**, **Versione**, **Sorgente dati**, metodo di **Ricerca** e **Aggiornato**. Azioni della riga: **Prova**, **Modifica**, **Duplica**, **Elimina**.

## 4.6 Configurare una pipeline

Premi **Nuova RAG** (oppure **Modifica**). Premi il titolo per dargli un nome, per esempio `policies-qa`.

![L'editor delle pipeline](../images/it/04-rag-editor.png)

L'etichetta di stato accanto al nome mostra **Bozza** o *Pubblicato*, e **Indice pronto** (oppure *in costruzione*) dice se l'indice di questa configurazione è disponibile.

### Dati e modelli

| Campo | Cosa scegliere |
| --- | --- |
| **Sorgente dati** (indicatore 1) | La collezione da cercare. Le collezioni si creano in Projects Studio → Dati. |
| **LLM** (indicatore 2) | Il modello che **scrive la risposta** dai chunk recuperati. Si consiglia un alias (capitolo 2). |
| **Modello di embedding** (indicatore 3) | Il modello che trasforma in vettori i chunk *e le domande*. **Cambiarlo costruisce un nuovo indice.** Usa lo stesso modello di embedding per tutta la vita di una pipeline, salvo buone ragioni. |

### Retrieval

| Campo | Significato |
| --- | --- |
| **Metodo di ricerca** (indicatore 4) | **Ricerca vettoriale**: solo similarità di significato. **Ricerca semantica**: ricerca vettoriale seguita da un *reranker*. **Ricerca ibrida**: ricerca vettoriale più ricerca per parole chiave (BM25), unite e poi riordinate dal reranker. L'ibrida è il miglior valore predefinito: trova i passaggi per significato *e* per parole esatte come codici prodotto o nomi. |
| **Reranker** | Un modello facoltativo che riordina i candidati per pertinenza (da 0 a 1). *Nessuna* usa la semplice similarità. |
| **Top_k** | Quanti chunk vengono inviati al modello. Più chunk danno più contesto ma costano più token e possono distrarre il modello. Da quattro a otto è tipico. |
| **Soglia di similarità** (indicatore 5) | Il punteggio minimo (da 0 a 1) che un chunk deve avere per essere usato. Se **nessun** chunk lo supera, la risposta dice che *non sono state trovate informazioni pertinenti* invece di tirare a indovinare. Alzala per essere più severo. |
| **Candidati** | Quanti chunk ogni sistema di recupero preleva prima di unire e riordinare. |

### Chunking

| Campo | Significato |
| --- | --- |
| **Node parser** | Come si tagliano i documenti: **Strutturale (per titolo)**, **Ricorsivo (paragrafi, frasi)** o **Dimensione fissa**. Lo strutturale è il migliore per documenti ben scritti con titoli. |
| **Dimensione chunk** (indicatore 6) | In token, di solito da 100 a 1000. I chunk piccoli sono precisi; quelli grandi conservano più contesto. |
| **Sovrapposizione chunk** | In token, di solito il 10-20% della dimensione, così una frase tagliata al confine compare intera in un chunk. |

Salvare una modifica qui **costruisce un nuovo indice in background** (l'area **Indice** mostra l'avanzamento). I test continuano a usare l'ultimo indice pronto finché il nuovo non è pronto.

### Risposta

| Campo | Significato |
| --- | --- |
| **Prompt** | Facoltativo. Un prompt della Libreria dei prompt che dice al modello come usare i chunk. Si usa la sua versione *pubblicata*, e `{context}` e `{question}` vengono compilati per te. Senza, Forge usa istruzioni predefinite: rispondere solo dalle fonti e citarle. |
| **Modalità di risposta** | **Compact**: una richiesta con tutti i chunk. **Refine**: un chunk alla volta, per contesti molto lunghi o eterogenei. |
| **Budget di contesto** | Il numero massimo di token di chunk messi nella richiesta. I chunk con rank più basso vengono scartati per primi. |
| **Temperatura** | Valori bassi danno risposte fedeli; tienila bassa per il RAG. |
| **Nota di versione** | Una frase salvata con la versione. |

![Pulsanti di salvataggio](../images/it/04-rag-editor-answer.png)

**Salva configurazione** (indicatore 1) crea la versione successiva. **Salva configurazione e prova la RAG** (indicatore 2) salva e apre la pagina di prova. Il pannello **Versioni** funziona esattamente come in Prompt Studio: ogni salvataggio è una nuova versione immutabile; puoi leggerne una vecchia e ricaricarla.

## 4.7 Provare una pipeline e leggere la risposta

![La pagina di prova della RAG](../images/it/04-rag-test.png)

1. Scegli la versione in alto (di default l'ultima).
2. Scrivi una **Domanda** (indicatore 1), per esempio *How many days of annual leave do I have, and how do I ask for them?* (i documenti dimostrativi sono in inglese).
3. Premi **Procedi** (indicatore 2).

Se l'indice di questa versione è ancora in costruzione, un avviso dice che la risposta usa l'ultimo indice pronto e può essere incompleta.

![Una risposta con citazioni](../images/it/04-rag-answer.png)

Il risultato mostra:

- la **Risposta** (indicatore 1), in cui ogni affermazione termina con un numero tra parentesi quadre;
- le **Fonti** (indicatore 2): l'elenco numerato dei passaggi usati, ciascuno con il nome del file e, per i PDF, la pagina. Premi una fonte per aprire il documento a quella pagina (un PDF si apre sulla pagina; i file di testo si aprono come testo semplice);
- le statistiche (indicatore 3): **Latenza**, **Costo**, **Modello** che ha risposto davvero e i token usati;
- **Trace** (indicatore 4): il dettaglio dell'intero percorso.

Se non si trova nulla di pertinente, Forge lo dice: *Nessuna informazione pertinente è stata trovata nella knowledge base per questa domanda.* È il comportamento voluto; protegge gli utenti da risposte inventate.

### Leggere il trace

![Il trace del retrieval](../images/it/04-rag-trace.png)

Il trace spiega **perché** la risposta è quella: la tabella **Retrieval** elenca i chunk candidati con **Documento**, **Pagina**, **Punteggio**, similarità **Coseno**, i loro **Rank** in ciascuna ricerca (vettoriale, per parole chiave, reranker) e se sono stati **Usato** nel prompt. Sotto trovi i messaggi esatti inviati al modello (**Input**), l'**Output**, l'**Utilizzo totale** e i **Metadati**. Quando una risposta è sbagliata, il trace ti dice se il passaggio giusto non è mai stato recuperato (cambia chunking, metodo di ricerca o soglia), è stato recuperato ma con rank troppo basso (alza *Top_k* o aggiungi un reranker), oppure è stato usato ma letto male (migliora il prompt o il modello).

## 4.8 Pubblicare

Come per i prompt, la pagina di prova ha **Pubblica v…**. Casi d'uso, agenti e API seguono la versione *pubblicata*. La libreria mostra **Pubblicato** e il numero della versione pubblicata.

## 4.9 Buone pratiche

- **Parti in piccolo.** Carica cinque documenti rappresentativi, costruisci la pipeline, fai domande reali, leggi i trace. Poi aggiungi il resto.
- **Scrivi i documenti pensando al recupero.** Titoli chiari, un argomento per sezione, niente immagini scansionate quando hai il testo.
- **Limita l'accesso alla fonte.** Metti i file riservati nei gruppi fin dal primo caricamento; è più facile che sistemarlo dopo.
- **Tieni onesta la soglia.** Una soglia pari a zero risponde sempre; una soglia sensata rende possibile il «non lo so».
- **Usa la ricerca ibrida** quando i documenti contengono codici, nomi o sigle.
- **Non cambiare alla leggera il modello di embedding.** Ricostruisce l'indice e costa tempo e token.

## 4.10 Risoluzione dei problemi

| Sintomo | Causa e rimedio |
| --- | --- |
| «RAG Studio richiede il ruolo Builder o Project Admin» | Il tuo ruolo non può modificare le pipeline. |
| *Crea prima una Data Collection* | La pipeline ha bisogno di una collezione. Creane una in Projects Studio → Dati. |
| Un file è *Non riuscita* | Leggi il motivo nella riga; correggi e premi **Riprova**. |
| La risposta dice che non c'è nulla di pertinente | La domanda è fuori dai documenti, oppure la soglia è troppo alta, oppure i documenti sono ancora in indicizzazione. Controlla lo stato dell'**Indice** e il trace. |
| La risposta ignora un documento che sai essere presente | Chi chiede potrebbe non avere accesso (sezione 4.3), il file potrebbe non essere ancora *Completato*, oppure è finito sotto *Top_k*. Apri il trace. |
| L'esecuzione di una sorgente dice «nothing was deleted» | La rete di sicurezza ha trattenuto una cancellazione di massa. Controlla l'origine, poi conferma con **Sincronizza ed elimina** solo se la rimozione è voluta. |
| Una sorgente non raggiunge il suo host | L'host non è nell'elenco consentito del progetto (capitolo 5), oppure l'installazione usa un proxy. Chiedi agli amministratori. |
| Una sorgente *filesystem* viene rifiutata | Gli amministratori devono montare la directory e consentirla per il progetto. |

Prossimo: [5. Tools Studio](05-strumenti.md)
