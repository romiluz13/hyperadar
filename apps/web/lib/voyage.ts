/**
 * Voyage AI REST API client for query-time embedding generation.
 *
 * The web app (Next.js/TypeScript) cannot run the voyageai Python SDK.
 * This module calls the Voyage REST API directly to embed the user's
 * search query into a 1024-dim vector for $vectorSearch.
 *
 * The API key is a MongoDB Atlas Voyage AI key (al- prefix) which routes
 * to https://ai.mongodb.com/v1 instead of api.voyageai.com.
 *
 * Returns null on any failure — the caller falls back to text-only search.
 */

const VOYAGE_API_URL = "https://ai.mongodb.com/v1/embeddings";
const VOYAGE_MODEL = "voyage-4-large";

export async function embedQuery(text: string): Promise<number[] | null> {
	const apiKey = process.env.VOYAGE_API_KEY;
	if (!apiKey || !text.trim()) return null;

	try {
		const response = await fetch(VOYAGE_API_URL, {
			method: "POST",
			headers: {
				"Content-Type": "application/json",
				Authorization: `Bearer ${apiKey}`,
			},
			body: JSON.stringify({
				model: VOYAGE_MODEL,
				input: [text],
				input_type: "query",
			}),
		});

		if (!response.ok) return null;

		const data: { data: Array<{ embedding: number[] }> } =
			await response.json();
		return data.data[0]?.embedding ?? null;
	} catch {
		return null;
	}
}
