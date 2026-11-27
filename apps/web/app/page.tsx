import { unstable_cache } from "next/cache";

import { FeedPage } from "@/app/components/FeedPage";
import { recentFeedPosts } from "@/lib/feedPosts";

// Rendered per request from an hourly data cache, so a build never reads MongoDB:
// prerendering here failed every deploy without the database (Preview has none).
export const dynamic = "force-dynamic";

const cachedFeedPosts = unstable_cache(recentFeedPosts, ["recent-feed"], {
	revalidate: 3600,
});

export default async function Home() {
	return <FeedPage posts={await cachedFeedPosts()} />;
}
