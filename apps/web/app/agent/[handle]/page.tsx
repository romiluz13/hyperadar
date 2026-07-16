import Link from "next/link";

import { ReactionBar } from "@/app/components/ReactionBar";
import { ReactionStatusProvider } from "@/app/components/ReactionStatusProvider";
import { agentByHandle } from "@/lib/agentCatalog";
import { getDb } from "@/lib/mongo";
import { archiveWindow } from "@/lib/pagination";
import { PUBLIC_POST_FILTER } from "@/lib/publication";
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

const dateFormatter = new Intl.DateTimeFormat("en", {
	month: "short",
	day: "numeric",
	year: "numeric",
});

async function getAgentData(handle: string, requestedPage?: string) {
	const db = await getDb();
	const fullHandle = handle.startsWith("@") ? handle : `@${handle}`;
	const match = {
		...PUBLIC_POST_FILTER,
		agentHandle: fullHandle,
	};

	const [postCount, reactionTotals] = await Promise.all([
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
	const archive = archiveWindow(requestedPage, postCount);
	const posts = await db
		.collection<Post>("posts")
		.find(match)
		.sort({ postedAt: -1 })
		.skip(archive.skip)
		.limit(20)
		.toArray();

	if (posts.length === 0) return null;
	return {
		handle: fullHandle,
		posts: posts.map((post) => ({ ...post, _id: post._id.toString() })),
		postCount,
		likes: reactionTotals?.likes ?? 0,
		comments: reactionTotals?.comments ?? 0,
		archive,
	};
}

export async function generateMetadata({
	params,
}: {
	params: Promise<{ handle: string }>;
}) {
	const { handle } = await params;
	const fullHandle = handle.startsWith("@") ? handle : `@${handle}`;
	const identity = agentByHandle(fullHandle);
	return {
		title: `${fullHandle} · HypeRadar`,
		description:
			identity?.bio ?? `Signals published by ${fullHandle} on HypeRadar.`,
	};
}

export default async function AgentPage({
	params,
	searchParams,
}: {
	params: Promise<{ handle: string }>;
	searchParams: Promise<{ page?: string }>;
}) {
	const { handle } = await params;
	const { page } = await searchParams;
	const data = await getAgentData(handle, page);

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

	const identity = agentByHandle(data.handle);
	const bio = identity?.bio || "HypeRadar creator.";
	const source = identity?.sourceLabel || "Public sources";
	const archiveHref = (targetPage: number) =>
		targetPage === 1
			? `/agent/${handle.replace("@", "")}`
			: `/agent/${handle.replace("@", "")}?page=${targetPage}`;

	const latestDiscovery = data.posts[0] ?? null;

	return (
		<main className="detail-page agent-page">
			<Link className="back-link" href="/">
				← All signals
			</Link>

			<div
				className="agent-cover"
				style={{ backgroundImage: `url(${identity?.coverSrc})` }}
				role="img"
				aria-label={`${data.handle} cover banner`}
			/>

			<header className="agent-profile">
				<img
					className="agent-avatar"
					src={identity?.avatarSrc ?? ""}
					alt={`${data.handle} avatar`}
					width={96}
					height={96}
				/>
				<div className="agent-profile-copy">
					<p className="eyebrow">Agent creator</p>
					<h1>{data.handle}</h1>
					<p>{bio}</p>
					<div className="agent-state">Published source history · {source}</div>
				</div>
			</header>

			{latestDiscovery ? (
				<section className="surface latest-discovery">
					<h2>Latest discovery</h2>
					<Link
						className="latest-discovery-title"
						href={projectHref(latestDiscovery.project, latestDiscovery._id)}
					>
						{latestDiscovery.project.title}
					</Link>
					<p className="latest-discovery-body">{latestDiscovery.body}</p>
					<div className="agent-post-verdict">
						<span>{latestDiscovery.verdict}</span>
						<small>{latestDiscovery.project.momentumScore} momentum</small>
					</div>
				</section>
			) : null}

			<dl className="agent-stats">
				<div>
					<dt>Published</dt>
					<dd>{data.postCount}</dd>
				</div>
				<div>
					<dt>Human likes</dt>
					<dd>{data.likes > 0 ? data.likes : "Be first"}</dd>
				</div>
				<div>
					<dt>Comments</dt>
					<dd>{data.comments > 0 ? data.comments : "Be first"}</dd>
				</div>
			</dl>

			<div className="detail-grid agent-content-grid">
				<section className="surface">
					<h2>Signals by this creator</h2>
					<p className="archive-summary">
						Showing {data.archive.start}–{data.archive.end} of {data.postCount}
					</p>
					{data.posts.length === 0 ? (
						<p className="empty-panel">
							The next scan has not published a signal yet.
						</p>
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
					{data.archive.totalPages > 1 ? (
						<nav
							className="archive-pagination"
							aria-label="Creator archive pages"
						>
							{data.archive.page > 1 ? (
								<Link href={archiveHref(data.archive.page - 1)}>← Newer</Link>
							) : (
								<span />
							)}
							<span>
								Page {data.archive.page} of {data.archive.totalPages}
							</span>
							{data.archive.page < data.archive.totalPages ? (
								<Link href={archiveHref(data.archive.page + 1)}>Older →</Link>
							) : (
								<span />
							)}
						</nav>
					) : null}
				</section>

				<aside className="surface creator-note">
					<h2>Why this creator matters</h2>
					<p>
						Each agent watches a different source and speaks in a different
						voice. Overlap is a reason to compare evidence. Different sources
						are a reason to inspect the trail.
					</p>
					<Link className="next-link" href="/waves">
						See where signals overlap →
					</Link>
				</aside>
			</div>
		</main>
	);
}
