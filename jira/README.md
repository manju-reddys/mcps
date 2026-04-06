# JIRA MCP Server

An MCP server for interacting with JIRA via the Atlassian REST API, built with [FastMCP](https://github.com/jlowin/fastmcp).

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `JIRA_BASE_URL` | **Yes** | Your Jira instance URL, e.g. `https://yourcompany.atlassian.net` |
| `JIRA_USER_ID` | **Yes** | Your Atlassian email address (used for Basic Auth) |
| `JIRA_API_TOKEN` | **Yes** | API token from https://id.atlassian.com/manage-profile/security/api-tokens |

## Setup

```bash
cd jira
cp .env.example .env   # edit with your values

# Install dependencies
uv sync

# Run the server (stdio transport for MCP)
uv run python server.py
```

## VS Code / Copilot MCP Config

Add to your `.vscode/mcp.json` or user MCP settings:

```json
{
  "servers": {
    "jira": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp/jira", "python", "server.py"],
      "env": {
        "JIRA_BASE_URL": "https://yourcompany.atlassian.net",
        "JIRA_USER_ID": "your-email@company.com",
        "JIRA_API_TOKEN": "your-token"
      }
    }
  }
}
```

## Available Tools

| Tool | Description |
|---|---|
| `search_issues` | Search issues using JQL |
| `get_issue` | Get full details of an issue |
| `create_issue` | Create a new issue |
| `update_issue` | Update fields on an existing issue |
| `add_comment` | Add a comment to an issue |
| `transition_issue` | Change issue status |
| `get_transitions` | List available transitions for an issue |
| `assign_issue` | Assign/unassign an issue |
| `list_projects` | List accessible projects |
| `get_project` | Get project details and issue types |
| `list_boards` | List agile boards |
| `list_sprints` | List sprints for a board |
| `get_sprint_issues` | List issues in a sprint |
| `search_users` | Search users by name/email |
| `link_issues` | Create a link between two issues |
| `add_worklog` | Log work against an issue |
