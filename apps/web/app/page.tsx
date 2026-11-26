import { FeedPage } from "@/app/components/FeedPage";
import { recentFeedPosts } from "@/lib/feedPosts";

export const revalidate = 3600;

export default async function Home() {
	return <FeedPage posts={await recentFeedPosts()} />;
}
