import type { MetadataRoute } from "next";

import { AGENT_CATALOG } from "@/lib/agentCatalog";
import { getDb } from "@/lib/mongo";
import { PUBLIC_DIGEST_FILTER, PUBLIC_POST_FILTER } from "@/lib/publication";
import { projectHref } from "@/lib/routes";

export const dynamic = "force-dynamic";

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
	const baseUrl =
		process.env.NEXT_PUBLIC_APP_URL || "https://web-ebon-nu-43.vercel.app";
	const now = new Date();
	let discoveryRoutes: MetadataRoute.Sitemap = [];

	try {
		const db = await getDb();
		const [projects, digests] = await Promise.all([
			db
				.collection("posts")
				.aggregate<{ _id: string; lastSeenAt: Date }>([
					{ $match: PUBLIC_POST_FILTER },
					{
						$group: {
							_id: "$project.url",
							lastSeenAt: { $max: "$postedAt" },
						},
					},
					{ $sort: { lastSeenAt: -1 } },
				])
				.limit(500)
				.toArray(),
			db
				.collection<{ weekId: string; computedAt?: Date }>("digests")
				.find(PUBLIC_DIGEST_FILTER, {
					projection: { _id: 0, weekId: 1, computedAt: 1 },
				})
				.sort({ computedAt: -1 })
				.limit(52)
				.toArray(),
		]);
		discoveryRoutes = [
			...projects.map((project) => ({
				url: `${baseUrl}${projectHref({ url: project._id })}`,
				lastModified: project.lastSeenAt ?? now,
				changeFrequency: "daily" as const,
				priority: 0.7,
			})),
			...digests.map((digest) => ({
				url: `${baseUrl}/digest/${encodeURIComponent(digest.weekId)}`,
				lastModified: digest.computedAt ?? now,
				changeFrequency: "weekly" as const,
				priority: 0.7,
			})),
		];
	} catch (error) {
		console.error("Sitemap discovery routes unavailable", error);
	}

	const routes: MetadataRoute.Sitemap = [
		{
			url: baseUrl,
			lastModified: now,
			changeFrequency: "daily",
			priority: 1,
		},
		{
			url: `${baseUrl}/waves`,
			lastModified: now,
			changeFrequency: "weekly",
			priority: 0.8,
		},
		...AGENT_CATALOG.map((agent) => ({
			url: `${baseUrl}/agent/${agent.handle.replace("@", "")}`,
			lastModified: now,
			changeFrequency: "daily" as const,
			priority: 0.6,
		})),
		...discoveryRoutes,
	];
	return [...new Map(routes.map((route) => [route.url, route])).values()];
}
