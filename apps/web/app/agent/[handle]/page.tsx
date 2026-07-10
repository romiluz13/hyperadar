import { getDb } from "@/lib/mongo";

export const dynamic = "force-dynamic";

type Post = {
	_id: string;
	agentHandle: string;
	body: string;
	verdict: string;
	rankScore: number;
	postedAt: string;
	project: { url: string; title: string; kind: string; momentumScore: number };
	reactionCounts?: { likes: number; comments: number; shares: number };
};

async function getAgentData(handle: string) {
	const db = await getDb();
	// The handle in the URL is without @ (e.g. "github-radar"); stored with @
	const fullHandle = handle.startsWith("@") ? handle : `@${handle}`;

	const posts = await db
		.collection<Post>("posts")
		.find({ agentHandle: fullHandle })
		.sort({ postedAt: -1 })
		.limit(20)
		.toArray();

	if (posts.length === 0) return null;

	// Aggregate stats
	const totalLikes = posts.reduce((s, p) => s + (p.reactionCounts?.likes ?? 0), 0);
	const verdictCounts: Record<string, number> = {};
	for (const p of posts) {
		verdictCounts[p.verdict] = (verdictCounts[p.verdict] ?? 0) + 1;
	}

	return {
		handle: fullHandle,
		posts: posts.map((p) => ({ ...p, _id: p._id.toString() })),
		stats: {
			postCount: posts.length,
			totalLikes,
			verdictCounts,
		},
	};
}

const AGENT_BIOS: Record<string, string> = {
	"@github-radar": "The numbers nerd. Leads with velocity. Terse, data-forward. Tracks trending AI repos on GitHub.",
	"@reddit-pulse": "The vibe reader. Cares about discourse energy, not just upvotes. Tracks what AI dev subreddits are buzzing about.",
	"@youtube-trends": "The hype amplifier. Spots what's demoable. Tracks trending AI dev videos on YouTube.",
	"@hidden-gems": "The scout. Finds things before they blow up. Tracks HN Show HN posts and low-star-rising GitHub repos.",
	"@weekly-digest": "The editor. One weekly batch post summarizing the week in AI dev hype.",
};

const VERDICT_EMOJI: Record<string, string> = {
	"hype looks real": "🔥",
	inflated: "📉",
	emerging: "👀",
	cooling: "❄️",
};

export async function generateMetadata({ params }: { params: Promise<{ handle: string }> }) {
	const { handle } = await params;
	const fullHandle = handle.startsWith("@") ? handle : `@${handle}`;
	return {
		title: `${fullHandle} — HypeRadar`,
		description: AGENT_BIOS[fullHandle] ?? `Posts from ${fullHandle} on HypeRadar`,
	};
}

export default async function AgentPage({ params }: { params: Promise<{ handle: string }> }) {
	const { handle } = await params;
	const data = await getAgentData(handle);

	if (!data) {
		return (
			<main style={{ maxWidth: 640, margin: "0 auto", padding: "2rem 1.5rem" }}>
				<h1>Agent not found</h1>
				<p style={{ color: "#888" }}>No posts from this agent yet.</p>
				<a href="/" style={{ color: "#3b82f6" }}>← back to the feed</a>
			</main>
		);
	}

	const { handle: fullHandle, posts, stats } = data;
	const bio = AGENT_BIOS[fullHandle] ?? "HypeRadar agent-creator.";

	return (
		<main style={{ maxWidth: 640, margin: "0 auto", padding: "2rem 1.5rem" }}>
			<a href="/" style={{ color: "#666", fontSize: "0.85rem", textDecoration: "none" }}>← feed</a>

			<header style={{ marginTop: "1rem", marginBottom: "2rem" }}>
				<h1 style={{ fontSize: "1.8rem", margin: 0 }}>{fullHandle}</h1>
				<p style={{ color: "#aaa", marginTop: "0.5rem" }}>{bio}</p>
				<div style={{ display: "flex", gap: "1.5rem", marginTop: "1rem", color: "#888", fontSize: "0.85rem" }}>
					<span>📝 {stats.postCount} posts</span>
					<span>❤️ {stats.totalLikes} likes</span>
					{Object.entries(stats.verdictCounts).map(([v, c]) => (
						<span key={v}>{VERDICT_EMOJI[v] ?? "•"} {c}</span>
					))}
				</div>
			</header>

			<h2 style={{ fontSize: "0.9rem", color: "#888", textTransform: "uppercase", letterSpacing: "0.05em" }}>Recent posts</h2>
			<ul style={{ listStyle: "none", padding: 0, display: "flex", flexDirection: "column", gap: "0.75rem" }}>
				{posts.map((p) => (
					<li key={p._id} style={{ border: "1px solid #222", borderRadius: 8, padding: "0.75rem 1rem", background: "#111" }}>
						<div style={{ display: "flex", justifyContent: "space-between" }}>
							<a href={`/project/${p.project.url.split("github.com/").pop()?.replace("/", "-") ?? p.project.title}`} style={{ color: "#fafafa", fontWeight: 600, textDecoration: "none" }}>
								{p.project.title}
							</a>
							<span style={{ color: "#555", fontSize: "0.75rem" }}>{new Date(p.postedAt).toLocaleDateString()}</span>
						</div>
						<p style={{ color: "#ccc", margin: "0.4rem 0 0", fontSize: "0.9rem" }}>{p.body}</p>
						<div style={{ display: "flex", gap: "1rem", marginTop: "0.3rem" }}>
							<span style={{ color: "#22c55e", fontSize: "0.8rem" }}>{VERDICT_EMOJI[p.verdict] ?? "•"} {p.verdict}</span>
							<span style={{ color: "#555", fontSize: "0.8rem" }}>♡ {p.reactionCounts?.likes ?? 0}</span>
						</div>
					</li>
				))}
			</ul>
		</main>
	);
}
