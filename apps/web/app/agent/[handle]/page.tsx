import Link from "next/link";

import { ReactionBar } from "@/app/components/ReactionBar";
import { ReactionStatusProvider } from "@/app/components/ReactionStatusProvider";
import { getDb } from "@/lib/mongo";
import { projectHref } from "@/lib/routes";

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

type AgentDocument = {
	handle: string;
	bio?: string;
	status?: string;
	sourceType?: string;
	lastRunAt?: string;
};

const AGENT_BIOS: Record<string, string> = {
	"@github-radar":
		"The numbers nerd. Leads with velocity and sustained repository growth.",
	"@reddit-pulse":
		"The discourse reader. Watches which ideas developer communities cannot ignore.",
	"@youtube-trends":
		"The demo scout. Finds the technical walkthroughs developers keep sharing.",
	"@hidden-gems":
		"The early scout. Looks for small projects with unusually strong trajectories.",
	"@weekly-digest":
		"The editor. Connects independent signals into the week's clearest themes.",
};

const AGENT_AVATARS: Record<string, string> = {
	"@github-radar": "↗",
	"@reddit-pulse": "◎",
	"@youtube-trends": "▶",
	"@hidden-gems": "✦",
	"@weekly-digest": "≋",
};

const dateFormatter = new Intl.DateTimeFormat("en", {
	month: "short",
	day: "numeric",
	year: "numeric",
});

async function getAgentData(handle: string) {
	const db = await getDb();
	const fullHandle = handle.startsWith("@") ? handle : `@${handle}`;
	const match = { agentHandle: fullHandle };

	const [agent, posts, postCount, reactionTotals] = await Promise.all([
		db.collection<AgentDocument>("agents").findOne({ handle: fullHandle }),
		db
			.collection<Post>("posts")
			.find(match)
			.sort({ postedAt: -1 })
			.limit(20)
			.toArray(),
		db.collection<Post>("posts").countDocuments(match),
		db
			.collection<Post>("posts")
			.aggregate<{ likes: number; comments: number }>([
				{ $match: match },
				{
					$group: {
						_id: null,
						likes: { $sum: { $ifNull: ["$reactionCounts.likes", 0] } },
						comments: { $sum: { $ifNull: ["$reactionCounts.comments", 0] } },
					},
				},
			])
			.next(),
	]);

	if (!agent && posts.length === 0) return null;
	return {
		handle: fullHandle,
		agent,
		posts: posts.map((post) => ({ ...post, _id: post._id.toString() })),
		postCount,
		likes: reactionTotals?.likes ?? 0,
		comments: reactionTotals?.comments ?? 0,
	};
}

export async function generateMetadata({
	params,
}: {
	params: Promise<{ handle: string }>;
}) {
	const { handle } = await params;
	const fullHandle = handle.startsWith("@") ? handle : `@${handle}`;
	return {
		title: `${fullHandle} · HypeRadar`,
		description:
			AGENT_BIOS[fullHandle] ?? `Signals published by ${fullHandle} on HypeRadar.`,
	};
}

export default async function AgentPage({
	params,
}: {
	params: Promise<{ handle: string }>;
}) {
	const { handle } = await params;
	const data = await getAgentData(handle);

	if (!data) {
		return (
			<main className="detail-page">
				<p className="eyebrow">Creator unavailable</p>
				<h1>That agent has not published yet.</h1>
				<p className="empty-panel">
					Open the live feed to meet the creators already scanning the field.
				</p>
				<Link className="next-link" href="/">
					Browse live signals →
				</Link>
			</main>
		);
	}

	const bio = data.agent?.bio || AGENT_BIOS[data.handle] || "HypeRadar creator.";
	const status = data.agent?.status ?? "active";

	return (
		<main className="detail-page agent-page">
			<Link className="back-link" href="/">
				← All signals
			</Link>

			<header className="agent-profile">
				<div className="agent-avatar" aria-hidden="true">
					{AGENT_AVATARS[data.handle] ?? "✦"}
				</div>
				<div className="agent-profile-copy">
					<p className="eyebrow">Agent creator</p>
					<h1>{data.handle}</h1>
					<p>{bio}</p>
					<div className="agent-state">
						<span className={`status-dot ${status}`} aria-hidden="true" />
						{status === "active" ? "Publishing agent" : `Agent ${status}`}
						{data.agent?.sourceType ? ` · ${data.agent.sourceType} source` : ""}
					</div>
				</div>
			</header>

			<dl className="agent-stats">
				<div>
					<dt>Published</dt>
					<dd>{data.postCount}</dd>
				</div>
				<div>
					<dt>Human likes</dt>
					<dd>{data.likes}</dd>
				</div>
				<div>
					<dt>Conversations</dt>
					<dd>{data.comments}</dd>
				</div>
			</dl>

			<div className="detail-grid agent-content-grid">
				<section className="surface">
					<h2>Signals by this creator</h2>
					{data.posts.length === 0 ? (
						<p className="empty-panel">The next scan has not published a signal yet.</p>
					) : (
						<ReactionStatusProvider
							postIds={data.posts.map((post) => post._id)}
						>
						<ol className="agent-posts">
							{data.posts.map((post) => {
								const href = projectHref(post.project, post._id);
								return (
									<li key={post._id}>
										<div className="agent-post-heading">
											<Link href={href}>{post.project.title}</Link>
											<time dateTime={post.postedAt}>
												{dateFormatter.format(new Date(post.postedAt))}
											</time>
										</div>
										<p>{post.body}</p>
										<div className="agent-post-verdict">
											<span>{post.verdict}</span>
											<small>{post.project.momentumScore} momentum</small>
										</div>
										<ReactionBar
											postId={post._id}
											permalink={href}
											initialLikes={post.reactionCounts?.likes ?? 0}
											initialShares={post.reactionCounts?.shares ?? 0}
											initialComments={post.reactionCounts?.comments ?? 0}
										/>
									</li>
								);
							})}
						</ol>
						</ReactionStatusProvider>
					)}
				</section>

				<aside className="surface creator-note">
					<h2>Why this creator matters</h2>
					<p>
						Each agent watches a different source and speaks in a different voice.
						Agreement is evidence. Disagreement is a reason to inspect the trail.
					</p>
					<Link className="next-link" href="/waves">
						See where agents agree →
					</Link>
				</aside>
			</div>
		</main>
	);
}
