"""Thin wrapper around the GitHub REST API for branch, file, and PR operations."""

from __future__ import annotations

import base64

import httpx
import structlog

logger = structlog.get_logger(__name__)

GITHUB_API_BASE = "https://api.github.com"


class GitHubAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"GitHub API error {status_code}: {message}")


class GitHubClient:
    def __init__(self, token: str, owner: str, repo: str):
        self.owner = owner
        self.repo = repo
        self.token = token
        self._client = httpx.Client(
            base_url=GITHUB_API_BASE,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        logger.info("github_api_request", method=method, path=path)
        response = self._client.request(method, path, **kwargs)
        if response.is_error:
            raise GitHubAPIError(response.status_code, response.text)
        return response

    def get_default_branch_sha(self, branch: str = "main") -> str:
        path = f"/repos/{self.owner}/{self.repo}/git/ref/heads/{branch}"
        response = self._request("GET", path)
        return response.json()["object"]["sha"]

    def create_branch(self, branch_name: str, from_sha: str) -> dict:
        path = f"/repos/{self.owner}/{self.repo}/git/refs"
        body = {"ref": f"refs/heads/{branch_name}", "sha": from_sha}
        response = self._request("POST", path, json=body)
        return response.json()

    def get_file_content(self, path: str, branch: str) -> tuple[str, str]:
        api_path = f"/repos/{self.owner}/{self.repo}/contents/{path}"
        response = self._request("GET", api_path, params={"ref": branch})
        data = response.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        return content, data["sha"]

    def update_file(
        self, path: str, branch: str, content: str, sha: str, message: str
    ) -> dict:
        api_path = f"/repos/{self.owner}/{self.repo}/contents/{path}"
        body = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "sha": sha,
            "branch": branch,
        }
        response = self._request("PUT", api_path, json=body)
        return response.json()

    def create_pull_request(
        self, title: str, body: str, head: str, base: str = "main"
    ) -> dict:
        path = f"/repos/{self.owner}/{self.repo}/pulls"
        payload = {"title": title, "body": body, "head": head, "base": base}
        response = self._request("POST", path, json=payload)
        return response.json()

    def delete_branch(self, branch_name: str) -> None:
        path = f"/repos/{self.owner}/{self.repo}/git/refs/heads/{branch_name}"
        self._request("DELETE", path)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
