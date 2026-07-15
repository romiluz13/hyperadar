import { createHmac } from "node:crypto";

import type { Db } from "mongodb";

type RateLimitDocument = {
	_id: string;
	count: number;
	createdAt: Date;
	expiresAt: Date;
};

export class MutationRateLimitError extends Error {
	constructor() {
		super("Too many social actions");
		this.name = "MutationRateLimitError";
	}
}

export function mutationRequestError(request: Request): string | null {
	const contentType = request.headers.get("content-type")?.toLowerCase() ?? "";
	if (!contentType.startsWith("application/json")) {
		return "Social actions require a JSON request";
	}

	const origin = request.headers.get("origin");
	if (!origin) return "Social actions require a same-origin request";
	try {
		const suppliedOrigin = new URL(origin).origin;
		const allowedOrigins = new Set([new URL(request.url).origin]);
		if (process.env.NEXT_PUBLIC_APP_URL) {
			allowedOrigins.add(new URL(process.env.NEXT_PUBLIC_APP_URL).origin);
		}
		if (!allowedOrigins.has(suppliedOrigin)) {
			return "Social actions require a same-origin request";
		}
	} catch {
		return "Social actions require a valid origin";
	}
	return null;
}

export function clientAddress(request: Request): string {
	if (process.env.VERCEL) {
		return request.headers.get("x-vercel-forwarded-for")?.trim() || "unknown-address";
	}
	const forwarded = request.headers
		.get("x-forwarded-for")
		?.split(",")
		.map((value) => value.trim())
		.filter(Boolean);
	return request.headers.get("x-real-ip")?.trim() || forwarded?.at(-1) || "unknown-address";
}

export function reactionRetryAfter(type: string): number {
	return type === "share" ? 600 : 60;
}

export function isValidOperationId(value: unknown): value is string {
	return (
		typeof value === "string" &&
		/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
			value,
		)
	);
}

export function mutationIdentityKey(value: string, secret: string) {
	return createHmac("sha256", secret).update(value).digest("hex").slice(0, 32);
}

function mutationIdentitySecret(): string {
	const secret =
		process.env.MUTATION_RATE_LIMIT_SECRET ?? process.env.MONGODB_URI;
	if (!secret) {
		throw new Error("MUTATION_RATE_LIMIT_SECRET is required for social actions");
	}
	return secret;
}

export function mutationNetworkIdentity(request: Request): string {
	return mutationIdentityKey(clientAddress(request), mutationIdentitySecret());
}

export async function consumeMutationRateLimit(
	db: Db,
	request: Request,
	userId: string,
	scope: string,
	limit: number,
	windowMs: number,
) {
	const now = new Date();
	const bucket = Math.floor(now.getTime() / windowMs);
	const identitySecret = mutationIdentitySecret();
	const networkIdentity = mutationNetworkIdentity(request);
	const identities = [
		`user:${mutationIdentityKey(userId, identitySecret)}`,
		`address:${networkIdentity}`,
	];
	for (const identity of identities) {
		const document = await db
			.collection<RateLimitDocument>("reaction_rate_limits")
			.findOneAndUpdate(
				{ _id: `${scope}:${bucket}:${identity}` },
				{
					$inc: { count: 1 },
					$setOnInsert: {
						createdAt: now,
						expiresAt: new Date(now.getTime() + windowMs * 2),
					},
				},
				{ upsert: true, returnDocument: "after" },
			);
		if ((document?.count ?? limit + 1) > limit) {
			throw new MutationRateLimitError();
		}
	}
	return networkIdentity;
}
