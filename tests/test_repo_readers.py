"""Tests for configurable repository README discovery."""

import base64
import json
from pathlib import Path

from gtx_broker.repo_readers import RepositoryReadmeReader, RepositoryRegistry
from gtx_broker.tools.repo_tools import RepositoryTools


def _write_registry(path: Path, repositories: dict[str, str]) -> Path:
    path.write_text(json.dumps({"repositories": repositories}))
    return path


def test_registry_loads_all_configured_repositories(tmp_path):
    registry_path = _write_registry(
        tmp_path / "repositories.json",
        {
            "first": str(tmp_path / "first"),
            "second": str(tmp_path / "second"),
        },
    )

    registry = RepositoryRegistry.from_file(registry_path)

    assert registry.repositories == {
        "first": str(tmp_path / "first"),
        "second": str(tmp_path / "second"),
    }


def test_discovery_reports_successes_and_failures_for_all_configured_repositories(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "README.md").write_text("first README")
    (second / "README.md").write_text("second README")
    registry_path = _write_registry(
        tmp_path / "repositories.json",
        {
            "first": str(first),
            "second": str(second),
            "missing": str(tmp_path / "missing"),
        },
    )

    tools = RepositoryTools(registry_path=str(registry_path))
    result = tools.discover_readmes()

    assert result.success, result.error
    data = {item["repo"]: item for item in json.loads(result.content)}
    assert set(data) == {"first", "second", "missing"}
    assert data["first"]["status"] == "ok"
    assert data["second"]["status"] == "ok"
    assert data["missing"]["status"] == "error"

    reader = RepositoryReadmeReader(RepositoryRegistry.from_file(registry_path))
    summary = reader.get_summary()
    assert summary["total_repos"] == 3
    assert summary["successful"] == 2
    assert summary["failed"] == 1


def test_remote_https_repository_url_is_parsed(monkeypatch):
    requested = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"content": base64.b64encode(b"remote README").decode("ascii")}

    def fake_get(url, headers, timeout):
        requested.append((url, headers, timeout))
        return Response()

    monkeypatch.setattr("gtx_broker.repo_readers.requests.get", fake_get)
    registry = RepositoryRegistry(
        {"demo": "https://github.com/example/demo.git"}
    )

    readme = RepositoryReadmeReader(registry, github_token="x").get_readme("demo")

    assert readme is not None
    assert readme.error is None
    assert readme.content == "remote README"
    assert requested[0][0] == "https://api.github.com/repos/example/demo/readme"


def test_local_readme_symlink_escape_is_rejected(tmp_path):
    repository = tmp_path / "repository"
    outside = tmp_path / "outside.md"
    repository.mkdir()
    outside.write_text("outside secret")
    (repository / "README.md").symlink_to(outside)

    reader = RepositoryReadmeReader(RepositoryRegistry({"demo": str(repository)}))
    readme = reader.get_readme("demo")

    assert readme is not None
    assert readme.error
    assert "symlink" in readme.error
    assert readme.content == ""


def test_ssh_repository_url_is_read_as_remote(monkeypatch):
    requested = []

    class Response:
        status_code = 200

        def json(self):
            return {"content": base64.b64encode(b"ssh README").decode(), "path": "README.md"}

    def fake_get(url, headers, timeout):
        requested.append(url)
        return Response()

    monkeypatch.setattr("gtx_broker.repo_readers.requests.get", fake_get)
    registry = RepositoryRegistry({"demo": "git@github.com:example/demo.git"})

    readme = RepositoryReadmeReader(registry, github_token="x").get_readme("demo")

    assert readme is not None
    assert readme.error is None
    assert readme.content == "ssh README"
    assert requested == ["https://api.github.com/repos/example/demo/readme"]
