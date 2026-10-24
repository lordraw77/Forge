# 4. Data and RAG Studio

[Index](README.md) · Previous: [3. Prompt Studio](03-prompt-studio.md) · Next: [5. Tools Studio](05-tools.md)

Language models do not know your company's documents. **RAG** (*retrieval-augmented generation*) fixes this: when someone asks a question, Forge first **searches your documents** for the most relevant passages, then asks the model to answer **using only those passages** and to **cite them**. This chapter covers the whole path:

1. **Data Collections** hold your files (section 4.2);
2. **Sources** keep a collection in step with an external system (section 4.4);
3. **RAG Studio** defines *pipelines* that question a collection and gives cited answers (sections 4.5 to 4.8).

> **Who is this for?** Builders and Project Admins. Collections and sources are in **Projects Studio**; pipelines are in **RAG Studio**.

## 4.1 How it works, in plain words

| Step | What happens | Where you see it |
| --- | --- | --- |
| **Upload** | A file is stored. Forge recognises its type from its *content* (a renamed file is not fooled). | Collection → Files |
| **Parse** | The text is extracted: PDF, Word, PowerPoint and Excel files, web pages, Markdown, plain text and images (with optical character recognition). | Status *In progress* |
| **Chunk** | The text is cut into passages ("chunks") of a few hundred tokens, usually following headings and paragraphs. | Pipeline → Chunking |
| **Embed** | Each chunk is turned into a list of numbers (an *embedding*) that represents its meaning, by an embedding model. | Collection → Indexes |
| **Retrieve** | A question is compared with the chunks; the best ones are kept. | Pipeline → Retrieval |
| **Answer** | The model writes the answer from those chunks and marks each statement with a number `[1]`, `[2]`… that points to a source. | RAG test page |

Everything up to *Embed* happens automatically after upload, in the background. Files that have not changed are never processed twice: a file with identical content is reported as *unchanged*.

## 4.2 Data Collections

Open **Projects Studio → Data**.

![The data collections](../images/en/04-collections.png)

A **collection** is a named folder of files that belong together, for example *Company policies*. The table shows its **Status** (*Success*, *In progress*, *Indexing*, *Failed* or *Empty*), the number of **Files** (with the number of failed ones), the **Size**, the number of **Indexes** and the last **Updated**. Click the name to open it.

### Create a collection

1. Click **New collection** (marker 1).

   ![New collection](../images/en/04-new-collection.png)

2. Type a **Name** (unique in the project) and an optional **Description**, then **Create**.

**Rename** and **Delete** are in the row. Deleting a collection deletes its files, its chunks and its indexes, after you confirm; it is refused while a RAG pipeline still uses it ("RAG pipelines use this collection; delete them first").

### Upload files

Open the collection.

![A collection with its files and indexes](../images/en/04-collection.png)

1. Before uploading, decide **who may read** the files (marker 1, next to the buttons): choose groups and/or roles, or leave both empty to make them readable by **the whole project** (see section 4.3).
2. Click **Upload files** to pick one or more files, or **Upload folder** to upload a whole directory (subfolders are kept as paths). You can upload up to **100 MB per upload**; larger selections are sent in several uploads automatically.
3. A summary appears: *N accepted, N unchanged, N rejected*. A rejected file is listed with the reason given by the server (for example an unsupported type).

Accepted types: **PDF, DOCX, PPTX, XLSX, HTML, Markdown, text and PNG/JPG images** (the text of images is read with OCR). ZIP archives are not accepted.

### The files table

![The files of a collection](../images/en/04-documents.png)

| Column | Meaning |
| --- | --- |
| **File** | Name, folder path and number of pages. |
| **Type** | The type detected from the content. |
| **Size** | In KB. |
| **Status** | **Success**, **In progress** or **Failed**. For successful files, a line shows which embedding model indexed them and how many chunks (and how many embeddings were reused). For failed files the reason and the time are shown. |
| **Access groups** | The groups and roles allowed to read the file, or **Whole project**. Blue tags are groups; purple tags are roles. |
| **Uploaded** | Date and time. |

Row actions: **Download** (PDFs and images open in the browser; text opens as plain text; Office files are downloaded), **Retry** (only for failed files: processes them again), **Access** (change who may read) and **Delete**.

> **If a file stays *Failed*.** Read the reason in the row. Typical causes: a password-protected PDF, a corrupted Office file, or an embedding model that was unreachable. Fix the cause and click **Retry**.

### Indexes

![The indexes of a collection](../images/en/04-indexes.png)

An **index** (the technical name is *knowledge base*) is the collection's chunks for one *embedding model* and one *chunking setting*. You do not create indexes by hand: a RAG pipeline asks for the one it needs, and pipelines with the same settings share it. The card lists, for each index, the **Embedding model**, the **Chunking** (parser, size and overlap), the **Status**, the number of **Chunks** and the creation date. If you change a pipeline's embedding model or chunking, a new index is built in the background; the previous one keeps answering until the new one is ready.

## 4.3 Who can read a document

Not every document is for everybody. When RAG builds an answer, **only the passages the asking user may read are used**, so a restricted document can never leak into the answer of someone without access.

- A file with no groups and no roles is readable by **the whole project**.
- Otherwise a member can read it if they belong to **one of its groups** *or* have **one of its roles**.
- Set it at upload time, or later with **Access** in the files table:

  ![Change who may read a file](../images/en/04-access.png)

  Choose groups and roles and click **Save**. Leave both empty to make the file readable by the whole project.
- Groups are created in **Projects Studio → Groups** (chapter 2). API tokens see only the files readable by the whole project.

## 4.4 Sources: keep a collection up to date

A **source** copies documents from outside Forge into a collection **on a schedule**. Only what changed is downloaded again, and files removed at the origin are removed from the collection too. A document that comes from a source is an ordinary document: same access rules, same indexing, same citations.

Open **Projects Studio → Sources**.

![The sources](../images/en/04-sources.png)

The table shows each source's **Name**, **Type**, **Collection**, **Schedule**, **Last sync** and **Documents**. A source can be **Paused** (disabled) or **Manual** (no schedule). Row actions: **Sync now**, **Runs** (the history: when it ran, how many documents were *Added*, *Updated*, *Unchanged*, *Deleted*, *Skipped* or *Failed*), **Edit** and **Delete**.

### Create a source

1. Click **New source**.

   ![New source](../images/en/04-new-source.png)

2. Choose a **Name**, the **Collection** that receives the documents and the **Type**:

   | Type | Reads from | Credentials |
   | --- | --- | --- |
   | `s3` | A bucket (and prefix) on any S3-compatible storage. | access key and secret key |
   | `url` | Web pages: starts from the given addresses and follows links on the same sites (up to 500 pages, depth 5). | bearer token or headers |
   | `rest` | A JSON API: one record becomes one document. | bearer token or headers |
   | `sql` | A read-only `SELECT` on a data source of the project: one row becomes one document (up to 1,000 rows). | none: it uses the data source's |
   | `git` | A Git repository over HTTPS (a branch and a sub-folder). | token, or user name and password |
   | `filesystem` | A directory that your administrators mounted on the server (for example a network share). | none |

3. Fill in **Configuration (JSON)**. Selecting a type shows an example you can adapt, such as `{"start_urls": ["https://docs.example.com/"], "max_pages": 100, "max_depth": 2}`.
4. If needed, fill in **Credentials (JSON, write-only)**: they are stored encrypted and are never shown again. When you edit a source, stored credentials stay unless you type new ones.
5. **Every (minutes, empty = manual only)**: the interval between syncs, at least 5 minutes. Leave it empty to sync only with **Sync now**.
6. Keep **Enabled** ticked to run on schedule. Keep **Delete documents that disappear from the source** ticked if the collection must mirror the origin.
7. Choose the **groups and roles** that may read the documents, then **Save**.

**Safety net.** If a sync finds that *more than half* of the known documents have vanished (or the listing is empty), it assumes a mistake — an unmounted share, a wrong address — and **deletes nothing**. The run shows a warning, and you can confirm with **Sync and delete them** if the removal is really intended.

**Network rules.** Sources that go out to the network follow the project's allowed-hosts list of Tools Studio (chapter 5). If a host is not allowed, the run fails with a clear message. When you delete a source you choose **Keep documents** (default) or **Delete documents too**.

## 4.5 RAG Studio: pipelines

A **RAG pipeline** is a saved recipe: *which collection to search, how to search it, which model writes the answer, and how*. Like prompts, pipelines are versioned and can be published.

Open **RAG Studio**. The **RAG Library** lists the pipelines.

![The RAG library](../images/en/04-rag-library.png)

The controls work like the Prompt Library (chapter 3): search by name (marker 1), filter by status (marker 2) and by time range (marker 3), **New RAG** (marker 4) and the table (marker 5) with **Name**, **Status**, **Version**, **Data Source**, **Search** method and **Updated**. Row actions: **Try**, **Edit**, **Duplicate**, **Delete**.

## 4.6 Configure a pipeline

Click **New RAG** (or **Edit**). Click the title to name it, for example `policies-qa`.

![The pipeline editor](../images/en/04-rag-editor.png)

The status tag next to the name shows **Draft** or *Published*, and **Index ready** (or *building*) tells whether the index of this configuration is available.

### Data and models

| Field | What to choose |
| --- | --- |
| **Data Source** (marker 1) | The collection to search. Collections are created in Projects Studio → Data. |
| **LLM** (marker 2) | The model that **writes the answer** from the retrieved chunks. An alias is recommended (chapter 2). |
| **Embedding Model** (marker 3) | The model that turns chunks *and questions* into vectors. **Changing it builds a new index.** Use the same embedding model for the whole life of a pipeline unless you have a reason. |

### Retrieval

| Field | Meaning |
| --- | --- |
| **Search Method** (marker 4) | **Vector Search**: similarity of meaning only. **Semantic Search**: vector search followed by a *reranker*. **Hybrid Search**: vector search plus keyword search (BM25), merged, then reranked. Hybrid is the best default: it finds passages by meaning *and* by exact words such as product codes or names. |
| **Reranker** | An optional model that re-orders the candidates by relevance (0 to 1). *None* uses plain similarity. |
| **Top_k** | How many chunks are sent to the model. More chunks give more context but cost more tokens and can distract the model. Four to eight is typical. |
| **Similarity Threshold** (marker 5) | The minimum score (0 to 1) a chunk needs to be used. If **no** chunk passes, the answer says that *no relevant information was found* instead of guessing. Raise it to be stricter. |
| **Candidates** | How many chunks each retriever fetches before merging and reranking. |

### Chunking

| Field | Meaning |
| --- | --- |
| **Node Parser** | How documents are cut: **Structure-aware (by heading)**, **Recursive (paragraphs, sentences)** or **Fixed size**. Structure-aware is best for well-written documents with headings. |
| **Chunk Size** (marker 6) | In tokens, usually 100 to 1000. Small chunks are precise; large ones keep more context. |
| **Chunk Overlap** | In tokens, usually 10 to 20% of the size, so that a sentence cut at a boundary still appears whole in one chunk. |

Saving a change here **builds a new index in the background** (the **Index** area shows the progress). Tests keep using the previous ready index until the new one is ready.

### Answer

| Field | Meaning |
| --- | --- |
| **Prompt** | Optional. A prompt from the Prompt Library that tells the model how to use the chunks. Its *published* version is used, and `{context}` and `{question}` are filled in for you. Without it Forge uses default instructions: answer only from the sources and cite them. |
| **Response Mode** | **Compact**: one request with all chunks. **Refine**: one chunk at a time, for very long or mixed context. |
| **Context budget** | The maximum number of tokens of chunks put in the request. Lower-ranked chunks are dropped first. |
| **Temperature** | Low values give faithful answers; keep it low for RAG. |
| **Version note** | A sentence saved with the version. |

![Save buttons](../images/en/04-rag-editor-answer.png)

**Save Configuration** (marker 1) creates the next version. **Save Configuration & Test RAG** (marker 2) saves and opens the test page. A **Versions** drawer works exactly as in Prompt Studio: every save is a new immutable version; you can read an old one and load it back.

## 4.7 Test a pipeline and read the answer

![The RAG test page](../images/en/04-rag-test.png)

1. Choose the version at the top (the latest by default).
2. Type a **Question** (marker 1), for example *How many days of annual leave do I have, and how do I ask for them?*
3. Click **Proceed** (marker 2).

If the index of this version is still being built, a notice says that the answer uses the previous ready index and may be incomplete.

![A cited answer](../images/en/04-rag-answer.png)

The result shows:

- the **Answer** (marker 1), in which each statement ends with a number between square brackets;
- **Sources** (marker 2): the numbered list of the passages used, each with the file name and, for PDFs, the page. Click a source to open the document at that page (a PDF opens on the page; text files open as plain text);
- the statistics (marker 3): **Latency**, **Cost**, **Model** that really answered and the tokens used;
- **Trace** (marker 4): the detail of the whole path.

If nothing relevant is found, Forge says so: *No relevant information was found in the knowledge base for this question.* This is the intended behaviour; it protects users from invented answers.

### Read the trace

![The retrieval trace](../images/en/04-rag-trace.png)

The trace explains **why** the answer is what it is: the **Retrieval** table lists the candidate chunks with their **Document**, **Page**, **Score**, **Cosine** similarity, their **Ranks** in each search (vector, keyword, reranker) and whether they were **Used** in the prompt. Below it you find the exact messages sent to the model (**Input**), the **Output**, the **Total usage** and the **Metadata**. When an answer is wrong, the trace tells you whether the right passage was never retrieved (change chunking, search method or threshold), was retrieved but ranked too low (raise *Top_k* or add a reranker), or was used but misread (improve the prompt or the model).

## 4.8 Publish

As with prompts, the test page has **Publish v…**. Use cases, agents and the API follow the *published* version. The library shows **Published** and the published version number.

## 4.9 Good practice

- **Start small.** Upload five representative documents, build the pipeline, ask real questions, read the traces. Then add the rest.
- **Write documents for retrieval.** Clear headings, one topic per section, no scanned images when you have the text.
- **Restrict at the source.** Put confidential files in groups from the first upload; it is easier than fixing it later.
- **Keep the threshold honest.** A threshold of zero always answers; a sensible threshold makes "I don't know" possible.
- **Use hybrid search** when your documents contain codes, names or acronyms.
- **Do not change the embedding model casually.** It rebuilds the index and costs time and tokens.

## 4.10 Troubleshooting

| Symptom | Cause and remedy |
| --- | --- |
| "RAG Studio needs the Builder or Project Admin role" | Your role cannot edit pipelines. |
| *Create a Data Collection first* | The pipeline needs a collection. Create one in Projects Studio → Data. |
| A file is *Failed* | Read the reason in the row; fix and click **Retry**. |
| The answer says nothing relevant was found | The question is outside the documents, or the threshold is too high, or the documents are still being indexed. Check the **Index** status and the trace. |
| The answer ignores a document you know is there | The reader may lack access (section 4.3), the file may not be *Success* yet (still *In progress*), or it ranked below *Top_k*. Open the trace. |
| A source run says "nothing was deleted" | The safety net held back a mass deletion. Check the origin, then confirm with **Sync and delete them** only if the removal is intended. |
| A source cannot reach its host | The host is not in the project's allowed list (chapter 5), or the installation uses a proxy. Ask your administrators. |
| A *filesystem* source is refused | Your administrators must mount the directory and allow it for the project. |

Next: [5. Tools Studio](05-tools.md)
