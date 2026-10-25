# 5. Tools Studio

[Index](README.md) · Previous: [4. Data and RAG Studio](04-data-and-rag.md) · Next: [6. Agentic AI Studio](06-agents.md)

A language model can only write text. A **tool** lets it *do* something: look up a value in an API, query a database, call a company service, run a small calculation. In Tools Studio you define tools **once**, test them, publish them, and then agents and use cases can use them. Forge handles the dangerous parts for you: secrets are stored encrypted and never reach the model, outgoing calls are restricted to hosts you allow, and every call is logged.

> **Who is this for?** Builders and Project Admins create and publish tools. Only **Project Admins** manage the *Connections* (allowed hosts, credentials, data sources, MCP servers). Other roles do not see Tools Studio.

## 5.1 Concepts

| Word | Meaning |
| --- | --- |
| **Tool** | A named action with typed *parameters* and a *description* that the model reads to decide when to use it. |
| **Type** | **HTTP** (call a web API), **SQL (read-only)** (query a database), **Python (sandbox)** (a small function) and **MCP** (a tool that comes from an MCP server). |
| **Version** | Each save creates a new immutable version. A tool is either *Draft* or *Published* at one version. Agents pin the published version when they are saved. |
| **Connections** | The shared building blocks: *allowed hosts*, *credentials*, *data sources*, *MCP servers*. |
| **Call log** | The record of every call, from tests and from agents. |

Tools Studio has three tabs: **Tool Library**, **Connections** and **Call log**.

## 5.2 The tool library

![The tool library](../images/en/05-library.png)

| Marker | Control | Use |
| --- | --- | --- |
| 1 | **Search by name** | Filters as you type; next to it you can filter by **Type** and **Status**. |
| 2 | **Import OpenAPI** | Creates many tools at once from an API description (section 5.6). |
| 3 | **New tool** | Opens the tool editor. |
| 4 | The table | **Name** and description, **Type**, **Status** (*Draft* or *Published*), **Version** (for example *v2 (published v1)*), **Flags** and **Updated**. |

The **Flags** column warns you: **Can change data** (orange) marks tools that may modify something outside Forge, namely HTTP tools with a method other than GET and every MCP tool, because Forge cannot know what a remote server does; **Needs approval** (blue) marks tools that wait for a person's decision (section 5.4).

Row actions: **Try / Edit** opens the editor and its test console, **Publish** / **Unpublish**, **Duplicate** (asks for the name of the copy) and **Delete** (refused while an agent still uses the tool).

## 5.3 Create a tool

Click **New tool**. The editor opens on the left with the definition and, once the tool is saved, a **Test console** below it.

![A new tool](../images/en/05-new-tool.png)

Fields common to every type:

| Field | Meaning |
| --- | --- |
| **Name (lower-case, digits, underscores)** | The name the model uses, for example `exchange_rate`. It cannot be changed later. |
| **Type** | HTTP, SQL (read-only) or Python (sandbox). |
| **Description the model reads** | The most important field. It tells the model *what the tool does and when to use it*. Write it like instructions to a colleague: "Latest foreign exchange rates for a base currency. Use it for any question about currency conversion." |
| **Timeout (seconds)** | From 1 to 60. A call that takes longer is stopped. |
| **Requires approval** | See section 5.4. |

### HTTP tools

An HTTP tool calls a web API.

![An HTTP tool](../images/en/05-tool-http.png)

1. Choose the **Method** and write the **URL**. Put `{name}` in the URL for a *path parameter*, for example `https://api.example.com/customers/{id}`.
2. Choose a **Credential** if the API needs authentication (credentials are created in the Connections tab, section 5.7). Forge adds it to the request itself: the model never sees it.
3. **Static headers** (one `Name: value` per line) are sent with every call, for example `Accept: application/json`.
4. **Response limit for the model (characters)** cuts long answers (500 to 64,000).
5. Declare the **Parameters** the model may fill in (**Add parameter**, marker 1). For each: the **Name**, the **Location** (*path*, *query*, *header* or *body*), the **Type** (string, integer, number, boolean, array, object), a **Description**, optional **Allowed values** (`a, b, c`) and whether it is **required**. Parameters are *the only way* the model can influence the request: headers and body come only from declared parameters, and unknown arguments are refused.
6. Click **Create tool** (or **Save as new version** on an existing tool). Add a **Version note** if you like.

### SQL tools (read-only)

A SQL tool runs **one `SELECT` (or `WITH`) statement** on a PostgreSQL data source of the project.

1. Choose the **Data source** (created in Connections, section 5.7).
2. Write the query and use `:name` for values supplied by the model, for example `SELECT id, status FROM orders WHERE customer = :customer`.
3. Declare each parameter (name, type, description, required).
4. Set the **Row limit** (up to 1,000).

Values are always passed as *bound parameters*, never pasted into the text, so they cannot change the meaning of the query. The statement runs in a **read-only transaction** with a time limit, and anything other than one SELECT is refused.

### Python tools (sandbox)

For a small calculation or a transformation that no API offers, write a Python function.

![A Python tool](../images/en/05-tool-python.png)

1. In the code editor (marker 1) define `run(args)`: it receives the arguments as a dictionary and **returns a value that can be written as JSON**, for example `def run(args): return {"words": len(args["text"].split())}`.
2. Declare the **Arguments** (**Add argument**): name, type, description, required.
3. Set **Memory (MB)** (32 to 512).

The code runs in a throw-away container (isolated with gVisor where your administrators enabled it) with **no files, the standard library only**, a memory limit and a time limit, and **no network** unless you list hosts in *Network access*. Each host you list must also be in the project's **Allowed hosts**; the code reaches them over HTTP and HTTPS only (Python's `urllib` does this by itself), and everything else, including private addresses, is refused. It is meant for computation and for small calls to a service you trust. Python tools need the *sandbox* service, which your administrators enable (chapter 12); without it a Python tool fails and reports that the sandbox service is not available or not configured.

### MCP tools

MCP tools are not written here: they are **imported** from an MCP server registered in the Connections tab (section 5.7). In the editor of an imported tool you can change only the description, the timeout and the approval flag.

## 5.4 Requires approval

Tick **Requires approval** for tools that change something important (send an e-mail, create an order). In a **use case**, a *tool* step that uses such a tool stops *before* the call and asks a person to approve it, and the person may even edit the arguments (chapter 7, *Approvals*). If nobody approves, the tool is never called.

> **Current limitation.** An **agent** that calls such a tool on its own (in a chat, or inside a use case step that is an agent) is **refused** with the message that approval is not available: approvals pause use-case steps, not the inner reasoning loop of an agent. The label of the checkbox in the editor still says "not available until human approval ships", which is out of date: the approval flow exists, with this limitation.

## 5.5 Test, then publish

Below the definition (after saving) the **Test console** lets you run the tool for real.

![The test console](../images/en/05-tool-test.png)

1. Choose the **Version** to test.
2. Fill in the arguments (one field per parameter; required ones are marked with an asterisk).
3. Click **Run** (marker 1).
4. The result (marker 2) shows a status tag (**ok** or an error code), the **latency**, the **Request** that was really sent (secrets masked) and **What the model reads**: the text that goes back to the model. Check that last block carefully: it is what the model will base its next step on, so it should be short, clear and free of noise. You will notice that the answer of an external API is wrapped in `<untrusted-content>` tags followed by a note: this tells the model that the text is *data from an outside system, never instructions*, which is a defence against prompt injection (a web page or an API that tries to give orders to the model).

Every test is recorded in the call log. When you are satisfied, go back to the library and click **Publish**. Only a *published* tool can be added to an agent.

## 5.6 Import an API from OpenAPI

If an API already has an OpenAPI description (version 3.x), import its *read* operations in one go.

![Import from OpenAPI](../images/en/05-import-openapi.png)

1. Click **Import OpenAPI**.
2. Paste the **OpenAPI document** (JSON or YAML, up to 2 MB) or choose a file, then click **Read operations**.
3. The operations are listed. Tick the ones you want, fill in the **Base URL** (if the document does not give one) and an optional **Credential**.
4. Click **Import selected**. One tool is created for each operation, named after it; existing names are skipped and reported.

Imported tools are *drafts*: review the descriptions (they come from the API document and may be too technical), test them and publish them.

## 5.7 Connections

Only Project Admins can change this tab; Builders can use what is listed.

![Connections](../images/en/05-connections.png)

### Allowed hosts

HTTP tools, MCP servers and data sources can reach **only the hosts listed here**. An **empty list allows nothing**. Write one host per line; use `*.example.com` for all subdomains. Click **Save hosts**. Even listed hosts must be on a public address: private addresses (internal networks) are refused unless your administrators deliberately allowed them for the whole installation (a notice on the page tells you when that is the case).

### Credentials

A **credential** is a named secret used by tools. Type a **Name**, choose the kind, type the **Secret** and click **Add**:

| Kind (as shown) | Extra field | Sent as |
| --- | --- | --- |
| **Bearer token** | none | `Authorization: Bearer …` |
| **Basic (user and password)** | **User** | HTTP basic authentication |
| **Header** | **Header or parameter name** | A header with that name |
| **Query parameter** | **Parameter name** | A query-string parameter |

The secret is stored encrypted and is never shown again. The table shows the credential's **Name**, **Kind** and **Used by** (which tools use it); **Delete** is refused while a tool uses it.

### Data sources (PostgreSQL)

A **data source** is a read-only connection to a PostgreSQL database for SQL tools and SQL sources. Fill in **Name**, **Host**, port, **Database**, user and **Password**, and choose the **TLS** mode: *No TLS*, *TLS (no verification)* or *TLS (verify certificate)*. Use a database account that has **read-only rights** on the tables you want to expose; Forge adds its own read-only transaction, but least privilege is the first line of defence.

### MCP servers

An **MCP server** offers ready-made tools. Fill in **Name**, **Transport** (Streamable HTTP, SSE, or *stdio (allowed commands only)*), the **Target** (the URL, or for stdio the **Command** and **Arguments**) and, if needed, a **Credential**, then **Add**. Click **Discover tools** on a server to list its tools, tick the ones you want and click **Import selected**: they become tools of the library. The *stdio* transport starts a program inside the Forge server, so it is off unless your administrators allow named commands.

## 5.8 The call log

![The call log](../images/en/05-calls.png)

Every call is recorded, including calls that were refused. Filter by **Tool** (*All tools*), **Status** (*Any status*) and **Source** (*Any source*: the test console or an agent). The table shows the **Time**, the **Tool**, the **Status**, the **Source**, the **Latency** and a **Details** link that opens the **Input** and **Output** (secrets are scrubbed, and long values are shortened to 64 KB). Use it to find out *who called what and what came back*.

## 5.9 Good practice

- **Write descriptions for the model.** A vague description means a tool that is used at the wrong time or never.
- **Prefer GET and read-only.** Tools that change data deserve approval, tests, and a narrow scope.
- **Keep answers small.** A short, clean response is cheaper and makes the model more accurate: use the *Response limit* and, where you can, pick the fields in the API call.
- **Least privilege everywhere.** A dedicated read-only database user; the narrowest list of allowed hosts; one credential per integration.
- **Test the failure cases** as well: wrong parameters, an unreachable host. The test console shows the error code the model will receive.

## 5.10 Troubleshooting

| Symptom | Cause and remedy |
| --- | --- |
| "Your role cannot manage tools in this project" | Only Builders and Project Admins use Tools Studio. |
| The call is refused because the host is not allowed | Add the host to **Allowed hosts** (Project Admin). |
| The call is refused for a private address | Internal addresses are blocked by default. Ask your administrators whether the deployment allows them. |
| The tool cannot be added to an agent | It is not **published**, or it was deleted. |
| *Delete* is refused | An agent still uses the tool or the credential; remove the reference first. |
| A Python tool reports that the sandbox is unavailable or not configured | The sandbox service is not enabled on this installation (chapter 12). |
| The model never uses the tool | The description does not say when to use it, or the agent's instructions do not mention it. |

Next: [6. Agentic AI Studio](06-agents.md)
