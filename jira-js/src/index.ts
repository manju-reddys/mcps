#!/usr/bin/env node
/**
 * JIRA MCP Server — TypeScript implementation using @modelcontextprotocol/sdk
 *
 * Environment variables:
 *   JIRA_BASE_URL   – e.g. https://mycompany.atlassian.net
 *   JIRA_USER_ID    – your Atlassian account email
 *   JIRA_API_TOKEN  – API token from id.atlassian.com
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

function cfg(key: string): string {
  const v = process.env[key] ?? "";
  return v.replace(/\/$/, "");
}

const BASE_URL = cfg("JIRA_BASE_URL");
const USER_ID = process.env["JIRA_USER_ID"] ?? "";
const API_TOKEN = process.env["JIRA_API_TOKEN"] ?? "";

// ---------------------------------------------------------------------------
// HTTP helpers
// ---------------------------------------------------------------------------

function authHeader(): string {
  if (!BASE_URL) throw new Error("JIRA_BASE_URL is not set");
  if (!USER_ID || !API_TOKEN)
    throw new Error("JIRA_USER_ID and JIRA_API_TOKEN must be set");
  return "Basic " + Buffer.from(`${USER_ID}:${API_TOKEN}`).toString("base64");
}

const JSON_HEADERS = {
  Accept: "application/json",
  "Content-Type": "application/json",
};

async function jiraFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const url = BASE_URL + path;
  const resp = await fetch(url, {
    ...options,
    headers: {
      ...JSON_HEADERS,
      Authorization: authHeader(),
      ...(options.headers ?? {}),
    },
  });

  if (!resp.ok) {
    let detail: unknown;
    try {
      detail = await resp.json();
    } catch {
      detail = await resp.text();
    }
    throw new Error(`JIRA API error ${resp.status}: ${JSON.stringify(detail)}`);
  }

  if (resp.status === 204) return { status: "ok" } as T;
  return resp.json() as Promise<T>;
}

function get<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  const url = params
    ? path + "?" + new URLSearchParams(
        Object.fromEntries(
          Object.entries(params).map(([k, v]) => [k, String(v)])
        )
      ).toString()
    : path;
  return jiraFetch<T>(url);
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return jiraFetch<T>(path, {
    method: "POST",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

function put<T>(path: string, body?: unknown): Promise<T> {
  return jiraFetch<T>(path, {
    method: "PUT",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

// ---------------------------------------------------------------------------
// ADF helpers
// ---------------------------------------------------------------------------

type AdfNode = {
  type: string;
  text?: string;
  content?: AdfNode[];
};

function extractText(doc: unknown): string {
  if (doc == null) return "";
  if (typeof doc === "string") return doc;
  const node = doc as AdfNode;
  if (node.type === "text") return node.text ?? "";
  if (Array.isArray(node.content)) {
    return node.content.map(extractText).filter(Boolean).join("\n");
  }
  if (Array.isArray(doc)) {
    return (doc as AdfNode[]).map(extractText).filter(Boolean).join("\n");
  }
  return "";
}

function textToAdf(text: string) {
  return {
    type: "doc",
    version: 1,
    content: [
      {
        type: "paragraph",
        content: [{ type: "text", text }],
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Issue formatting
// ---------------------------------------------------------------------------

type JiraIssue = {
  key: string;
  fields: Record<string, unknown>;
};

function formatIssue(issue: JiraIssue) {
  const f = issue.fields;
  const get = <T>(k: string) => f[k] as T | undefined;

  return {
    key: issue.key,
    summary: get<string>("summary"),
    status: (get<{ name: string }>("status") ?? {}).name,
    priority: (get<{ name: string }>("priority") ?? {}).name,
    assignee: (get<{ displayName: string }>("assignee") ?? {}).displayName,
    reporter: (get<{ displayName: string }>("reporter") ?? {}).displayName,
    issue_type: (get<{ name: string }>("issuetype") ?? {}).name,
    created: get<string>("created"),
    updated: get<string>("updated"),
    labels: (get<string[]>("labels") ?? []),
    description: extractText(get("description")),
    url: `${BASE_URL}/browse/${issue.key}`,
  };
}

// ---------------------------------------------------------------------------
// MCP Server setup
// ---------------------------------------------------------------------------

const server = new McpServer({
  name: "jira",
  version: "1.0.0",
});

// ---------------------------------------------------------------------------
// Tool: search_issues
// ---------------------------------------------------------------------------

server.tool(
  "search_issues",
  "Search JIRA issues using JQL (Jira Query Language).",
  {
    jql: z.string().describe('JQL query string, e.g. \'project = MYPROJ AND status = "In Progress"\''),
    max_results: z.number().int().min(1).max(100).default(20).describe("Maximum results (default 20, max 100)"),
    fields: z
      .string()
      .default("summary,status,priority,assignee,reporter,issuetype,created,updated,labels,description")
      .describe("Comma-separated list of fields to return"),
  },
  async ({ jql, max_results, fields }) => {
    const data = await get<{ issues: JiraIssue[] }>("/rest/api/3/search/jql", {
      jql,
      maxResults: Math.min(max_results, 100),
      fields,
    });
    const issues = data.issues.map(formatIssue);
    return { content: [{ type: "text", text: JSON.stringify(issues, null, 2) }] };
  }
);

// ---------------------------------------------------------------------------
// Tool: get_issue
// ---------------------------------------------------------------------------

server.tool(
  "get_issue",
  "Get full details of a JIRA issue by its key (e.g. PROJ-123).",
  {
    issue_key: z.string().describe('The issue key, e.g. "PROJ-123"'),
  },
  async ({ issue_key }) => {
    const issue = await get<JiraIssue>(`/rest/api/3/issue/${issue_key}`);
    const result: Record<string, unknown> = formatIssue(issue);

    const commentsRaw = (
      (issue.fields["comment"] as { comments?: unknown[] }) ?? {}
    ).comments ?? [];
    result["comments"] = commentsRaw.map((c) => {
      const cm = c as Record<string, unknown>;
      return {
        author: ((cm["author"] as { displayName?: string }) ?? {}).displayName,
        body: extractText(cm["body"]),
        created: cm["created"],
      };
    });

    const subtasks = (issue.fields["subtasks"] as JiraIssue[]) ?? [];
    result["subtasks"] = subtasks.map((s) => ({
      key: s.key,
      summary: s.fields["summary"],
      status: ((s.fields["status"] as { name?: string }) ?? {}).name,
    }));

    return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
  }
);

// ---------------------------------------------------------------------------
// Tool: create_issue
// ---------------------------------------------------------------------------

server.tool(
  "create_issue",
  "Create a new JIRA issue.",
  {
    project_key: z.string().describe('Project key, e.g. "PROJ"'),
    summary: z.string().describe("Issue summary / title"),
    issue_type: z.string().default("Task").describe("Issue type: Task, Bug, Story, Epic, Sub-task, etc."),
    description: z.string().default("").describe("Plain-text description"),
    assignee_id: z.string().default("").describe("Atlassian account ID of the assignee (optional)"),
    priority: z.string().default("").describe("Priority name: Highest, High, Medium, Low, Lowest (optional)"),
    labels: z.array(z.string()).optional().describe("List of labels (optional)"),
    parent_key: z.string().default("").describe("Parent issue key for sub-tasks (optional)"),
  },
  async ({ project_key, summary, issue_type, description, assignee_id, priority, labels, parent_key }) => {
    const fields: Record<string, unknown> = {
      project: { key: project_key },
      summary,
      issuetype: { name: issue_type },
    };
    if (description) fields["description"] = textToAdf(description);
    if (assignee_id) fields["assignee"] = { accountId: assignee_id };
    if (priority) fields["priority"] = { name: priority };
    if (labels?.length) fields["labels"] = labels;
    if (parent_key) fields["parent"] = { key: parent_key };

    const result = await post<{ key: string; id: string }>("/rest/api/3/issue", { fields });
    const out = { key: result.key, id: result.id, url: `${BASE_URL}/browse/${result.key}` };
    return { content: [{ type: "text", text: JSON.stringify(out, null, 2) }] };
  }
);

// ---------------------------------------------------------------------------
// Tool: update_issue
// ---------------------------------------------------------------------------

server.tool(
  "update_issue",
  "Update fields on an existing JIRA issue.",
  {
    issue_key: z.string().describe('The issue key, e.g. "PROJ-123"'),
    summary: z.string().optional().describe("New summary (optional)"),
    description: z.string().optional().describe("New plain-text description (optional)"),
    assignee_id: z.string().optional().describe("New assignee account ID (optional)"),
    priority: z.string().optional().describe("New priority name (optional)"),
    labels: z.array(z.string()).optional().describe("Replace labels with this list (optional)"),
  },
  async ({ issue_key, summary, description, assignee_id, priority, labels }) => {
    const fields: Record<string, unknown> = {};
    if (summary) fields["summary"] = summary;
    if (description) fields["description"] = textToAdf(description);
    if (assignee_id) fields["assignee"] = { accountId: assignee_id };
    if (priority) fields["priority"] = { name: priority };
    if (labels !== undefined) fields["labels"] = labels;

    if (Object.keys(fields).length === 0)
      throw new Error("At least one field must be specified to update");

    await put(`/rest/api/3/issue/${issue_key}`, { fields });
    return { content: [{ type: "text", text: JSON.stringify({ status: "updated", key: issue_key }) }] };
  }
);

// ---------------------------------------------------------------------------
// Tool: add_comment
// ---------------------------------------------------------------------------

server.tool(
  "add_comment",
  "Add a comment to a JIRA issue.",
  {
    issue_key: z.string().describe('The issue key, e.g. "PROJ-123"'),
    body: z.string().min(1).describe("Plain-text comment body"),
  },
  async ({ issue_key, body }) => {
    const result = await post<Record<string, unknown>>(
      `/rest/api/3/issue/${issue_key}/comment`,
      { body: textToAdf(body) }
    );
    const out = {
      id: result["id"],
      author: ((result["author"] as { displayName?: string }) ?? {}).displayName,
      created: result["created"],
    };
    return { content: [{ type: "text", text: JSON.stringify(out, null, 2) }] };
  }
);

// ---------------------------------------------------------------------------
// Tool: transition_issue
// ---------------------------------------------------------------------------

server.tool(
  "transition_issue",
  "Change the status of a JIRA issue by performing a named transition.",
  {
    issue_key: z.string().describe('The issue key, e.g. "PROJ-123"'),
    transition_name: z.string().describe('Transition name, e.g. "In Progress", "Done"'),
  },
  async ({ issue_key, transition_name }) => {
    const data = await get<{ transitions: Array<{ id: string; name: string; to: { name: string } }> }>(
      `/rest/api/3/issue/${issue_key}/transitions`
    );
    const transitions = data.transitions;
    const match = transitions.find(
      (t) => t.name.toLowerCase() === transition_name.toLowerCase()
    );
    if (!match) {
      const available = transitions.map((t) => t.name);
      throw new Error(
        `Transition '${transition_name}' not found. Available: ${available.join(", ")}`
      );
    }
    await post(`/rest/api/3/issue/${issue_key}/transitions`, {
      transition: { id: match.id },
    });
    const out = { status: "transitioned", key: issue_key, to: match.to.name };
    return { content: [{ type: "text", text: JSON.stringify(out, null, 2) }] };
  }
);

// ---------------------------------------------------------------------------
// Tool: get_transitions
// ---------------------------------------------------------------------------

server.tool(
  "get_transitions",
  "List available transitions (status changes) for a JIRA issue.",
  {
    issue_key: z.string().describe('The issue key, e.g. "PROJ-123"'),
  },
  async ({ issue_key }) => {
    const data = await get<{ transitions: Array<{ id: string; name: string; to: { name: string } }> }>(
      `/rest/api/3/issue/${issue_key}/transitions`
    );
    const out = data.transitions.map((t) => ({ id: t.id, name: t.name, to: t.to.name }));
    return { content: [{ type: "text", text: JSON.stringify(out, null, 2) }] };
  }
);

// ---------------------------------------------------------------------------
// Tool: assign_issue
// ---------------------------------------------------------------------------

server.tool(
  "assign_issue",
  'Assign a JIRA issue to a user. Use "-1" for automatic assignment or "none" to unassign.',
  {
    issue_key: z.string().describe('The issue key, e.g. "PROJ-123"'),
    assignee_id: z.string().describe('Atlassian account ID, "-1" for automatic, "none" to unassign'),
  },
  async ({ issue_key, assignee_id }) => {
    const accountId = assignee_id.toLowerCase() === "none" ? null : assignee_id;
    await put(`/rest/api/3/issue/${issue_key}/assignee`, { accountId });
    const out = { status: "assigned", key: issue_key, assignee: assignee_id };
    return { content: [{ type: "text", text: JSON.stringify(out, null, 2) }] };
  }
);

// ---------------------------------------------------------------------------
// Tool: list_projects
// ---------------------------------------------------------------------------

server.tool(
  "list_projects",
  "List JIRA projects accessible to the authenticated user.",
  {
    max_results: z.number().int().min(1).max(100).default(50).describe("Maximum projects to return"),
  },
  async ({ max_results }) => {
    const data = await get<{
      values: Array<{
        key: string;
        name: string;
        projectTypeKey?: string;
        lead?: { displayName?: string };
      }>;
    }>("/rest/api/3/project/search", { maxResults: Math.min(max_results, 100) });

    const out = data.values.map((p) => ({
      key: p.key,
      name: p.name,
      project_type: p.projectTypeKey,
      lead: p.lead?.displayName,
      url: `${BASE_URL}/browse/${p.key}`,
    }));
    return { content: [{ type: "text", text: JSON.stringify(out, null, 2) }] };
  }
);

// ---------------------------------------------------------------------------
// Tool: get_project
// ---------------------------------------------------------------------------

server.tool(
  "get_project",
  "Get details of a specific JIRA project.",
  {
    project_key: z.string().describe('The project key, e.g. "PROJ"'),
  },
  async ({ project_key }) => {
    const p = await get<{
      key: string;
      name: string;
      description?: unknown;
      projectTypeKey?: string;
      lead?: { displayName?: string };
      issueTypes?: Array<{ name: string; subtask?: boolean }>;
    }>(`/rest/api/3/project/${project_key}`);

    const out = {
      key: p.key,
      name: p.name,
      description: extractText(p.description),
      project_type: p.projectTypeKey,
      lead: p.lead?.displayName,
      url: `${BASE_URL}/browse/${p.key}`,
      issue_types: (p.issueTypes ?? []).map((it) => ({
        name: it.name,
        subtask: it.subtask ?? false,
      })),
    };
    return { content: [{ type: "text", text: JSON.stringify(out, null, 2) }] };
  }
);

// ---------------------------------------------------------------------------
// Tool: list_boards
// ---------------------------------------------------------------------------

server.tool(
  "list_boards",
  "List agile boards, optionally filtered by project.",
  {
    project_key: z.string().default("").describe("Filter boards by project key (optional)"),
    max_results: z.number().int().min(1).max(100).default(50).describe("Maximum boards to return"),
  },
  async ({ project_key, max_results }) => {
    const params: Record<string, string | number> = {
      maxResults: Math.min(max_results, 100),
    };
    if (project_key) params["projectKeyOrId"] = project_key;

    const data = await get<{
      values: Array<{
        id: number;
        name: string;
        type?: string;
        location?: { projectKey?: string };
      }>;
    }>("/rest/agile/1.0/board", params);

    const out = data.values.map((b) => ({
      id: b.id,
      name: b.name,
      type: b.type,
      project_key: b.location?.projectKey,
    }));
    return { content: [{ type: "text", text: JSON.stringify(out, null, 2) }] };
  }
);

// ---------------------------------------------------------------------------
// Tool: list_sprints
// ---------------------------------------------------------------------------

server.tool(
  "list_sprints",
  "List sprints for an agile board.",
  {
    board_id: z.number().int().describe("The ID of the JIRA board"),
    state: z
      .string()
      .default("active")
      .describe('Sprint state: "active", "future", "closed", or comma-separated combo'),
  },
  async ({ board_id, state }) => {
    const data = await get<{
      values: Array<{
        id: number;
        name: string;
        state: string;
        startDate?: string;
        endDate?: string;
        goal?: string;
      }>;
    }>(`/rest/agile/1.0/board/${board_id}/sprint`, { state, maxResults: 50 });

    const out = data.values.map((s) => ({
      id: s.id,
      name: s.name,
      state: s.state,
      start_date: s.startDate,
      end_date: s.endDate,
      goal: s.goal,
    }));
    return { content: [{ type: "text", text: JSON.stringify(out, null, 2) }] };
  }
);

// ---------------------------------------------------------------------------
// Tool: get_sprint_issues
// ---------------------------------------------------------------------------

server.tool(
  "get_sprint_issues",
  "List issues in a specific sprint.",
  {
    sprint_id: z.number().int().describe("The sprint ID"),
    max_results: z.number().int().min(1).max(100).default(50).describe("Maximum issues to return"),
  },
  async ({ sprint_id, max_results }) => {
    const data = await get<{ issues: JiraIssue[] }>(
      `/rest/agile/1.0/sprint/${sprint_id}/issue`,
      { maxResults: Math.min(max_results, 100) }
    );
    const out = data.issues.map(formatIssue);
    return { content: [{ type: "text", text: JSON.stringify(out, null, 2) }] };
  }
);

// ---------------------------------------------------------------------------
// Tool: search_users
// ---------------------------------------------------------------------------

server.tool(
  "search_users",
  "Search for JIRA users by name or email.",
  {
    query: z.string().describe("Search string (name, email, or username)"),
    max_results: z.number().int().min(1).max(50).default(10).describe("Maximum results"),
  },
  async ({ query, max_results }) => {
    const users = await get<
      Array<{
        accountId: string;
        displayName?: string;
        emailAddress?: string;
        active?: boolean;
      }>
    >("/rest/api/3/user/search", { query, maxResults: Math.min(max_results, 50) });

    const out = users.map((u) => ({
      account_id: u.accountId,
      display_name: u.displayName,
      email: u.emailAddress,
      active: u.active,
    }));
    return { content: [{ type: "text", text: JSON.stringify(out, null, 2) }] };
  }
);

// ---------------------------------------------------------------------------
// Tool: link_issues
// ---------------------------------------------------------------------------

server.tool(
  "link_issues",
  'Create a link between two JIRA issues (e.g. "Blocks", "Relates", "Duplicate").',
  {
    inward_issue_key: z.string().describe('Inward issue key, e.g. "PROJ-1"'),
    outward_issue_key: z.string().describe('Outward issue key, e.g. "PROJ-2"'),
    link_type: z
      .string()
      .default("Relates")
      .describe('Link type name: "Relates", "Blocks", "Cloners", "Duplicate", etc.'),
  },
  async ({ inward_issue_key, outward_issue_key, link_type }) => {
    await post("/rest/api/3/issueLink", {
      type: { name: link_type },
      inwardIssue: { key: inward_issue_key },
      outwardIssue: { key: outward_issue_key },
    });
    const out = {
      status: "linked",
      inward: inward_issue_key,
      outward: outward_issue_key,
      type: link_type,
    };
    return { content: [{ type: "text", text: JSON.stringify(out, null, 2) }] };
  }
);

// ---------------------------------------------------------------------------
// Tool: add_worklog
// ---------------------------------------------------------------------------

server.tool(
  "add_worklog",
  "Log work against a JIRA issue.",
  {
    issue_key: z.string().describe('The issue key, e.g. "PROJ-123"'),
    time_spent: z.string().describe('Time in JIRA format, e.g. "2h 30m", "1d", "30m"'),
    comment: z.string().default("").describe("Optional comment for the worklog"),
  },
  async ({ issue_key, time_spent, comment }) => {
    const payload: Record<string, unknown> = { timeSpent: time_spent };
    if (comment) payload["comment"] = textToAdf(comment);

    const result = await post<Record<string, unknown>>(
      `/rest/api/3/issue/${issue_key}/worklog`,
      payload
    );
    const out = {
      id: result["id"],
      time_spent: result["timeSpent"],
      author: ((result["author"] as { displayName?: string }) ?? {}).displayName,
      created: result["created"],
    };
    return { content: [{ type: "text", text: JSON.stringify(out, null, 2) }] };
  }
);

// ---------------------------------------------------------------------------
// Start server
// ---------------------------------------------------------------------------

const transport = new StdioServerTransport();
await server.connect(transport);
