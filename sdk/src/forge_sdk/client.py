"""HTTP client for the parts of the Forge API that automation uses: bundles (export and import),
use case runs, and the OpenAI-compatible gateway.

Authentication is a project API key (`fk_…`). What a key may do is limited to its scopes and to
what its creator may do: exporting and importing assets needs a key with `asset.write` (and
`project.manage` for configuration items), running use cases needs `usecase.run`, chat needs
`llm.invoke`.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


class ForgeError(Exception):
    """The API refused or failed. `code` is the problem code (for example `forbidden`)."""

    def __init__(self, status: int, code: str, title: str, detail: str | None = None) -> None:
        super().__init__(f"{status} {code}: {title}" + (f" ({detail})" if detail else ""))
        self.status, self.code, self.title, self.detail = status, code, title, detail


class ForgeClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        project_id: str | None = None,
        *,
        timeout: float = 60.0,
        http: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._project_id = project_id
        self._http = http or httpx.Client(timeout=timeout)
        self._headers = {"Authorization": f"Bearer {api_key}", "User-Agent": "forge-sdk/0.1"}

    # ---- plumbing
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ForgeClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._http.request(method, self.base_url + path, headers=self._headers, **kwargs)
        if response.status_code >= 400:
            try:
                problem = response.json()
            except ValueError:
                problem = {}
            raise ForgeError(
                response.status_code,
                str(problem.get("code", f"http_{response.status_code}")),
                str(problem.get("title", response.reason_phrase or "Request failed")),
                problem.get("detail"),
            )
        return response.json() if response.content else None

    @property
    def project_id(self) -> str:
        if self._project_id is None:
            self._project_id = self.whoami().get("project_id")
            if not self._project_id:
                raise ForgeError(400, "no_project", "Use a project API key or set the project id")
        return self._project_id

    def _project(self, path: str) -> str:
        return f"/api/v1/projects/{self.project_id}{path}"

    # ---- identity
    def whoami(self) -> dict[str, Any]:
        """The key's project and the permissions it really holds."""
        return self._request("GET", "/api/v1/whoami")  # type: ignore[no-any-return]

    # ---- bundles
    def export_bundle(
        self,
        *,
        versions: str = "latest",
        config: bool = False,
        **selection: Any,
    ) -> dict[str, Any]:
        """Every prompt, RAG pipeline, agent, use case and collection of the project (unless
        `selection` names items), with `versions` of each: `latest`, `published` or `all`.
        `config=True` adds model aliases, gateway limits, user groups and the guardrail policy
        (the key needs `project.manage`)."""
        body: dict[str, Any] = {"everything": not selection, "everything_versions": versions}
        if config:
            body |= {
                "model_aliases": True,
                "gateway_config": True,
                "user_groups": True,
                "guardrail_policy": True,
            }
        body |= selection
        return self._request("POST", self._project("/export"), json=body)  # type: ignore[no-any-return]

    def import_bundle(
        self, bundle: dict[str, Any], *, policy: str = "new_version", dry_run: bool = True
    ) -> dict[str, Any]:
        """Dry run by default: the report says what would be created, extended, renamed or
        skipped. `policy` is `new_version`, `rename` or `skip` for items that exist."""
        return self._request(  # type: ignore[no-any-return]
            "POST",
            self._project("/import"),
            json={"bundle": bundle, "policy": policy, "dry_run": dry_run},
            timeout=300,
        )

    # ---- use cases
    def list_use_cases(self) -> list[dict[str, Any]]:
        """The use cases this key may run (the catalog view)."""
        return self._request("GET", self._project("/catalog/use-cases"))  # type: ignore[no-any-return]

    def _use_case_id(self, name_or_id: str) -> str:
        for u in self.list_use_cases():
            if name_or_id in (u["id"], u["name"]):
                return str(u["id"])
        raise ForgeError(404, "not_found", f"No runnable use case '{name_or_id}'")

    def start_run(
        self, use_case: str, inputs: dict[str, Any] | None = None, **options: Any
    ) -> dict[str, Any]:
        body = {"inputs": inputs or {}, "delivery": "async"} | options
        return self._request(  # type: ignore[no-any-return]
            "POST", self._project(f"/use-cases/{self._use_case_id(use_case)}/runs"), json=body
        )

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._request("GET", self._project(f"/use-case-runs/{run_id}"))  # type: ignore[no-any-return]

    def run_use_case(
        self,
        use_case: str,
        inputs: dict[str, Any] | None = None,
        *,
        wait: bool = True,
        timeout: float = 300.0,
        poll: float = 1.0,
    ) -> dict[str, Any]:
        """Start a run of the published version; with `wait`, poll until it ends."""
        run = self.start_run(use_case, inputs)
        if not wait:
            return run
        deadline = time.monotonic() + timeout
        while run["status"] not in TERMINAL:
            if time.monotonic() > deadline:
                raise ForgeError(408, "timeout", f"Run {run['id']} still {run['status']}")
            time.sleep(poll)
            run = self.get_run(run["id"])
        return run

    # ---- gateway (OpenAI-compatible)
    def models(self) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/models")["data"]  # type: ignore[no-any-return]

    def chat(self, model: str, messages: list[dict[str, Any]], **options: Any) -> dict[str, Any]:
        return self._request(  # type: ignore[no-any-return]
            "POST", "/v1/chat/completions", json={"model": model, "messages": messages} | options
        )
