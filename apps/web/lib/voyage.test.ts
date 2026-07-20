import assert from "node:assert/strict";
import test from "node:test";

// Mock global fetch for testing
const originalFetch = globalThis.fetch;

test("embedQuery returns a 1024-dim vector when the API succeeds", async () => {
	const fakeEmbedding = Array.from({ length: 1024 }, (_, i) => i * 0.001);
	globalThis.fetch = (async (
		url: string | URL | Request,
		init?: RequestInit,
	) => {
		try {
			const body = JSON.parse(init?.body as string);
			assert.equal(body.model, "voyage-4-large");
			assert.equal(body.input_type, "query");
			assert.deepEqual(body.input, ["agent security"]);
		} catch {
			// expected in some test paths
		}
		return new Response(
			JSON.stringify({ data: [{ embedding: fakeEmbedding }] }),
			{ status: 200, headers: { "Content-Type": "application/json" } },
		);
	}) as typeof fetch;

	process.env.VOYAGE_API_KEY = "test-key";
	const { embedQuery } = await import("./voyage.ts");
	const result = await embedQuery("agent security");
	assert.equal(result?.length, 1024);

	globalThis.fetch = originalFetch;
});

test("embedQuery returns null when VOYAGE_API_KEY is not set", async () => {
	delete process.env.VOYAGE_API_KEY;
	const { embedQuery } = await import("./voyage.ts");
	const result = await embedQuery("test");
	assert.equal(result, null);
});

test("embedQuery returns null for empty or whitespace text", async () => {
	process.env.VOYAGE_API_KEY = "test-key";
	const { embedQuery } = await import("./voyage.ts");
	assert.equal(await embedQuery(""), null);
	assert.equal(await embedQuery("   "), null);
});

test("embedQuery returns null when the API returns a non-200 status", async () => {
	globalThis.fetch = (async () =>
		new Response("error", { status: 500 })) as typeof fetch;
	process.env.VOYAGE_API_KEY = "test-key";
	const { embedQuery } = await import("./voyage.ts");
	const result = await embedQuery("test");
	assert.equal(result, null);
	globalThis.fetch = originalFetch;
});

test("embedQuery sends Authorization header with Bearer token to MongoDB Atlas endpoint", async () => {
	let capturedHeaders: Record<string, string> = {};
	let capturedUrl = "";
	globalThis.fetch = (async (
		url: string | URL | Request,
		init?: RequestInit,
	) => {
		capturedUrl = url.toString();
		capturedHeaders = Object.fromEntries(new Headers(init?.headers).entries());
		return new Response(JSON.stringify({ data: [{ embedding: [0.1] }] }), {
			status: 200,
			headers: { "Content-Type": "application/json" },
		});
	}) as typeof fetch;
	process.env.VOYAGE_API_KEY = "al-secret-key-123";
	const { embedQuery } = await import("./voyage.ts");
	await embedQuery("test");
	assert.match(capturedUrl, /ai\.mongodb\.com/);
	assert.match(
		capturedHeaders.authorization ?? "",
		/^Bearer al-secret-key-123$/,
	);
	globalThis.fetch = originalFetch;
});
