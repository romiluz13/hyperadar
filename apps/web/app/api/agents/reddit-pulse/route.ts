/** Cron-triggered agent runner stubs.
 *
 * Vercel Cron hits these routes on schedule. In production, each route enqueues
 * a Python Sandbox task (Firecracker microVM) that runs the corresponding
 * agent's main.py. For local dev, these can shell out to `uv run python main.py`.
 *
 * The actual Python agents live in /integrations/<agent_name>/main.py.
 */

import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

// Map of agent route -> integration path
const AGENTS: Record<string, string> = {
  "github-radar": "integrations/github_radar",
  "reddit-pulse": "integrations/reddit_pulse",
  "youtube-trends": "integrations/youtube_trends",
  "hidden-gems": "integrations/hidden_gems",
  "weekly-digest": "integrations/weekly_digest",
};

export async function POST(req: Request) {
  try {
    const path = new URL(req.url).pathname;
    const agentName = path.split("/").pop() ?? "";
    const integrationPath = AGENTS[agentName];

    if (!integrationPath) {
      return NextResponse.json({ error: `unknown agent: ${agentName}` }, { status: 404 });
    }

    // In production: enqueue a Vercel Sandbox task to run the Python agent.
    // For now: return a structured response documenting the trigger.
    // Local dev: run manually with `cd integrations/<name> && uv run python main.py`
    return NextResponse.json({
      agent: agentName,
      integration: integrationPath,
      status: "triggered (production: enqueues Python Sandbox task)",
      localDev: `cd ${integrationPath} && uv run python main.py`,
    });
  } catch (err) {
    console.error("agent trigger error:", err);
    return NextResponse.json({ error: "internal error" }, { status: 500 });
  }
}

export async function GET(req: Request) {
  return POST(req);
}
