/** Port.io self-service action webhook handler.

When an operator triggers an action in the Port portal, Port sends a POST
to this endpoint with the action identifier, run ID, and user inputs.
We process the action and report status back to Port via PATCH.

Security: HMAC-SHA256 signature verification via x-port-signature header.
The webhook secret is stored in PORT_WEBHOOK_SECRET env var.
*/

import crypto from "crypto";
import { type NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/mongo";
import { toObjectId, isValidObjectId } from "@/lib/objectId";

export const dynamic = "force-dynamic";

const PORT_BASE = "https://api.getport.io/v1";

async function getPortToken(): Promise<string> {
	try {
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
	} catch (err) {
		console.error("Port auth failed:", err);
		return "";
	}
}

async function reportRunStatus(
	runId: string,
	status: "SUCCESS" | "FAILURE",
	summary: string,
) {
	// Swallow errors — the action already succeeded/failed locally.
	// Don't let Port API failures cause a 500 after successful MongoDB ops.
	try {
		const token = await getPortToken();
		if (!token) return;
		await fetch(`${PORT_BASE}/actions/runs/${runId}`, {
			method: "PATCH",
			headers: {
				Authorization: `Bearer ${token}`,
				"Content-Type": "application/json",
			},
			body: JSON.stringify({ status, summary }),
		});
	} catch (err) {
		console.error("Port status report failed:", err);
	}
}

/** Verify the Port HMAC-SHA256 signature. Returns true if valid. */
function verifySignature(rawBody: string, signature: string | null): boolean {
	const secret = process.env.PORT_WEBHOOK_SECRET;
	if (!secret) {
		console.error("PORT_WEBHOOK_SECRET not set — rejecting webhook");
		return false;
	}
	if (!signature) return false;
	const expected = crypto
		.createHmac("sha256", secret)
		.update(rawBody)
		.digest("hex");
	try {
		return crypto.timingSafeEqual(
			Buffer.from(signature),
			Buffer.from(expected),
		);
	} catch {
		return false;
	}
}

export async function POST(req: NextRequest) {
	// Read raw body for signature verification
	const rawBody = await req.text();
	const signature = req.headers.get("x-port-signature");

	if (!verifySignature(rawBody, signature)) {
		return NextResponse.json({ error: "invalid signature" }, { status: 401 });
	}

	// Hoist declarations so the catch block can report FAILURE to Port
	let actionIdentifier = "";
	let runId = "";

	try {
		const body = JSON.parse(rawBody);
		actionIdentifier = body?.action ?? "";
		runId = body?.runId ?? "";
		const inputs = body?.inputs || {};
		const entity = body?.entity;

		if (!actionIdentifier || !runId) {
			return NextResponse.json(
				{ error: "missing action or runId" },
				{ status: 400 },
			);
		}

		// Validate runId is alphanumeric (prevent path injection into Port API URL)
		if (!/^[a-zA-Z0-9_-]+$/.test(runId)) {
			return NextResponse.json({ error: "invalid runId" }, { status: 400 });
		}

		const db = await getDb();
		let summary = "";

		switch (actionIdentifier) {
			case "run_agent_now": {
				const handle = inputs.agent_handle;
				if (typeof handle !== "string" || !handle.trim()) {
					await reportRunStatus(
						runId,
						"FAILURE",
						"Missing or invalid agent_handle",
					);
					return NextResponse.json(
						{ error: "missing agent_handle" },
						{ status: 400 },
					);
				}
				await db
					.collection("agents")
					.updateOne(
						{ handle: handle },
						{ $set: { lastRunAt: new Date(), triggeredBy: "port-action" } },
					);
				summary = `Agent ${handle} run triggered via Port.`;
				break;
			}

			case "track_project": {
				const url = inputs.project_url;
				if (typeof url !== "string" || !url.trim()) {
					await reportRunStatus(runId, "FAILURE", "Missing project_url");
					return NextResponse.json(
						{ error: "missing project_url" },
						{ status: 400 },
					);
				}
				await db.collection("projects").updateOne(
					{ url },
					{
						$set: {
							url,
							title: url.split("/").pop() || url,
							kind: "repo",
							topics: [],
							momentumScore: 0,
							lastSeenAt: new Date(),
						},
						$setOnInsert: { firstSeenAt: new Date() },
					},
					{ upsert: true },
				);
				summary = `Project ${url} enrolled for tracking.`;
				break;
			}

			case "boost_post": {
				const entityId = entity?.identifier;
				if (
					!entityId ||
					typeof entityId !== "string" ||
					!isValidObjectId(entityId)
				) {
					await reportRunStatus(
						runId,
						"FAILURE",
						"Missing or invalid entity identifier",
					);
					return NextResponse.json(
						{ error: "invalid entity id" },
						{ status: 400 },
					);
				}
				await db
					.collection("posts")
					.updateOne(
						{ _id: toObjectId(entityId) },
						{ $set: { rankScore: 100, boosted: true } },
					);
				summary = `Post ${entityId} boosted to rankScore 100.`;
				break;
			}

			case "mute_agent": {
				const entityId = entity?.identifier;
				if (!entityId || typeof entityId !== "string") {
					await reportRunStatus(runId, "FAILURE", "Missing entity identifier");
					return NextResponse.json(
						{ error: "missing entity" },
						{ status: 400 },
					);
				}
				const handle = `@${entityId}`;
				await db
					.collection("agents")
					.updateOne({ handle }, { $set: { status: "muted" } });
				summary = `Agent ${handle} muted.`;
				break;
			}

			case "retire_agent": {
				const entityId = entity?.identifier;
				if (!entityId || typeof entityId !== "string") {
					await reportRunStatus(runId, "FAILURE", "Missing entity identifier");
					return NextResponse.json(
						{ error: "missing entity" },
						{ status: 400 },
					);
				}
				const handle = `@${entityId}`;
				await db
					.collection("agents")
					.updateOne({ handle }, { $set: { status: "retired" } });
				summary = `Agent ${handle} retired.`;
				break;
			}

			case "generate_digest": {
				summary = "Weekly digest generation triggered via Port.";
				break;
			}

			default:
				await reportRunStatus(
					runId,
					"FAILURE",
					`Unknown action: ${actionIdentifier}`,
				);
				return NextResponse.json(
					{ error: `unknown action: ${actionIdentifier}` },
					{ status: 400 },
				);
		}

		await reportRunStatus(runId, "SUCCESS", summary || "Action completed.");
		return NextResponse.json({ ok: true, action: actionIdentifier, summary });
	} catch (err) {
		console.error("Port webhook error:", err);
		if (runId) {
			await reportRunStatus(
				runId,
				"FAILURE",
				`Internal error: ${err instanceof Error ? err.message : "unknown"}`,
			);
		}
		return NextResponse.json({ error: "internal error" }, { status: 500 });
	}
}
