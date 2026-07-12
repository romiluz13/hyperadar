import type { MetadataRoute } from "next";

import { getDb } from "@/lib/mongo";
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
				.collection<{ url: string; lastSeenAt?: Date }>("projects")
				.find({}, { projection: { _id: 0, url: 1, lastSeenAt: 1 } })
				.sort({ lastSeenAt: -1 })
				.limit(500)
				.toArray(),
			db
				.collection<{ weekId: string; computedAt?: Date }>("digests")
				.find({}, { projection: { _id: 0, weekId: 1, computedAt: 1 } })
				.sort({ computedAt: -1 })
				.limit(52)
				.toArray(),
		]);
		discoveryRoutes = [
			...projects.map((project) => ({
				url: `${baseUrl}${projectHref(project)}`,
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
		...[
			"@github-radar",
			"@reddit-pulse",
			"@youtube-trends",
			"@hidden-gems",
			"@weekly-digest",
		].map((handle) => ({
			url: `${baseUrl}/agent/${handle.replace("@", "")}`,
			lastModified: now,
			changeFrequency: "daily" as const,
			priority: 0.6,
		})),
		...discoveryRoutes,
	];
	return [...new Map(routes.map((route) => [route.url, route])).values()];
}
