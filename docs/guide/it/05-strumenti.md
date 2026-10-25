# 5. Tools Studio

[Indice](README.md) · Precedente: [4. Dati e RAG Studio](04-dati-e-rag.md) · Prossimo: [6. Agentic AI Studio](06-agenti.md)

Un modello linguistico sa solo scrivere testo. Un **tool** (strumento) gli permette di *fare* qualcosa: cercare un valore in un'API, interrogare un database, chiamare un servizio aziendale, eseguire un piccolo calcolo. In Tools Studio definisci gli strumenti **una volta**, li provi, li pubblichi, e poi agenti e casi d'uso possono usarli. Forge gestisce per te le parti pericolose: i segreti sono memorizzati cifrati e non arrivano mai al modello, le chiamate in uscita sono limitate agli host che consenti e ogni chiamata è registrata.

> **A chi serve?** Builder e Project Admin creano e pubblicano gli strumenti. Solo i **Project Admin** gestiscono le *Connessioni* (host consentiti, credenziali, origini dati, server MCP). Gli altri ruoli non vedono Tools Studio.

## 5.1 Concetti

| Parola | Significato |
| --- | --- |
| **Tool** | Un'azione con un nome, *parametri* tipizzati e una *descrizione* che il modello legge per decidere quando usarla. |
| **Tipo** | **HTTP** (chiama un'API web), **SQL (sola lettura)** (interroga un database), **Python (sandbox)** (una piccola funzione) e **MCP** (uno strumento che arriva da un server MCP). |
| **Versione** | Ogni salvataggio crea una nuova versione immutabile. Un tool è *Bozza* oppure *Pubblicato* a una versione. Gli agenti fissano la versione pubblicata quando vengono salvati. |
| **Connessioni** | I componenti condivisi: *host consentiti*, *credenziali*, *origini dati*, *server MCP*. |
| **Registro delle chiamate** | La traccia di ogni chiamata, dalle prove e dagli agenti. |

Tools Studio ha tre schede: **Libreria dei tool**, **Connessioni** e **Registro delle chiamate**.

## 5.2 La libreria dei tool

![La libreria dei tool](../images/it/05-library.png)

| Indicatore | Controllo | Uso |
| --- | --- | --- |
| 1 | **Cerca per nome** | Filtra mentre scrivi; accanto puoi filtrare per **Tipo** e **Stato**. |
| 2 | **Importa OpenAPI** | Crea molti tool in una volta dalla descrizione di un'API (sezione 5.6). |
| 3 | **Nuovo tool** | Apre l'editor dei tool. |
| 4 | La tabella | **Nome** e descrizione, **Tipo**, **Stato** (*Bozza* o *Pubblicato*), **Versione** (per esempio *v2 (pubblicata v1)*), **Flag** e **Aggiornato**. |

La colonna **Flag** ti avverte: **Può modificare dati** (arancione) segna i tool che possono modificare qualcosa fuori da Forge, cioè i tool HTTP con un metodo diverso da GET e tutti i tool MCP, perché Forge non può sapere cosa fa un server remoto; **Richiede approvazione** (blu) segna i tool che attendono la decisione di una persona (sezione 5.4).

Azioni della riga: **Prova / Modifica** apre l'editor e la sua console di prova, **Pubblica** / **Annulla pubblicazione**, **Duplica** (chiede il nome della copia) ed **Elimina** (rifiutato finché un agente usa ancora il tool).

## 5.3 Creare un tool

Premi **Nuovo tool**. L'editor si apre con la definizione e, dopo il salvataggio, una **Console di prova** sotto di essa.

![Un nuovo tool](../images/it/05-new-tool.png)

Campi comuni a ogni tipo:

| Campo | Significato |
| --- | --- |
| **Nome (minuscole, cifre, underscore)** | Il nome che usa il modello, per esempio `exchange_rate`. Non si può cambiare dopo. |
| **Tipo** | HTTP, SQL (sola lettura) o Python (sandbox). |
| **Descrizione letta dal modello** | Il campo più importante. Dice al modello *cosa fa lo strumento e quando usarlo*. Scrivila come istruzioni a un collega: «Ultimi tassi di cambio per una valuta di base. Usalo per qualsiasi domanda sulla conversione di valuta.» |
| **Timeout (secondi)** | Da 1 a 60. Una chiamata più lunga viene interrotta. |
| **Richiede approvazione** | Vedi sezione 5.4. |

### Tool HTTP

Un tool HTTP chiama un'API web.

![Un tool HTTP](../images/it/05-tool-http.png)

1. Scegli il **Metodo** e scrivi l'**URL**. Metti `{nome}` nell'URL per un *parametro di percorso*, per esempio `https://api.example.com/customers/{id}`.
2. Scegli una **Credenziale** se l'API richiede autenticazione (le credenziali si creano nella scheda Connessioni, sezione 5.7). Forge la aggiunge da sé alla richiesta: il modello non la vede mai.
3. Gli **Header statici** (uno `Nome: valore` per riga) vengono inviati a ogni chiamata, per esempio `Accept: application/json`.
4. **Limite della risposta per il modello (caratteri)** taglia le risposte lunghe (da 500 a 64.000).
5. Dichiara i **Parametri** che il modello può compilare (**Aggiungi parametro**, indicatore 1). Per ciascuno: il **Nome**, la **Località** (*path*, *query*, *header* o *body*), il **Tipo** (string, integer, number, boolean, array, object), una **Descrizione**, eventuali **Valori ammessi** (`a, b, c`) e se è **obbligatorio**. I parametri sono *l'unico modo* con cui il modello può influenzare la richiesta: header e corpo vengono solo dai parametri dichiarati, e gli argomenti sconosciuti sono rifiutati.
6. Premi **Crea tool** (oppure **Salva come nuova versione** su un tool esistente). Aggiungi una **Nota di versione** se vuoi.

### Tool SQL (sola lettura)

Un tool SQL esegue **una sola istruzione `SELECT` (o `WITH`)** su un'origine dati PostgreSQL del progetto.

1. Scegli l'**Origine dati** (creata in Connessioni, sezione 5.7).
2. Scrivi la query e usa `:nome` per i valori forniti dal modello, per esempio `SELECT id, status FROM orders WHERE customer = :customer`.
3. Dichiara ogni parametro (nome, tipo, descrizione, obbligatorio).
4. Imposta il **Limite di righe** (fino a 1.000).

I valori sono sempre passati come *parametri associati*, mai incollati nel testo, quindi non possono cambiare il significato della query. L'istruzione gira in una **transazione di sola lettura** con un limite di tempo, e tutto ciò che non è una sola SELECT viene rifiutato.

### Tool Python (sandbox)

Per un piccolo calcolo o una trasformazione che nessuna API offre, scrivi una funzione Python.

![Un tool Python](../images/it/05-tool-python.png)

1. Nell'editor di codice (indicatore 1) definisci `run(args)`: riceve gli argomenti come dizionario e **restituisce un valore scrivibile come JSON**, per esempio `def run(args): return {"words": len(args["text"].split())}`.
2. Dichiara gli **Argomenti** (**Aggiungi argomento**): nome, tipo, descrizione, obbligatorio.
3. Imposta la **Memoria (MB)** (da 32 a 512).

Il codice gira in un container usa e getta (isolato con gVisor dove i tuoi amministratori l'hanno abilitato), **senza file, con la sola libreria standard**, con un limite di memoria e uno di tempo, e **senza rete** a meno che tu elenchi degli host in *Accesso alla rete*. Ogni host elencato deve essere anche tra gli **Host consentiti** del progetto; il codice li raggiunge solo via HTTP e HTTPS (il `urllib` di Python lo fa da solo) e tutto il resto, indirizzi privati compresi, viene rifiutato. È pensato per il calcolo e per piccole chiamate a un servizio di cui ti fidi. I tool Python richiedono il servizio *sandbox*, che abilitano i tuoi amministratori (capitolo 12); senza di esso un tool Python fallisce e segnala che il servizio sandbox non è disponibile o non è configurato.

### Tool MCP

I tool MCP non si scrivono qui: si **importano** da un server MCP registrato nella scheda Connessioni (sezione 5.7). Nell'editor di un tool importato puoi cambiare solo la descrizione, il timeout e il flag di approvazione.

## 5.4 Richiede approvazione

Spunta **Richiede approvazione** per i tool che cambiano qualcosa di importante (inviare un'email, creare un ordine). In un **caso d'uso**, un passaggio *tool* che usa uno strumento così si ferma *prima* della chiamata e chiede a una persona di approvare, e la persona può anche modificare gli argomenti (capitolo 7, *Approvazioni*). Se nessuno approva, il tool non viene mai chiamato.

> **Limitazione attuale.** Un **agente** che chiama da solo uno strumento così (in una chat, o in un passaggio di caso d'uso che è un agente) viene **rifiutato** con il messaggio che l'approvazione non è disponibile: le approvazioni mettono in pausa i passaggi del caso d'uso, non il ciclo di ragionamento interno dell'agente. L'etichetta della casella nell'editor dice ancora «not available until human approval ships» (non disponibile finché non arriva l'approvazione umana), che è obsoleta: il flusso di approvazione esiste, con questa limitazione.

## 5.5 Prova, poi pubblica

Sotto la definizione (dopo il salvataggio) la **Console di prova** ti permette di eseguire davvero lo strumento.

![La console di prova](../images/it/05-tool-test.png)

1. Scegli la **Versione** da provare.
2. Compila gli argomenti (un campo per parametro; quelli obbligatori hanno un asterisco).
3. Premi **Esegui** (indicatore 1).
4. Il risultato (indicatore 2) mostra un'etichetta di stato (**ok** o un codice di errore), la **latenza**, la **Richiesta** realmente inviata (segreti mascherati) e **Cosa legge il modello**: il testo che torna al modello. Controlla con cura quest'ultimo blocco: è ciò su cui il modello baserà il passo successivo, quindi deve essere breve, chiaro e senza rumore. Noterai che la risposta di un'API esterna è racchiusa tra i tag `<untrusted-content>` seguiti da una nota: dice al modello che il testo è *dato proveniente da un sistema esterno, mai istruzioni*, una difesa contro la prompt injection (una pagina web o un'API che prova a dare ordini al modello).

Ogni prova è registrata nel registro delle chiamate. Quando sei soddisfatto, torna alla libreria e premi **Pubblica**. Solo un tool *pubblicato* si può aggiungere a un agente.

## 5.6 Importare un'API da OpenAPI

Se un'API ha già una descrizione OpenAPI (versione 3.x), importa in un colpo solo le sue operazioni di *lettura*.

![Importa da OpenAPI](../images/it/05-import-openapi.png)

1. Premi **Importa OpenAPI**.
2. Incolla il **Documento OpenAPI** (JSON o YAML, fino a 2 MB) oppure scegli un file, poi premi **Leggi le operazioni**.
3. Le operazioni vengono elencate. Spunta quelle che vuoi, compila l'**URL di base** (se il documento non lo indica) e una **Credenziale** facoltativa.
4. Premi **Importa i selezionati**. Viene creato un tool per ogni operazione, con il suo nome; i nomi già esistenti vengono saltati e segnalati.

I tool importati sono *bozze*: rivedi le descrizioni (arrivano dal documento dell'API e possono essere troppo tecniche), provali e pubblicali.

## 5.7 Connessioni

Solo i Project Admin possono modificare questa scheda; i Builder possono usare ciò che è elencato.

![Connessioni](../images/it/05-connections.png)

### Host consentiti (egress)

I tool HTTP, i server MCP e le origini dati possono raggiungere **solo gli host elencati qui**. Un **elenco vuoto non consente nulla**. Scrivi un host per riga; usa `*.example.com` per tutti i sottodomini. Premi **Salva host**. Anche gli host elencati devono avere un indirizzo pubblico: gli indirizzi privati (reti interne) sono rifiutati, salvo che gli amministratori li abbiano consentiti di proposito per l'intera installazione (un avviso nella pagina ti dice quando è così).

### Credenziali

Una **credenziale** è un segreto con un nome usato dai tool. Scrivi un **Nome**, scegli il tipo, scrivi il **Segreto** e premi **Aggiungi**:

| Tipo (come mostrato) | Campo aggiuntivo | Inviato come |
| --- | --- | --- |
| **Bearer token** | nessuno | `Authorization: Bearer …` |
| **Basic (user and password)** | **Utente** | autenticazione HTTP basic |
| **Header** | **Nome dell'header o del parametro** | un header con quel nome |
| **Query parameter** | **Nome del parametro** | un parametro della query string |

I nomi dei tipi in questo selettore non sono tradotti. Il segreto è memorizzato cifrato e non viene più mostrato. La tabella mostra **Nome**, **Tipo** e **Usato da** della credenziale (quali tool la usano); **Elimina** è rifiutato finché un tool la usa.

### Origini dati (PostgreSQL)

Un'**origine dati** è una connessione di sola lettura a un database PostgreSQL per i tool SQL e le fonti SQL. Compila **Nome**, **Host**, porta, **Database**, utente e **Password**, e scegli la modalità **TLS**: *Senza TLS*, *TLS (senza verifica)* o *TLS (verifica il certificato)*. Usa un account del database che abbia **diritti di sola lettura** sulle tabelle che vuoi esporre; Forge aggiunge la sua transazione di sola lettura, ma il minimo privilegio è la prima linea di difesa.

### Server MCP

Un **server MCP** offre strumenti già pronti. Compila **Nome**, **Trasporto** (Streamable HTTP, SSE oppure *stdio (solo comandi consentiti)*), l'**Oggetto** (l'URL, oppure per stdio il **Comando** e gli **Argomenti**) e, se serve, una **Credenziale**, poi **Aggiungi**. Premi **Scopri i tool** su un server per elencare i suoi strumenti, spunta quelli che vuoi e premi **Importa i selezionati**: diventano tool della libreria. Il trasporto *stdio* avvia un programma dentro il server Forge, quindi è disattivato a meno che gli amministratori non consentano comandi con un nome preciso.

## 5.8 Il registro delle chiamate

![Il registro delle chiamate](../images/it/05-calls.png)

Ogni chiamata è registrata, comprese quelle rifiutate. Filtra per **Tool** (*Tutti i tool*), **Stato** (*Qualsiasi stato*) e **Origine** (*Qualsiasi origine*: la console di prova o un agente). La tabella mostra **Ora**, **Tool**, **Stato**, **Origine**, **Latenza** e un link **Dettagli** che apre **Input** e **Output** (i segreti sono rimossi e i valori lunghi sono abbreviati a 64 KB). Usalo per scoprire *chi ha chiamato cosa e cosa è tornato*.

## 5.9 Buone pratiche

- **Scrivi le descrizioni per il modello.** Una descrizione vaga significa uno strumento usato al momento sbagliato o mai.
- **Preferisci GET e la sola lettura.** I tool che modificano dati meritano approvazione, prove e un perimetro stretto.
- **Tieni piccole le risposte.** Una risposta breve e pulita costa meno e rende il modello più accurato: usa il *Limite della risposta* e, dove puoi, scegli i campi nella chiamata all'API.
- **Minimo privilegio ovunque.** Un utente di database dedicato in sola lettura; l'elenco più stretto di host consentiti; una credenziale per integrazione.
- **Prova anche i casi di errore:** parametri sbagliati, un host irraggiungibile. La console di prova mostra il codice di errore che riceverà il modello.

## 5.10 Risoluzione dei problemi

| Sintomo | Causa e rimedio |
| --- | --- |
| «Il tuo ruolo non può gestire i tool in questo progetto» | Solo Builder e Project Admin usano Tools Studio. |
| La chiamata è rifiutata perché l'host non è consentito | Aggiungi l'host a **Host consentiti** (Project Admin). |
| La chiamata è rifiutata per un indirizzo privato | Gli indirizzi interni sono bloccati per impostazione predefinita. Chiedi agli amministratori se l'installazione li consente. |
| Il tool non si può aggiungere a un agente | Non è **pubblicato**, oppure è stato eliminato. |
| **Elimina** è rifiutato | Un agente usa ancora il tool o la credenziale; togli prima il riferimento. |
| Un tool Python segnala che la sandbox non è disponibile o non è configurata | Il servizio sandbox non è abilitato in questa installazione (capitolo 12). |
| Il modello non usa mai il tool | La descrizione non dice quando usarlo, oppure le istruzioni dell'agente non lo nominano. |

Prossimo: [6. Agentic AI Studio](06-agenti.md)
