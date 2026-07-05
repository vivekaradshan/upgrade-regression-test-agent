import os
import time

import pytest

from src.tools.github_client import GitHubClient, GitHubAPIError

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
TEST_OWNER = "vivekaradshan"
TEST_REPO = "customer-transactions-pipeline"

pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    if not GITHUB_TOKEN:
        pytest.skip("GITHUB_TOKEN not set")

    gh = GitHubClient(token=GITHUB_TOKEN, owner=TEST_OWNER, repo=TEST_REPO)
    branch_name = f"test/github-client-{int(time.time())}"

    yield gh, branch_name

    try:
        gh.delete_branch(branch_name)
    except GitHubAPIError:
        pass
    gh.close()


def test_create_branch_write_file_read_back_delete(client):
    gh, branch_name = client

    main_sha = gh.get_default_branch_sha("main")
    assert main_sha

    created = gh.create_branch(branch_name, main_sha)
    assert created["ref"] == f"refs/heads/{branch_name}"

    original_content, sha = gh.get_file_content("README.md", branch_name)
    assert isinstance(original_content, str)
    assert sha

    new_content = original_content + "\n<!-- github_client integration test -->\n"
    update_result = gh.update_file(
        path="README.md",
        branch=branch_name,
        content=new_content,
        sha=sha,
        message="test: github_client integration test write",
    )
    assert update_result["commit"]["sha"]

    read_back_content, _ = gh.get_file_content("README.md", branch_name)
    assert "github_client integration test" in read_back_content


def test_get_default_branch_sha_for_nonexistent_branch_raises(client):
    gh, _ = client

    with pytest.raises(GitHubAPIError) as exc_info:
        gh.get_default_branch_sha("this-branch-does-not-exist")

    assert exc_info.value.status_code == 404
