# Demo project: Meridiana Assicurazioni

A complete, connected example of what Forge does, built through the public API so that every
object exists the way a customer would create it. **The company, people, policy documents and
claims are invented**; the weather data the tools return is real (Open-Meteo archive).

The company is a mid-size insurer in Bologna. The project is the claims desk for the Auto and
Home lines: customers ask questions on the portal, call-centre staff and adjusters ask an internal
assistant, and every claim above 1,500 euro goes through a human approval.

## Create it

Needs a running stack (`make docker/run`), model provider keys (`make providers/import`) and a
Super Admin.

```bash
FORGE_DEMO_ADMIN=<super admin> FORGE_DEMO_ADMIN_PASSWORD=<password> make demo/seed
# or one stage: make demo/seed STAGES="activity"
```

Stages are `project models knowledge prompts tools guardrails agents usecases integrations
evaluations activity`. Every stage finds its objects by name, so running the script again only
fills what is missing. The last stage uses the models for real (about 60 calls: chats, claim runs,
an evaluation) and waits out the free tiers' rate limits, so expect 10 to 20 minutes.

Check it in a browser (pictures land in `frontend/e2e/.results/demo`):

```bash
FORGE_E2E_DEMO_PASSWORD=<password> scripts/test-e2e.sh e2e/demo.spec.ts
```

## People

All people use the password `Meridiana-2026-Demo!` (set `FORGE_DEMO_PASSWORD` before seeding to
choose another). The script completes their first sign-in (new password, terms of use).

| User | Name | Project role | Groups |
|---|---|---|---|
| giulia.ferraris | Giulia Ferraris | Project Admin | Compliance e Legale |
| marco.bellandi | Marco Bellandi | Analista Processi (Builder) | Liquidatori Auto |
| chiara.moretti | Chiara Moretti | Liquidatore (Operator) | Liquidatori Auto |
| davide.conti | Davide Conti | Operatore Contact Center (End user) | Contact Center |
| paolo.gallo | Paolo Gallo | Operatore Contact Center (End user) | Contact Center |
| elena.rinaldi | Elena Rinaldi | Compliance (Viewer) | Compliance e Legale |

The Super Admin who runs the script owns the project.

## What is in it

| Area | Objects |
|---|---|
| Models | Aliases `default-chat` (gpt-oss-120b, then 20b), `fast`, `strong-chat`, `embedding` (mistral-embed) |
| Data | Collection of 8 documents: Auto and Home conditions, claims guide, FAQ, deductibles table, hail guidelines, privacy notice, and the internal liquidation procedure. The procedure is visible to *Liquidatori Auto* and *Compliance e Legale* only, the hail guidelines to *Liquidatori Auto* and *Contact Center* |
| RAG | Pipeline *Ricerca condizioni e procedure* (hybrid, top 5), answers with citations |
| Prompts | Claim classification (JSON), customer letter, case summary, and the instructions of both agents |
| Tools | `geocodifica_localita` and `meteo_storico` (HTTP, egress allow-list on the two Open-Meteo hosts) and `coordinate_comune_python` (Python in the sandbox, network limited to the geocoding host) |
| Agents | *Assistente liquidatori* (RAG + both tools + clock, long-term memory) and *Assistente portale clienti* (RAG only) |
| Guardrails | Fiscal code, IBAN, card, phone and email masked before any model; prompt injection and secrets blocked |
| Use cases | *Istruttoria sinistro con approvazione* (classify, adjuster agent, router on 1,500 euro, human approval by *Liquidatore*, customer letter, DOCX) and *Controllo meteo eventi diffusi* (loop over cities) |
| Triggers | Cron at 07:30 Europe/Rome for the weather check; inbound URL *Portale denunce online* for the claims intake |
| Evaluations | Dataset of 12 questions with expected answers and source documents; baseline run (recall@5, MRR, correctness) |
| Integrations | MCP tools `cerca_condizioni_polizza`, `assistente_liquidatori`, `classifica_denuncia`; widget *Portale clienti*; API key *Integrazione CRM sinistri* |
| Activity | Chats by four people, five claim runs (one succeeded, one through the inbound URL, one approved, one rejected, one waiting for approval), gateway calls, a public widget conversation |

## A walk through

1. **Sign in as chiara.moretti**, open *Approvals*: one claim (hail in Bologna, 3,200 euro) waits for her until someone decides it (the seed leaves it pending; run the `usecases` and `activity` stages on a fresh project to get another). Open it, read the adjuster's proposal and approve or reject.
2. **Use Cases Catalog**: run *Istruttoria sinistro con approvazione* with a small amount (no approval) and with a large one (the run pauses, the approval appears).
3. **Agentic AI Studio → Chat** with *Assistente liquidatori*: ask about the deductible for a theft, then about hail on 2026-07-02 in Bologna (the agent geocodes and checks the real weather, then compares with the 10 mm / 60 km/h thresholds).
4. **Sign in as davide.conti** and chat with *Assistente portale clienti*; write a fiscal code in the message and look at *Governance → Guardrails* afterwards: the event is logged, the text never reached the model.
5. **Projects Studio → Data**: the internal procedure is shown to adjusters only. Ask the portal assistant about adjuster approval thresholds: it cannot cite it.
6. **Governance → Evaluations**: open the baseline run, change the pipeline (chunk size, top k), run again and compare the two runs.
7. **MCP Studio**: copy the connection snippet into an MCP client; the collection, the agent and the classifier appear as tools.
8. **Projects Studio → Integrations**: the widget's embed code for the customer portal. The widget also works from `http://localhost:8080`, which is on its allowed sites for this reason.
9. **Analytics** and **Model Insights** (Super Admin): cost and latency of what the seed ran; *Activity* shows who did what.
10. **Build Your Own Use Case → Library**: open the workflow canvas, the triggers page (cron and inbound URL) and the version history.

## What it does not include, and why

- **E-mail for approvals**: SMTP is a platform setting; configure it in *Super Admin → Email* to receive approval links.
- **Scheduled RAG sources**: the S3, URL, Git and file-system connectors need a source the demo cannot assume (private hosts are refused by default).
- **SQL tools**: they need a database to point at. The widget is attached to the portal agent only because a widget accepts agents that just search documents.
- **Single sign-on**: needs a directory (`compose --profile idp-test` provides an OpenLDAP).

## Notes

- Model calls use the lab providers' free tiers. A rate-limited step shows up in the history as a failure; the seed retries its own chats, but a run you start during a busy minute can fail the same way.
- The `.state.json` file next to the script keeps ids and the one-time secrets (API key, inbound URL token); it is ignored by Git.
- Removing the demo: *Super Admin → Projects* archive and delete the project, and its users if wanted.
