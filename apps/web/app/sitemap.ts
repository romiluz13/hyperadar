import type { MetadataRoute } from "next";

export default function sitemap(): MetadataRoute.Sitemap {
  const baseUrl = process.env.NEXT_PUBLIC_APP_URL || "https://hyperadar.vercel.app";
  const now = new Date();

  return [
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
    ...["@github-radar", "@reddit-pulse", "@youtube-trends", "@hidden-gems", "@weekly-digest"].map(
      (handle) => ({
        url: `${baseUrl}/agent/${handle.replace("@", "")}`,
        lastModified: now,
        changeFrequency: "daily" as const,
        priority: 0.6,
      })
    ),
  ];
}
