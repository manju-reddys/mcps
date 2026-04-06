"""JIRA MCP Server using FastMCP."""

from __future__ import annotations

import os
import sys
from typing import Any

import httpx
from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
JIRA_BASE_URL = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
JIRA_USER_ID = os.environ.get("JIRA_USER_ID", "")
JIRA_API_TOKEN = os.environ.get("JIRA_API_TOKEN", "")

mcp = FastMCP("JIRA")

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _client() -> httpx.Client:
    """Return a configured httpx client with basic-auth."""
    if not JIRA_BASE_URL:
        raise RuntimeError("JIRA_BASE_URL environment variable is not set")
    if not JIRA_USER_ID or not JIRA_API_TOKEN:
        raise RuntimeError(
            "JIRA_USER_ID and JIRA_API_TOKEN environment variables must be set"
        )
    return httpx.Client(
        base_url=JIRA_BASE_URL,
        auth=(JIRA_USER_ID, JIRA_API_TOKEN),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=30.0,
    )


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise RuntimeError(f"JIRA API error {resp.status_code}: {detail}")


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    with _client() as c:
        resp = c.get(path, params=params)
        _raise_for_status(resp)
        return resp.json()


def _post(path: str, json: dict[str, Any] | None = None) -> Any:
    with _client() as c:
        resp = c.post(path, json=json)
        _raise_for_status(resp)
        if resp.status_code == 204:
            return {"status": "ok"}
        return resp.json()


def _put(path: str, json: dict[str, Any] | None = None) -> Any:
    with _client() as c:
        resp = c.put(path, json=json)
        _raise_for_status(resp)
        if resp.status_code == 204:
            return {"status": "ok"}
        try:
            return resp.json()
        except Exception:
            return {"status": "ok"}


# ---------------------------------------------------------------------------
# Helpers to format JIRA responses
# ---------------------------------------------------------------------------

def _format_issue(issue: dict[str, Any]) -> dict[str, Any]:
    """Return a trimmed representation of a JIRA issue."""
    fields = issue.get("fields", {})
    return {
        "key": issue["key"],
        "summary": fields.get("summary"),
        "status": fields.get("status", {}).get("name"),
        "priority": (fields.get("priority") or {}).get("name"),
        "assignee": (fields.get("assignee") or {}).get("displayName"),
        "reporter": (fields.get("reporter") or {}).get("displayName"),
        "issue_type": (fields.get("issuetype") or {}).get("name"),
        "created": fields.get("created"),
        "updated": fields.get("updated"),
        "labels": fields.get("labels", []),
        "description": _extract_text(fields.get("description")),
        "url": f"{JIRA_BASE_URL}/browse/{issue['key']}",
    }


def _extract_text(doc: Any) -> str | None:
    """Recursively extract plain text from Atlassian Document Format (ADF)."""
    if doc is None:
        return None
    if isinstance(doc, str):
        return doc
    if isinstance(doc, dict):
        if doc.get("type") == "text":
            return doc.get("text", "")
        children = doc.get("content", [])
        parts = [_extract_text(child) for child in children]
        return "\n".join(p for p in parts if p)
    if isinstance(doc, list):
        parts = [_extract_text(item) for item in doc]
        return "\n".join(p for p in parts if p)
    return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def search_issues(
    jql: str,
    max_results: int = 20,
    fields: str = "summary,status,priority,assignee,reporter,issuetype,created,updated,labels,description",
) -> list[dict[str, Any]]:
    """Search JIRA issues using JQL (Jira Query Language).

    Args:
        jql: A JQL query string, e.g. 'project = MYPROJ AND status = "In Progress"'
        max_results: Maximum number of results to return (default 20, max 100).
        fields: Comma-separated list of fields to return.
    """
    data = _get(
        "/rest/api/3/search/jql",
        params={"jql": jql, "maxResults": min(max_results, 100), "fields": fields},
    )
    return [_format_issue(i) for i in data.get("issues", [])]


@mcp.tool()
def get_issue(issue_key: str) -> dict[str, Any]:
    """Get full details of a JIRA issue by its key (e.g. PROJ-123).

    Args:
        issue_key: The issue key, e.g. "PROJ-123".
    """
    issue = _get(f"/rest/api/3/issue/{issue_key}")
    result = _format_issue(issue)
    # Add comments
    comments_data = issue.get("fields", {}).get("comment", {}).get("comments", [])
    result["comments"] = [
        {
            "author": (c.get("author") or {}).get("displayName"),
            "body": _extract_text(c.get("body")),
            "created": c.get("created"),
        }
        for c in comments_data
    ]
    # Add subtasks
    subtasks = issue.get("fields", {}).get("subtasks", [])
    result["subtasks"] = [
        {
            "key": s["key"],
            "summary": s.get("fields", {}).get("summary"),
            "status": s.get("fields", {}).get("status", {}).get("name"),
        }
        for s in subtasks
    ]
    return result


@mcp.tool()
def create_issue(
    project_key: str,
    summary: str,
    issue_type: str = "Task",
    description: str = "",
    assignee_id: str = "",
    priority: str = "",
    labels: list[str] | None = None,
    parent_key: str = "",
) -> dict[str, Any]:
    """Create a new JIRA issue.

    Args:
        project_key: Project key (e.g. "PROJ").
        summary: Issue summary / title.
        issue_type: Issue type name – Task, Bug, Story, Epic, Sub-task, etc.
        description: Plain-text description (converted to ADF).
        assignee_id: Atlassian account ID of the assignee (optional).
        priority: Priority name – Highest, High, Medium, Low, Lowest (optional).
        labels: List of labels (optional).
        parent_key: Parent issue key for sub-tasks or children (optional).
    """
    if not summary:
        raise ValueError("summary is required")

    fields: dict[str, Any] = {
        "project": {"key": project_key},
        "summary": summary,
        "issuetype": {"name": issue_type},
    }
    if description:
        fields["description"] = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": description}],
                }
            ],
        }
    if assignee_id:
        fields["assignee"] = {"accountId": assignee_id}
    if priority:
        fields["priority"] = {"name": priority}
    if labels:
        fields["labels"] = labels
    if parent_key:
        fields["parent"] = {"key": parent_key}

    result = _post("/rest/api/3/issue", json={"fields": fields})
    return {
        "key": result["key"],
        "id": result["id"],
        "url": f"{JIRA_BASE_URL}/browse/{result['key']}",
    }


@mcp.tool()
def update_issue(
    issue_key: str,
    summary: str = "",
    description: str = "",
    assignee_id: str = "",
    priority: str = "",
    labels: list[str] | None = None,
) -> dict[str, str]:
    """Update fields on an existing JIRA issue.

    Args:
        issue_key: The issue key, e.g. "PROJ-123".
        summary: New summary (optional).
        description: New plain-text description (optional).
        assignee_id: New assignee account ID (optional).
        priority: New priority name (optional).
        labels: Replace labels with this list (optional).
    """
    fields: dict[str, Any] = {}
    if summary:
        fields["summary"] = summary
    if description:
        fields["description"] = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": description}],
                }
            ],
        }
    if assignee_id:
        fields["assignee"] = {"accountId": assignee_id}
    if priority:
        fields["priority"] = {"name": priority}
    if labels is not None:
        fields["labels"] = labels

    if not fields:
        raise ValueError("At least one field must be specified to update")

    _put(f"/rest/api/3/issue/{issue_key}", json={"fields": fields})
    return {"status": "updated", "key": issue_key}


@mcp.tool()
def add_comment(issue_key: str, body: str) -> dict[str, Any]:
    """Add a comment to a JIRA issue.

    Args:
        issue_key: The issue key, e.g. "PROJ-123".
        body: Plain-text comment body.
    """
    if not body:
        raise ValueError("body is required")
    payload = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": body}],
                }
            ],
        }
    }
    result = _post(f"/rest/api/3/issue/{issue_key}/comment", json=payload)
    return {
        "id": result.get("id"),
        "author": (result.get("author") or {}).get("displayName"),
        "created": result.get("created"),
    }


@mcp.tool()
def transition_issue(issue_key: str, transition_name: str) -> dict[str, str]:
    """Change the status of a JIRA issue by performing a transition.

    Args:
        issue_key: The issue key, e.g. "PROJ-123".
        transition_name: The name of the transition, e.g. "In Progress", "Done", "To Do".
    """
    # First, get available transitions
    data = _get(f"/rest/api/3/issue/{issue_key}/transitions")
    transitions = data.get("transitions", [])
    match = None
    for t in transitions:
        if t["name"].lower() == transition_name.lower():
            match = t
            break
    if not match:
        available = [t["name"] for t in transitions]
        raise ValueError(
            f"Transition '{transition_name}' not found. Available: {available}"
        )
    _post(
        f"/rest/api/3/issue/{issue_key}/transitions",
        json={"transition": {"id": match["id"]}},
    )
    return {"status": "transitioned", "key": issue_key, "to": match["to"]["name"]}


@mcp.tool()
def get_transitions(issue_key: str) -> list[dict[str, str]]:
    """List available transitions (status changes) for an issue.

    Args:
        issue_key: The issue key, e.g. "PROJ-123".
    """
    data = _get(f"/rest/api/3/issue/{issue_key}/transitions")
    return [
        {"id": t["id"], "name": t["name"], "to": t["to"]["name"]}
        for t in data.get("transitions", [])
    ]


@mcp.tool()
def assign_issue(issue_key: str, assignee_id: str) -> dict[str, str]:
    """Assign a JIRA issue to a user.

    Args:
        issue_key: The issue key, e.g. "PROJ-123".
        assignee_id: Atlassian account ID. Use "-1" for automatic, or "none" to unassign.
    """
    account_id: str | None = assignee_id
    if assignee_id.lower() == "none":
        account_id = None
    _put(f"/rest/api/3/issue/{issue_key}/assignee", json={"accountId": account_id})
    return {"status": "assigned", "key": issue_key, "assignee": assignee_id}


@mcp.tool()
def list_projects(max_results: int = 50) -> list[dict[str, Any]]:
    """List JIRA projects accessible to the authenticated user.

    Args:
        max_results: Maximum number of projects to return.
    """
    data = _get(
        "/rest/api/3/project/search",
        params={"maxResults": min(max_results, 100)},
    )
    return [
        {
            "key": p["key"],
            "name": p["name"],
            "project_type": p.get("projectTypeKey"),
            "lead": (p.get("lead") or {}).get("displayName"),
            "url": f"{JIRA_BASE_URL}/browse/{p['key']}",
        }
        for p in data.get("values", [])
    ]


@mcp.tool()
def get_project(project_key: str) -> dict[str, Any]:
    """Get details of a specific JIRA project.

    Args:
        project_key: The project key, e.g. "PROJ".
    """
    p = _get(f"/rest/api/3/project/{project_key}")
    return {
        "key": p["key"],
        "name": p["name"],
        "description": _extract_text(p.get("description")),
        "project_type": p.get("projectTypeKey"),
        "lead": (p.get("lead") or {}).get("displayName"),
        "url": f"{JIRA_BASE_URL}/browse/{p['key']}",
        "issue_types": [
            {"name": it["name"], "subtask": it.get("subtask", False)}
            for it in p.get("issueTypes", [])
        ],
    }


@mcp.tool()
def list_sprints(board_id: int, state: str = "active") -> list[dict[str, Any]]:
    """List sprints for an agile board.

    Args:
        board_id: The ID of the JIRA board.
        state: Sprint state filter – "active", "future", "closed", or comma-separated combo.
    """
    data = _get(
        f"/rest/agile/1.0/board/{board_id}/sprint",
        params={"state": state, "maxResults": 50},
    )
    return [
        {
            "id": s["id"],
            "name": s["name"],
            "state": s["state"],
            "start_date": s.get("startDate"),
            "end_date": s.get("endDate"),
            "goal": s.get("goal"),
        }
        for s in data.get("values", [])
    ]


@mcp.tool()
def get_sprint_issues(
    sprint_id: int,
    max_results: int = 50,
) -> list[dict[str, Any]]:
    """List issues in a specific sprint.

    Args:
        sprint_id: The sprint ID.
        max_results: Maximum number of issues to return.
    """
    data = _get(
        f"/rest/agile/1.0/sprint/{sprint_id}/issue",
        params={"maxResults": min(max_results, 100)},
    )
    return [_format_issue(i) for i in data.get("issues", [])]


@mcp.tool()
def list_boards(project_key: str = "", max_results: int = 50) -> list[dict[str, Any]]:
    """List agile boards, optionally filtered by project.

    Args:
        project_key: Filter boards by project key (optional).
        max_results: Maximum number of boards to return.
    """
    params: dict[str, Any] = {"maxResults": min(max_results, 100)}
    if project_key:
        params["projectKeyOrId"] = project_key
    data = _get("/rest/agile/1.0/board", params=params)
    return [
        {
            "id": b["id"],
            "name": b["name"],
            "type": b.get("type"),
            "project_key": b.get("location", {}).get("projectKey"),
        }
        for b in data.get("values", [])
    ]


@mcp.tool()
def search_users(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    """Search for JIRA users by name or email.

    Args:
        query: Search string (name, email, or username).
        max_results: Maximum number of results.
    """
    users = _get(
        "/rest/api/3/user/search",
        params={"query": query, "maxResults": min(max_results, 50)},
    )
    return [
        {
            "account_id": u["accountId"],
            "display_name": u.get("displayName"),
            "email": u.get("emailAddress"),
            "active": u.get("active"),
        }
        for u in users
    ]


@mcp.tool()
def link_issues(
    inward_issue_key: str,
    outward_issue_key: str,
    link_type: str = "Relates",
) -> dict[str, str]:
    """Create a link between two JIRA issues.

    Args:
        inward_issue_key: The inward issue key (e.g. "PROJ-1" is blocked by).
        outward_issue_key: The outward issue key (e.g. "PROJ-2" blocks).
        link_type: Link type name – "Relates", "Blocks", "Cloners", "Duplicate", etc.
    """
    _post(
        "/rest/api/3/issueLink",
        json={
            "type": {"name": link_type},
            "inwardIssue": {"key": inward_issue_key},
            "outwardIssue": {"key": outward_issue_key},
        },
    )
    return {
        "status": "linked",
        "inward": inward_issue_key,
        "outward": outward_issue_key,
        "type": link_type,
    }


@mcp.tool()
def add_worklog(
    issue_key: str,
    time_spent: str,
    comment: str = "",
) -> dict[str, Any]:
    """Log work against a JIRA issue.

    Args:
        issue_key: The issue key, e.g. "PROJ-123".
        time_spent: Time in JIRA format, e.g. "2h 30m", "1d", "30m".
        comment: Optional comment for the worklog.
    """
    payload: dict[str, Any] = {"timeSpent": time_spent}
    if comment:
        payload["comment"] = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": comment}],
                }
            ],
        }
    result = _post(f"/rest/api/3/issue/{issue_key}/worklog", json=payload)
    return {
        "id": result.get("id"),
        "time_spent": result.get("timeSpent"),
        "author": (result.get("author") or {}).get("displayName"),
        "created": result.get("created"),
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    mcp.run()


if __name__ == "__main__":
    main()
