#!/usr/bin/env python3
"""Idempotent demo data for the user guide screenshots (docs/guide): the project "Forge Demo".

Usage: FORGE_URL=http://localhost:8080 FORGE_DOCS_USER=docs-admin FORGE_DOCS_PASSWORD=... docs-seed.py
Only the standard library is used; it talks to the public API like any client.
"""
import http.cookiejar
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("FORGE_URL", "http://localhost:8080").rstrip("/")
PROJECT = "Forge Demo"
MODEL_FIELDS = (
    "model_type", "model_id", "params", "embedding_dims", "timeout_s", "name", "display_name",
    "provider_id", "context_window", "max_output_tokens", "cost_input", "cost_output",
    "capabilities", "max_retries", "rpm_limit", "tokenizer", "all_projects",
    "enabled_project_ids", "status",
)


class Api:
    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ""

    def call(self, method: str, path: str, body=None, ok=(200, 201, 202, 204)):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(BASE + path, data=data, method=method)
        req.add_header("content-type", "application/json")
        if self.csrf:
            req.add_header("X-CSRF-Token", self.csrf)
        try:
            with self.opener.open(req) as res:
                raw = res.read()
                status = res.status
        except urllib.error.HTTPError as err:
            raw, status = err.read(), err.code
        if status not in ok:
            raise SystemExit(f"{method} {path} -> {status}: {raw[:400].decode(errors='replace')}")
        return json.loads(raw) if raw else None

    def upload(self, path: str, files: list[tuple[str, str]], fields: dict[str, list[str]]):
        """multipart/form-data with text files: files = [(name, content)], fields = {name: [values]}."""
        boundary = "----forgedocs" + secrets.token_hex(8)
        parts: list[bytes] = []
        for key, values in fields.items():
            for value in values:
                parts.append(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
                )
        for name, content in files:
            parts.append(
                (f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\n'
                 "Content-Type: text/markdown\r\n\r\n").encode() + content.encode() + b"\r\n"
            )
        parts.append(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(BASE + path, data=b"".join(parts), method="POST")
        req.add_header("content-type", f"multipart/form-data; boundary={boundary}")
        req.add_header("X-CSRF-Token", self.csrf)
        try:
            with self.opener.open(req) as res:
                return json.loads(res.read())
        except urllib.error.HTTPError as err:
            raise SystemExit(f"POST {path} -> {err.code}: {err.read()[:300].decode(errors='replace')}")

    def stream(self, path: str, body: dict) -> str:
        """POST that answers with a server-sent event stream: read it to the end."""
        req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST")
        req.add_header("content-type", "application/json")
        req.add_header("X-CSRF-Token", self.csrf)
        try:
            with self.opener.open(req, timeout=180) as res:
                return res.read().decode(errors="replace")
        except urllib.error.HTTPError as err:
            raise SystemExit(f"POST {path} -> {err.code}: {err.read()[:300].decode(errors='replace')}")

    def items(self, path: str) -> list:
        """A list endpoint, plain or paged ({items, next_cursor})."""
        out = self.call("GET", path)
        return out["items"] if isinstance(out, dict) else out

    def login(self, user: str, password: str) -> None:
        self.call("POST", "/api/v1/auth/login", {"login": user, "password": password})
        self.csrf = next((c.value for c in self.jar if c.name == "forge_csrf"), "")


def main() -> None:
    api = Api()
    api.login(os.environ["FORGE_DOCS_USER"], os.environ["FORGE_DOCS_PASSWORD"])
    me = api.call("GET", "/api/v1/me")["user"]

    # Demo users: a builder and an end user next to the admin.
    users = {u["username"]: u for u in api.call("GET", "/api/v1/admin/users")}
    for username, full in (("anna.builder", "Anna Rossi"), ("marco.user", "Marco Bianchi")):
        if username not in users:
            users[username] = api.call(
                "POST", "/api/v1/admin/users",
                {"username": username, "email": f"{username}@example.com", "full_name": full,
                 "is_super_admin": False, "password": "Demo-" + secrets.token_hex(8)},
            )

    projects = api.call("GET", "/api/v1/admin/projects")
    project = next((p for p in projects if p["name"] == PROJECT and p["status"] == "active"), None)
    if project is None:
        created = api.call(
            "POST", "/api/v1/admin/projects",
            {"name": PROJECT, "description": "Demo project used by the user guide.",
             "admin_user_id": me["id"]},
        )
        project = created.get("project", created)
    pid = project["id"]
    print("project", pid)

    role = {r["name"]: r["id"] for r in api.call("GET", f"/api/v1/projects/{pid}/roles")}
    have = {m["username"] for m in api.call("GET", f"/api/v1/projects/{pid}/members")}
    for username, role_name in (("anna.builder", "Builder"), ("marco.user", "User")):
        if username not in have:
            api.call("POST", f"/api/v1/projects/{pid}/members",
                     {"identifiers": [username], "role_name_id": role[role_name]})

    # Platform models enabled for the demo project (the lab providers of the dev stack).
    wanted = ("groq-gpt-oss-20b", "nim-gpt-oss-20b", "ministral-8b", "cf-llama-3.1-8b",
              "ollama-qwen2.5-1.5b", "ollama-bge-m3", "cf-bge-m3")
    for model in api.call("GET", "/api/v1/admin/models"):
        if model["name"] in wanted and pid not in model["enabled_project_ids"]:
            body = {k: v for k, v in model.items() if k in MODEL_FIELDS}
            body["enabled_project_ids"] = [*model["enabled_project_ids"], pid]
            api.call("PUT", f"/api/v1/admin/models/{model['id']}", body)

    # A provider and a model that exist only in this project, and an alias with a fallback chain.
    providers = api.call("GET", f"/api/v1/projects/{pid}/providers")
    internal = next((p for p in providers if p["name"] == "Internal vLLM"), None)
    if internal is None:
        internal = api.call(
            "POST", f"/api/v1/projects/{pid}/providers",
            {"name": "Internal vLLM", "provider_type": "vllm",
             "base_url": "https://llm.internal.example.com/v1", "api_key": None,
             "config": {"allow_http": False, "headers": {}}, "status": "active"},
        )
    project_models = api.call("GET", f"/api/v1/projects/{pid}/models")
    if not any(m["name"] == "qwen-internal" for m in project_models):
        api.call(
            "POST", f"/api/v1/projects/{pid}/models",
            {"model_type": "chat", "model_id": "Qwen/Qwen3-8B", "name": "qwen-internal",
             "display_name": "Qwen3 8B (internal)", "provider_id": internal["id"],
             "context_window": 32768, "max_output_tokens": 4096, "capabilities": ["streaming"],
             "all_projects": False, "enabled_project_ids": [], "status": "active"},
        )
    by_name = {m["name"]: m["id"] for m in api.call("GET", f"/api/v1/projects/{pid}/models")}
    api.call("PUT", f"/api/v1/projects/{pid}/aliases/support-chat",
             {"model_ids": [by_name["groq-gpt-oss-20b"], by_name["nim-gpt-oss-20b"],
                            by_name["ollama-qwen2.5-1.5b"]]})

    # Groups for restricted documents.
    groups = {g["name"]: g for g in api.call("GET", f"/api/v1/projects/{pid}/groups")}
    for name, description, members in (
        ("Finance", "Finance department", ["anna.builder"]),
        ("HR", "Human resources", ["marco.user"]),
    ):
        if name not in groups:
            groups[name] = api.call("POST", f"/api/v1/projects/{pid}/groups",
                                    {"name": name, "description": description})
            api.call("PUT", f"/api/v1/projects/{pid}/groups/{groups[name]['id']}/members",
                     {"user_ids": [users[u]["id"] for u in members]})
    seed_prompts(api, pid)
    seed_rag(api, pid, groups)
    seed_tools(api, pid)
    seed_agents(api, pid)
    seed_usecases(api, pid)
    seed_governance(api, pid)
    seed_integrations(api, pid)


def seed_prompts(api: Api, pid: str) -> None:
    base = f"/api/v1/projects/{pid}/prompts"
    have = {p["name"]: p for p in api.items(base)}
    if "customer-reply" not in have:
        version = {
            "model": "default-chat",
            "system_template": (
                "You are a customer support agent for {company}. Answer politely, in a {tone} "
                "tone, in at most 120 words. Never promise refunds."
            ),
            "user_template": "Customer message:\n{message}\n\nWrite the reply in {language}.",
            "variables": [
                {"name": "company", "value": "Acme Srl", "is_file": False, "required": True},
                {"name": "tone", "value": "friendly", "is_file": False, "required": True},
                {"name": "message", "value": "", "is_file": False, "required": True},
                {"name": "language", "value": "English", "is_file": False, "required": True},
            ],
            "examples": [
                {"input": "Where is my order 4471?",
                 "output": "Hello! Your order 4471 left our warehouse yesterday and should arrive "
                           "within two working days. Thank you for your patience."},
            ],
            "model_params": {"temperature": 0.3, "top_p": None, "max_tokens": 400},
            "note": "First version",
        }
        created = api.call("POST", base, {
            "name": "customer-reply",
            "description": "Polite reply to a customer message",
            "version": version,
        })
        prompt = created.get("prompt", created)
        pr = f"{base}/{prompt['id']}"
        v2 = dict(version, note="Shorter and warmer", model_params={"temperature": 0.5,
                                                                    "top_p": None,
                                                                    "max_tokens": 300})
        api.call("POST", f"{pr}/versions", v2)
        api.call("POST", f"{pr}/publish", {"version": 1})
        have["customer-reply"] = prompt
    api.call("PUT", f"{base}/{have['customer-reply']['id']}/tags/prod", {"version": 1})
    if "meeting-summary" not in have:
        api.call("POST", base, {
            "name": "meeting-summary",
            "description": "Summarise meeting minutes into decisions and actions",
            "version": {
                "model": "fast",
                "system_template": "You summarise meeting minutes. Output two lists: Decisions and Actions (owner, due date).",
                "user_template": "Minutes:\n{minutes}",
                "variables": [{"name": "minutes", "value": "", "is_file": True, "required": True}],
                "examples": [],
                "model_params": {"temperature": 0.2, "top_p": None, "max_tokens": 500},
                "note": "",
            },
        })


LEAVE = """# Leave policy

## Annual leave
Every employee has 26 working days of paid leave per calendar year. Requests are made in the
HR portal at least 10 working days in advance and are approved by the line manager.

## Carry-over
Up to 5 unused days can be carried over to the following year and must be used by 31 March.

## Sick leave
Tell your manager before 10:00 on the first day of absence. From the third day a medical
certificate is required.
"""

EXPENSES = """# Expenses policy

## What can be claimed
Travel, accommodation and meals during business trips are reimbursed against receipts.
Meals are reimbursed up to 35 euro per day.

## How to claim
Submit the expense report in the finance portal within 30 days of the trip. Reports above
500 euro need the approval of the department head.

## Not reimbursed
Fines, personal purchases and alcohol are never reimbursed.
"""

SALARY = """# Salary bands (confidential, HR only)

Band A: 28,000 - 38,000 euro. Band B: 36,000 - 52,000 euro. Band C: 50,000 - 75,000 euro.
Bonuses are decided once a year by the HR committee.
"""


def seed_rag(api: Api, pid: str, groups: dict) -> None:
    base = f"/api/v1/projects/{pid}/collections"
    have = {c["name"]: c for c in api.items(base)}
    if "Company policies" not in have:
        have["Company policies"] = api.call("POST", base, {
            "name": "Company policies", "description": "Leave, expenses and HR documents"})
        coll = have["Company policies"]["id"]
        api.upload(f"{base}/{coll}/documents",
                   [("leave-policy.md", LEAVE), ("expenses-policy.md", EXPENSES)], {})
        api.upload(f"{base}/{coll}/documents", [("salary-bands.md", SALARY)],
                   {"group_ids": [groups["HR"]["id"]]})
    coll = have["Company policies"]["id"]

    rag = f"/api/v1/projects/{pid}/rag"
    pipelines = {p["name"]: p for p in api.items(rag)}
    if "policies-qa" not in pipelines:
        created = api.call("POST", rag, {
            "name": "policies-qa",
            "description": "Answers questions about company policies, with citations",
            "config": {"collection_id": coll, "llm": "default-chat", "embedding_model": "embedding",
                       "search_method": "hybrid", "top_k": 4, "reranker": None},
        })
        pipelines["policies-qa"] = created.get("pipeline", created)
    pipe = pipelines["policies-qa"]["id"]

    # Wait for the files to be indexed (embeddings come from the lab providers: can take a minute).
    for _ in range(90):
        c = api.call("GET", f"{base}/{coll}")
        if c["status"] in ("success", "ready"):
            break
        time.sleep(4)
    else:
        print("collection not ready yet:", c["status"])
        return
    detail = api.call("GET", f"{rag}/{pipe}")
    cfg = detail["version"]["config"]
    if cfg["llm"] != "fast" or cfg["reranker"] is not None:
        # The `fast` alias answers in the language of the question more reliably than the lab chat chain.
        cfg = dict(cfg, llm="fast", reranker=None)
        api.call("POST", f"{rag}/{pipe}/versions", {"config": cfg, "note": "Demo configuration"})
        detail = api.call("GET", f"{rag}/{pipe}")
        api.call("POST", f"{rag}/{pipe}/publish", {"version": detail["latest_version"]})
        for _ in range(60):
            if api.call("GET", f"{rag}/{pipe}")["version"].get("kb_status") == "ready":
                break
            time.sleep(3)
    elif not detail.get("published_version"):
        api.call("POST", f"{rag}/{pipe}/publish", {"version": detail["latest_version"]})
    if not api.items(f"{rag}/{pipe}/runs"):
        api.call("POST", f"{rag}/{pipe}/query", {"query": "How many days of annual leave do I have?"})

    sources = f"/api/v1/projects/{pid}/sources"
    if not api.items(sources):
        api.call("POST", sources, {
            "name": "Docs website", "collection_id": coll, "type": "url",
            "config": {"start_urls": ["https://docs.example.com/"], "max_pages": 100, "max_depth": 2},
            "interval_minutes": 1440, "enabled": False, "delete_missing": True,
            "group_ids": [], "role_ids": [],
        })


def seed_tools(api: Api, pid: str) -> None:
    base = f"/api/v1/projects/{pid}"
    api.call("PUT", f"{base}/tool-policy", {"allowed_hosts": ["api.frankfurter.dev", "*.example.com"]})
    if not any(c["name"] == "crm-token" for c in api.call("GET", f"{base}/tool-credentials")):
        api.call("POST", f"{base}/tool-credentials",
                 {"name": "crm-token", "kind": "bearer", "secret": "demo-secret-not-a-real-token"})
    tools = {t["name"]: t for t in api.items(f"{base}/tools")}
    if "exchange_rate" not in tools:
        tools["exchange_rate"] = api.call("POST", f"{base}/tools", {
            "name": "exchange_rate",
            "description": "Latest foreign exchange rates for a base currency (ECB reference rates).",
            "config": {"kind": "http", "method": "GET", "url": "https://api.frankfurter.dev/v1/latest",
                       "params": [
                           {"name": "base", "in": "query", "type": "string", "required": True,
                            "description": "Base currency, ISO code such as EUR"},
                           {"name": "symbols", "in": "query", "type": "string", "required": False,
                            "description": "Comma-separated target currencies, such as USD,GBP"}],
                       "headers": {}, "credential": None, "response_max_chars": 4000},
            "timeout_s": 15, "note": "First version",
        })
    tool = tools["exchange_rate"]
    tool = tool.get("tool", tool)
    tid = tool["id"]
    if not tool.get("published_version"):
        api.call("POST", f"{base}/tools/{tid}/publish", {"version": 1})
    if not api.items(f"{base}/tool-calls"):
        api.call("POST", f"{base}/tools/{tid}/test", {"version": 1, "arguments": {"base": "EUR", "symbols": "USD,GBP"}})
    if "word_count" not in tools:
        api.call("POST", f"{base}/tools", {
            "name": "word_count",
            "description": "Counts the words and the lines of a text.",
            "config": {"kind": "python",
                       "code": "def run(args):\n    text = args['text']\n    return {'words': len(text.split()), 'lines': len(text.splitlines())}\n",
                       "params": [{"name": "text", "type": "string", "description": "The text to count", "required": True}],
                       "memory_mb": 128, "response_max_chars": 2000},
            "timeout_s": 10, "note": "",
        })


def seed_agents(api: Api, pid: str) -> None:
    base = f"/api/v1/projects/{pid}"
    prompts = {p["name"]: p for p in api.items(f"{base}/prompts")}
    if "assistant-instructions" not in prompts:
        created = api.call("POST", f"{base}/prompts", {
            "name": "assistant-instructions",
            "description": "Instructions of the internal assistant agent",
            "version": {
                "model": "default-chat",
                "system_template": (
                    "You are the internal assistant of Acme Srl. Use the policies search tool for "
                    "questions about leave, expenses and HR rules, and the exchange_rate tool for "
                    "currency questions. Answer briefly and say which source you used."
                ),
                "user_template": "{message}",
                "variables": [{"name": "message", "value": "", "is_file": False, "required": False}],
                "examples": [], "model_params": {"temperature": 0.2, "top_p": None, "max_tokens": 500},
                "note": "",
            },
        })
        prompts["assistant-instructions"] = created.get("prompt", created)
        api.call("POST", f"{base}/prompts/{prompts['assistant-instructions']['id']}/publish", {"version": 1})
    pid_prompt = prompts["assistant-instructions"]["id"]

    agents = {a["name"]: a for a in api.items(f"{base}/agents")}
    if "internal-assistant" not in agents:
        rag = next(p for p in api.items(f"{base}/rag") if p["name"] == "policies-qa")
        tool = next(t for t in api.items(f"{base}/tools") if t["name"] == "exchange_rate")
        created = api.call("POST", f"{base}/agents", {
            "name": "internal-assistant",
            "description": "Answers staff questions about policies and currency rates",
            "config": {
                "prompt": {"prompt_id": pid_prompt, "variables": {}},
                "model": "default-chat",
                "tools": [
                    {"kind": "rag", "pipeline_id": rag["id"], "name": "policies",
                     "description": "Search the company policies"},
                    {"kind": "library", "tool_id": tool["id"], "name": "exchange_rate"},
                    {"kind": "builtin", "name": "current_time", "allowed_hosts": []},
                ],
                "limits": {"max_steps": 8, "max_tokens": 50000, "timeout_s": 120, "max_cost_usd": 1.0},
                "temperature": 0.2, "max_output_tokens": 800, "guardrails": [],
                "memory": {"long_term": False, "embedding_model": "embedding", "recall": 5, "max_items": 200},
            },
        })
        agents["internal-assistant"] = created.get("agent", created)
    agent = agents["internal-assistant"]
    if not agent.get("published_version"):
        api.call("POST", f"{base}/agents/{agent['id']}/publish", {"version": 1})
    if not api.items(f"{base}/runs"):
        conv = api.call("POST", f"{base}/conversations", {"agent_id": agent["id"], "title": "Leave and currency"})
        cid = conv.get("conversation", conv)["id"]
        api.stream(f"{base}/conversations/{cid}/messages",
                   {"content": "How many days of annual leave do I have, and how many US dollars are 200 euro today?"})


def seed_usecases(api: Api, pid: str) -> None:
    base = f"/api/v1/projects/{pid}"
    catalogs = {c["name"]: c for c in api.call("GET", f"{base}/catalogs")}
    biz = catalogs["Forge.BizOps"]
    categories = {c["name"]: c for c in biz.get("categories", [])}
    for name in ("Customer service", "HR and policies"):
        if name not in categories:
            api.call("POST", f"{base}/catalogs/{biz['id']}/categories", {"name": name})
    biz = next(c for c in api.call("GET", f"{base}/catalogs") if c["name"] == "Forge.BizOps")
    categories = {c["name"]: c for c in biz["categories"]}
    role = {r["name"]: r["id"] for r in api.call("GET", f"{base}/roles")}
    api.call("PUT", f"{base}/roles/{role['User']}/catalog-access",
             {"catalogs": [{"catalog_id": biz["id"], "enabled": True, "excluded_categories": []}]})

    existing = {u["name"]: u for u in api.items(f"{base}/use-cases")}
    agent = next(a for a in api.items(f"{base}/agents") if a["name"] == "internal-assistant")

    def make(name, category, description, fields, definition):
        if name in existing:
            return existing[name]
        uc = api.call("POST", f"{base}/use-cases", {
            "name": name, "pattern": "graph", "description": description, "icon": "mail",
            "catalog_id": biz["id"], "category_id": categories[category]["id"],
            "input_fields": fields, "output_type": "markdown", "definition": definition,
        })
        uc = uc.get("use_case", uc)
        api.call("POST", f"{base}/use-cases/{uc['id']}/publish", {"note": "First version"})
        existing[name] = uc
        return uc

    reply = make(
        "Customer reply with review", "Customer service",
        "Drafts a reply to a customer message and waits for a person to approve it",
        [{"name": "message", "label": "Customer message", "type": "long_text", "required": True},
         {"name": "language", "label": "Reply language", "type": "select", "required": True,
          "options": ["English", "Italian"], "default": "English"}],
        {"nodes": [
            {"id": "start", "type": "start"},
            {"id": "draft", "type": "llm", "after": ["start"], "prompt_ref": "customer-reply@1",
             "variables": {"message": "{message}", "language": "{language}"}, "output": "draft"},
            {"id": "review", "type": "human_approval", "after": ["draft"], "input": "{draft}",
             "instructions": "Check the tone and make sure no refund is promised", "output": "final"},
            {"id": "end", "type": "end", "after": ["review"], "output": "{final}"},
        ]},
    )
    helper = make(
        "Policy helper", "HR and policies",
        "Answers a staff question about company policies with the internal assistant agent",
        [{"name": "question", "label": "Your question", "type": "long_text", "required": True}],
        {"nodes": [
            {"id": "start", "type": "start"},
            {"id": "ask", "type": "agent", "after": ["start"], "agent_ref": "internal-assistant@1",
             "task": "Answer this question: {question}", "output": "answer"},
            {"id": "end", "type": "end", "after": ["ask"], "output": "{answer}"},
        ]},
    )
    done = {r["use_case_id"] for r in api.items(f"{base}/use-case-runs")}
    if helper["id"] not in done:
        api.call("POST", f"{base}/use-cases/{helper['id']}/runs", {
            "inputs": {"question": "How do I claim travel expenses and what is the meal allowance?"},
            "mode": "published", "delivery": "async"})
    if reply["id"] not in done:
        api.call("POST", f"{base}/use-cases/{reply['id']}/runs", {
            "inputs": {"message": "Hello, my parcel arrived damaged. What can I do?", "language": "English"},
            "mode": "published", "delivery": "async"})
    if not api.items(f"{base}/triggers"):
        api.call("POST", f"{base}/use-cases/{helper['id']}/triggers", {
            "name": "Weekday morning digest", "kind": "cron", "cron": "0 8 * * 1-5",
            "timezone": "Europe/Rome", "inputs": {"question": "What changed in the leave policy?"},
            "enabled": False})
        api.call("POST", f"{base}/use-cases/{helper['id']}/triggers", {
            "name": "Helpdesk webhook", "kind": "webhook", "inputs": {}, "enabled": True})


def seed_governance(api: Api, pid: str) -> None:
    base = f"/api/v1/projects/{pid}"
    api.call("PUT", f"{base}/guardrails/policy", {"enabled": True, "rules": [
        {"type": "pii", "stage": ["input"], "action": "mask",
         "params": {"entities": ["EMAIL", "IBAN", "CREDIT_CARD"]}},
        {"type": "secrets", "stage": ["output"], "action": "block", "params": {}},
        {"type": "banned_topics", "stage": ["input"], "action": "block",
         "params": {"terms": ["insider trading"]}},
    ]})
    prompt = next(p for p in api.items(f"{base}/prompts") if p["name"] == "customer-reply")
    if not api.call("GET", f"{base}/guardrails/events"):
        # One real call through the gateway: the e-mail is masked and the intervention is logged.
        api.call("POST", f"{base}/prompts/{prompt['id']}/test", {
            "version": 1, "variables": {
                "message": "Please call me back, my e-mail is anna.rossi@example.com", "language": "English"}})
    datasets = {d["name"]: d for d in api.items(f"{base}/evals/datasets")}
    if "Reply checks" not in datasets:
        datasets["Reply checks"] = api.call("POST", f"{base}/evals/datasets", {
            "name": "Reply checks", "kind": "prompt", "description": "Three typical customer messages",
            "cases": [
                {"input": {"message": "Where is my order 4471?", "language": "English"}, "expected": "4471"},
                {"input": {"message": "My parcel arrived damaged.", "language": "English"}, "expected": "sorry"},
                {"input": {"message": "Can you refund me 500 euro right now?", "language": "English"}, "expected": "refund"},
            ]})
    ds = datasets["Reply checks"]
    runs = api.items(f"{base}/evals/runs")
    for version, name in ((1, "Baseline v1"), (2, "Candidate v2")):
        if any(r.get("name") == name for r in runs):
            continue
        run = api.call("POST", f"{base}/evals/runs", {
            "dataset_id": ds["id"], "target_id": prompt["id"], "target_version": version,
            "name": name, "evaluators": [{"type": "contains"}], "concurrency": 2})
        for _ in range(60):
            if api.call("GET", f"{base}/evals/runs/{run['id']}")["status"] not in ("queued", "running"):
                break
            time.sleep(3)


def seed_integrations(api: Api, pid: str) -> None:
    base = f"/api/v1/projects/{pid}"
    agent = next(a for a in api.items(f"{base}/agents") if a["name"] == "internal-assistant")
    prompt = next(p for p in api.items(f"{base}/prompts") if p["name"] == "customer-reply")
    coll = next(c for c in api.items(f"{base}/collections") if c["name"] == "Company policies")
    have = api.call("GET", f"{base}/mcp/exposures")
    have = have.get("items", have) if isinstance(have, dict) else have
    if not have:
        api.call("POST", f"{base}/mcp/exposures", {"agent_id": agent["id"], "name": "ask_assistant",
                                                  "description": "Ask the internal assistant a question"})
        api.call("POST", f"{base}/mcp/exposures", {"collection_id": coll["id"], "name": "search_policies",
                                                  "description": "Search the company policies"})
        api.call("POST", f"{base}/mcp/exposures", {"prompt_id": prompt["id"], "name": "customer_reply",
                                                  "description": "Draft a reply to a customer"})
    widgets = api.call("GET", f"{base}/widgets")
    widgets = widgets.get("items", widgets) if isinstance(widgets, dict) else widgets
    if not widgets:
        api.call("POST", f"{base}/widgets", {
            "name": "Website assistant", "agent_id": agent["id"], "title": "Ask Acme",
            "welcome": "Hi! Ask me about leave, expenses or currency rates.",
            "allowed_origins": ["https://www.example.com"], "enabled": True,
            "messages_per_minute": 10, "daily_limit": 500})


if __name__ == "__main__":
    sys.exit(main())
