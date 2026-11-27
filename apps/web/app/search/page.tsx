import { FeedPage } from "@/app/components/FeedPage";
import { recentFeedPosts, searchFeedPosts } from "@/lib/feedPosts";

export default async function SearchPage({
	searchParams,
}: {
	searchParams: Promise<{ q?: string }>;
}) {
	const { q: searchQuery } = await searchParams;
	const posts = searchQuery?.trim()
		? await searchFeedPosts(searchQuery.trim())
		: await recentFeedPosts();
	return <FeedPage posts={posts} searchQuery={searchQuery} />;
}
