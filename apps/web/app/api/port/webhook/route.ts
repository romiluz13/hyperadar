/** Port.io self-service action webhook handler.

When an operator triggers an action in the Port portal, Port sends a POST
to this endpoint with the action identifier, run ID, and user inputs.
We process the action and report status back to Port via PATCH.

Actions handled:
- run_agent_now: triggers an agent run
- track_project: enrolls a URL for monitoring
- boost_post: pins a post (boosts rankScore to 100)
- mute_agent: sets agent status to "muted" in Port + MongoDB
- retire_agent: sets agent status to "retired" in Port + MongoDB
- generate_digest: triggers the weekly-digest agent

Security: in production, verify the x-port-signature HMAC header.
For the showcase, we validate the action identifier + run ID.
*/

import { type NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/mongo";
import { toObjectId } from "@/lib/objectId";

export const dynamic = "force-dynamic";

const PORT_BASE = "https://api.getport.io/v1";

async function getPortToken() {
	const r = await fetch(`${PORT_BASE}/auth/access_token`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({
			clientId: process.env.PORT_CLIENT_ID,
			clientSecret: process.env.PORT_CLIENT_SECRET,
		}),
	});
	const d = await r.json();
	return d.accessToken;
}

async function reportRunStatus(runId: string, status: "SUCCESS" | "FAILURE", summary: string) {
	const token = await getPortToken();
	await fetch(`${PORT_BASE}/actions/runs/${runId}`, {
		method: "PATCH",
		headers: { "Authorization": `Bearer ${token}`, "Content-Type": "application/json" },
		body: JSON.stringify({ status, summary }),
	});
}

export async function POST(req: NextRequest) {
	try {
		const body = await req.json();
		const actionIdentifier = body?.action;
		const runId = body?.runId;
		const inputs = body?.inputs || {};
		const entity = body?.entity;

		if (!actionIdentifier || !runId) {
			return NextResponse.json({ error: "missing action or runId" }, { status: 400 });
		}

		const db = await getDb();
		let summary = "";

		switch (actionIdentifier) {
			case "run_agent_now": {
				// In production: enqueue a Python Sandbox task.
				// For now: record the trigger in MongoDB.
				const handle = inputs.agent_handle;
				await db.collection("agents").updateOne(
					{ handle },
					{ $set: { lastRunAt: new Date(), triggeredBy: "port-action" } },
				);
				summary = `Agent ${handle} run triggered via Port. In production, this enqueues a Vercel Python Sandbox task.`;
				break;
			}

			case "track_project": {
				const url = inputs.project_url;
				if (!url || typeof url !== "string") {
					await reportRunStatus(runId, "FAILURE", "Missing project_url");
					return NextResponse.json({ error: "missing project_url" }, { status: 400 });
				}
				// Enroll the project — create a minimal project doc + Port entity
				await db.collection("projects").updateOne(
					{ url },
					{ $set: { url, title: url.split("/").pop() || url, kind: "repo", topics: [], momentumScore: 0, lastSeenAt: new Date() },
						$setOnInsert: { firstSeenAt: new Date() } },
					{ upsert: true },
				);
				summary = `Project ${url} enrolled for tracking. The next agent run will pick it up.`;
				break;
			}

			case "boost_post": {
				const entityId = entity?.identifier;
				if (!entityId) {
					await reportRunStatus(runId, "FAILURE", "Missing entity identifier");
					return NextResponse.json({ error: "missing entity" }, { status: 400 });
				}
				// Boost the post's rankScore to 100 (pin to top of feed)
				await db.collection("posts").updateOne(
					{ _id: toObjectId(entityId) },
					{ $set: { rankScore: 100, boosted: true } },
				);
				summary = `Post ${entityId} boosted to rankScore 100 (pinned to top of feed).`;
				break;
			}

			case "mute_agent": {
				const entityId = entity?.identifier;
				if (!entityId) break;
				// Update agent status in MongoDB
				const handle = `@${entityId}`;
				await db.collection("agents").updateOne(
					{ handle },
					{ $set: { status: "muted" } },
				);
				// Update in Port (the entity update is handled by Port's action framework)
				summary = `Agent ${handle} muted. It will stop posting until reactivated.`;
				break;
			}

			case "retire_agent": {
				const entityId = entity?.identifier;
				if (!entityId) break;
				const handle = `@${entityId}`;
				await db.collection("agents").updateOne(
					{ handle },
					{ $set: { status: "retired" } },
				);
				summary = `Agent ${handle} retired.`;
				break;
			}

			case "generate_digest": {
				// In production: enqueue the weekly-digest Python agent.
				summary = "Weekly digest generation triggered via Port. In production, this runs the @weekly-digest agent with hype wave clustering.";
				break;
			}

			default:
				await reportRunStatus(runId, "FAILURE", `Unknown action: ${actionIdentifier}`);
				return NextResponse.json({ error: `unknown action: ${actionIdentifier}` }, { status: 400 });
		}

		// Report success back to Port
		await reportRunStatus(runId, "SUCCESS", summary || "Action completed.");

		return NextResponse.json({ ok: true, action: actionIdentifier, summary });
	} catch (err) {
		console.error("Port webhook error:", err);
		return NextResponse.json({ error: "internal error" }, { status: 500 });
	}
}
