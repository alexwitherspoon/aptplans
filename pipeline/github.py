"""GitHub Issues API for intake hints. Calls stay on the origin IP, not through PIA."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote
from urllib.request import Request, urlopen
import json
import os

from pipeline.intake import IntakeOutcome


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    state: str


class IssueClient(Protocol):
    def comment(self, number: int, body: str) -> None: ...

    def close(self, number: int) -> None: ...

    def open_intake_issues(self) -> list[Issue]: ...


class GitHubIntake:
    def __init__(self, client: IssueClient) -> None:
        self.client = client

    def apply(self, number: int, outcome: IntakeOutcome) -> None:
        self.client.comment(number, outcome.comment)
        if outcome.close:
            self.client.close(number)


class UrllibGitHub:
    def __init__(self, repo: str, token: str, user_agent: str = "aptplans.org") -> None:
        self.repo = repo
        self.token = token
        self.user_agent = user_agent

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict | list:
        url = f"https://api.github.com{path}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(url, data=data, method=method)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("User-Agent", self.user_agent)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        with urlopen(request, timeout=30) as response:
            body = response.read()
        if not body:
            return {}
        return json.loads(body.decode("utf-8"))

    def _repo_path(self) -> str:
        owner, name = self.repo.split("/", 1)
        return f"{quote(owner)}/{quote(name)}"

    def comment(self, number: int, body: str) -> None:
        repo = self._repo_path()
        self._request("POST", f"/repos/{repo}/issues/{number}/comments", {"body": body})

    def close(self, number: int) -> None:
        repo = self._repo_path()
        self._request("PATCH", f"/repos/{repo}/issues/{number}", {"state": "closed"})

    def open_intake_issues(self) -> list[Issue]:
        repo = self._repo_path()
        rows = self._request(
            "GET",
            f"/repos/{repo}/issues?labels=intake&state=open&per_page=20",
        )
        issues = []
        if not isinstance(rows, list):
            return issues
        for row in rows:
            if row.get("pull_request"):
                continue
            issues.append(
                Issue(
                    number=int(row["number"]),
                    title=row.get("title") or "",
                    body=row.get("body") or "",
                    state=row.get("state") or "open",
                )
            )
        return issues


def github_from_env() -> UrllibGitHub | None:
    token = os.environ.get("INTAKE_GITHUB_TOKEN", "").strip()
    repo = os.environ.get("INTAKE_GITHUB_REPO", "").strip()
    if not token or not repo:
        return None
    return UrllibGitHub(repo=repo, token=token)
