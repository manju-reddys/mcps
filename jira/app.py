"""MCP plugin — jira.

Built as a pure-Python stdio MCP server for WASI/componentize compatibility.
Entry point is wasm_entry.py.
"""

import json
import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)

_jira_client = None


def get_jira_client():
    global _jira_client
    if _jira_client is None:
        from jira import JIRA

        base_url = os.getenv("JIRA_BASE_URL")
        username = os.getenv("JIRA_USERNAME")
        api_token = os.getenv("JIRA_API_TOKEN")

        if not all([base_url, username, api_token]):
            raise ValueError(
                "Missing JIRA credentials. Set JIRA_BASE_URL, JIRA_USERNAME, and JIRA_API_TOKEN"
            )

        _jira_client = JIRA(server=base_url, basic_auth=(username, api_token))
    return _jira_client


def get_issue(issue_key: str) -> str:
    jira = get_jira_client()
    issue = jira.issue(issue_key)
    result = {
        "key": issue.key,
        "summary": issue.fields.summary,
        "description": issue.fields.description or "No description",
        "status": issue.fields.status.name,
        "priority": issue.fields.priority.name if issue.fields.priority else "None",
        "assignee": issue.fields.assignee.displayName if issue.fields.assignee else "Unassigned",
        "reporter": issue.fields.reporter.displayName if issue.fields.reporter else "Unknown",
        "created": str(issue.fields.created),
        "updated": str(issue.fields.updated),
        "issue_type": issue.fields.issuetype.name,
        "labels": issue.fields.labels,
        "url": issue.permalink(),
    }
    return json.dumps(result, indent=2)


def search_issues(jql: str, max_results: int = 10) -> str:
    jira = get_jira_client()
    issues = jira.search_issues(jql, maxResults=max_results)
    results: list[dict[str, Any]] = []
    for issue in issues:
        results.append(
            {
                "key": issue.key,
                "summary": issue.fields.summary,
                "status": issue.fields.status.name,
                "assignee": issue.fields.assignee.displayName if issue.fields.assignee else "Unassigned",
                "priority": issue.fields.priority.name if issue.fields.priority else "None",
                "updated": str(issue.fields.updated),
            }
        )
    return json.dumps({"total": len(results), "issues": results}, indent=2)


def get_issue_details(issue_key: str) -> str:
    jira = get_jira_client()
    issue = jira.issue(issue_key, expand="changelog")

    comments: list[dict[str, Any]] = []
    if issue.fields.comment and issue.fields.comment.comments:
        for comment in issue.fields.comment.comments:
            comments.append(
                {
                    "author": comment.author.displayName,
                    "created": str(comment.created),
                    "body": comment.body,
                }
            )

    result = {
        "key": issue.key,
        "summary": issue.fields.summary,
        "description": issue.fields.description or "No description",
        "status": issue.fields.status.name,
        "priority": issue.fields.priority.name if issue.fields.priority else "None",
        "issue_type": issue.fields.issuetype.name,
        "assignee": issue.fields.assignee.displayName if issue.fields.assignee else "Unassigned",
        "reporter": issue.fields.reporter.displayName if issue.fields.reporter else "Unknown",
        "created": str(issue.fields.created),
        "updated": str(issue.fields.updated),
        "labels": issue.fields.labels,
        "components": [c.name for c in (issue.fields.components or [])],
        "due_date": str(issue.fields.duedate) if issue.fields.duedate else None,
        "fix_versions": [v.name for v in (issue.fields.fixVersions or [])],
        "affected_versions": [v.name for v in (issue.fields.versions or [])],
        "comments_count": len(comments),
        "comments": comments,
        "url": issue.permalink(),
    }
    return json.dumps(result, indent=2)


def list_projects() -> str:
    jira = get_jira_client()
    projects = jira.projects()
    results: list[dict[str, Any]] = []
    for project in projects:
        results.append(
            {
                "key": project.key,
                "name": project.name,
                "type": project.projectTypeKey,
                "lead": project.lead.displayName if hasattr(project, "lead") and project.lead else "Unknown",
            }
        )
    return json.dumps({"total": len(results), "projects": results}, indent=2)


def get_issue_comments(issue_key: str) -> str:
    jira = get_jira_client()
    issue = jira.issue(issue_key)

    comments: list[dict[str, Any]] = []
    if issue.fields.comment and issue.fields.comment.comments:
        for comment in issue.fields.comment.comments:
            comments.append(
                {
                    "id": comment.id,
                    "author": comment.author.displayName,
                    "author_email": comment.author.emailAddress if hasattr(comment.author, "emailAddress") else None,
                    "created": str(comment.created),
                    "updated": str(comment.updated),
                    "body": comment.body,
                }
            )

    return json.dumps({"issue_key": issue_key, "comments_count": len(comments), "comments": comments}, indent=2)


TOOLS: dict[str, Any] = {
    "get_issue": get_issue,
    "search_issues": search_issues,
    "get_issue_details": get_issue_details,
    "list_projects": list_projects,
    "get_issue_comments": get_issue_comments,
}


def _read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}

    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        if b":" in line:
            key, value = line.split(b":", 1)
            headers[key.decode("utf-8").strip().lower()] = value.decode("utf-8").strip()

    content_length = headers.get("content-length")
    if not content_length:
        return None

    body = sys.stdin.buffer.read(int(content_length))
    if not body:
        return None

    return json.loads(body.decode("utf-8"))


def _write_message(payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(encoded)}\r\n\r\n".encode("utf-8"))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def _ok(msg_id: Any, result: dict[str, Any]) -> None:
    _write_message({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _err(msg_id: Any, code: int, message: str) -> None:
    _write_message(
        {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
    )


def _handle_request(msg: dict[str, Any]) -> None:
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params", {})

    if method == "initialize":
        _ok(
            msg_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "jira", "version": "0.1.0"},
            },
        )
        return

    if method == "tools/list":
        _ok(
            msg_id,
            {
                "tools": [
                    {
                        "name": "get_issue",
                        "description": "Get a JIRA issue by key (e.g., PROJ-123)",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"issue_key": {"type": "string"}},
                            "required": ["issue_key"],
                        },
                    },
                    {
                        "name": "search_issues",
                        "description": "Search JIRA issues with JQL",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "jql": {"type": "string"},
                                "max_results": {"type": "integer", "default": 10},
                            },
                            "required": ["jql"],
                        },
                    },
                    {
                        "name": "get_issue_details",
                        "description": "Get detailed issue info including comments",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"issue_key": {"type": "string"}},
                            "required": ["issue_key"],
                        },
                    },
                    {
                        "name": "list_projects",
                        "description": "List accessible JIRA projects",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "get_issue_comments",
                        "description": "Get all comments for a JIRA issue",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"issue_key": {"type": "string"}},
                            "required": ["issue_key"],
                        },
                    },
                ]
            },
        )
        return

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})

        if name not in TOOLS:
            _err(msg_id, -32601, f"Unknown tool: {name}")
            return

        try:
            if name == "list_projects":
                output = TOOLS[name]()
            else:
                output = TOOLS[name](**arguments)
            _ok(msg_id, {"content": [{"type": "text", "text": output}]})
        except Exception as e:
            _ok(msg_id, {"content": [{"type": "text", "text": json.dumps({"error": str(e)})}]})
        return

    if msg_id is not None:
        _err(msg_id, -32601, f"Method not found: {method}")


class Run:
    """componentize-py entry point for mcp:plugin/run."""

    def run(self) -> None:
        logging.basicConfig(level=logging.INFO, format="%(name)s - %(levelname)s - %(message)s")
        while True:
            msg = _read_message()
            if msg is None:
                break
            if "id" in msg and "method" in msg:
                _handle_request(msg)


if __name__ == "__main__":
    Run().run()
