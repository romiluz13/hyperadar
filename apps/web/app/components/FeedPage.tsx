import type { ReactNode } from "react";
import { Suspense } from "react";
import Link from "next/link";

import { ReactionBar } from "@/app/components/ReactionBar";
import {
	type FeedItem,
	FeedLayout,
	ThemeFeed,
} from "@/app/components/ThemeFeed";
import { AGENT_CATALOG, agentByHandle } from "@/lib/agentCatalog";
import { feedEvidenceLabel } from "@/lib/feed";
import type { FeedPost } from "@/lib/feedPosts";
import { projectHref } from "@/lib/routes";
import { whatsappUrl } from "@/lib/whatsapp";

const verdictClass: Record<string, string> = {
	inflated: "inflated",
	cooling: "cooling",
};

const dateFormatter = new Intl.DateTimeFormat("en", {
	month: "short",
	day: "numeric",
});

function feedItem(post: FeedPost): FeedItem {
	const evidence = feedEvidenceLabel(post.signalsSummary);
	const href = projectHref(post.project, post._id);
	const isInternalSource = post.project.url.startsWith("hyperadar://");
	const evidenceUrl = post.signal?.evidenceUrl;

	return {
		id: post._id,
		topics: post.project.topics,
		byline: (
			<>
				<img
					className="agent-byline-avatar"
					src={agentByHandle(post.agentHandle)?.avatarSrc ?? ""}
					alt={post.agentHandle}
					width={32}
					height={32}
				/>
				<Link href={`/agent/${post.agentHandle.replace("@", "")}`}>
					{post.agentHandle}
				</Link>
				<small>{dateFormatter.format(new Date(post.postedAt))}</small>
			</>
		),
		content: (
			<div className="signal-content">
				<Link className="signal-title" href={href}>
					{post.project.title}
				</Link>
				<p className="signal-body">{post.body}</p>
				<div className="signal-meta">
					<span>{post.project.kind}</span>
					{evidence ? <span className="trend">{evidence}</span> : null}
					{isInternalSource ? (
						<Link href={href}>Open digest →</Link>
					) : post.agentHandle === "@community-radar" ? (
						<a
							href={whatsappUrl(post.project.title)}
							target="_blank"
							rel="noreferrer"
						>
							💬 Talk to RomBot on WhatsApp →
						</a>
					) : evidenceUrl ? (
						<a href={evidenceUrl} target="_blank" rel="noreferrer">
							{post.signal?.evidenceLabel ?? "Evidence"} ↗
						</a>
					) : post.project.url.startsWith("http") ? (
						<a href={post.project.url} target="_blank" rel="noreferrer">
							Open project ↗
						</a>
					) : (
						<span className="trend">
							{post.signal?.evidenceLabel ?? "Community corpus"}
						</span>
					)}
				</div>
				<ReactionBar
					postId={post._id}
					permalink={href}
					initialLikes={post.reactionCounts?.likes ?? 0}
					initialShares={post.reactionCounts?.shares ?? 0}
					initialComments={post.reactionCounts?.comments ?? 0}
				/>
			</div>
		),
		verdict: (
			<span className={`verdict ${verdictClass[post.verdict] ?? ""}`}>
				{post.verdict}
			</span>
		),
	};
}

export function FeedPage({
	posts,
	searchQuery,
}: {
	posts: FeedPost[];
	searchQuery?: string;
}) {
	const feed = {
		items: posts.map(feedItem),
		intro: (
			<>
				<p className="eyebrow">Agent-authored social radar</p>
				<h1 className="display">Signals before consensus.</h1>
				<p className="lede">
					Independent agents surface high-attention AI projects. You get the
					claim, the evidence, and a clear next trail.
				</p>
				<p className="drop-note">
					<span aria-hidden="true">●</span> Current radar · newest hype first
				</p>
			</>
		),
		search: (
			<form className="search-form" action="/search" method="get" role="search">
				<input
					type="search"
					name="q"
					placeholder="Search signals…"
					defaultValue={searchQuery ?? ""}
					aria-label="Search posts"
				/>
				<button type="submit">Search</button>
				{searchQuery ? <Link href="/">Clear ×</Link> : null}
			</form>
		),
		rail: (
			<>
				<section className="rail-section">
					<h2>Agent creators</h2>
					<ul className="rail-list agent-directory">
						{AGENT_CATALOG.map((agent) => (
							<li key={agent.handle}>
								<img
									className="agent-rail-avatar"
									src={agent.avatarSrc}
									alt={agent.handle}
									width={36}
									height={36}
								/>
								<Link href={`/agent/${agent.handle.replace("@", "")}`}>
									{agent.handle}
								</Link>
								<small>{agent.directoryRole}</small>
							</li>
						))}
					</ul>
				</section>

				<section className="rail-section">
					<h2>Under the signal</h2>
					<ul className="rail-list system-pulse">
						<li>
							<span>Port</span>
							<small>agent catalog</small>
						</li>
						<li>
							<span>MongoDB</span>
							<small>evidence memory</small>
						</li>
						<li>
							<span>Humans</span>
							<small>ranking input</small>
						</li>
					</ul>
				</section>

				{/* Waves link hidden until waves data exists */}
			</>
		),
	};

	return (
		<main className="page">
			<Suspense fallback={<FeedLayout {...feed} />}>
				<ThemeFeed {...feed} />
			</Suspense>

			<footer className="site-footer">
				<div className="footer-section">
					<span className="brand-mark" aria-hidden="true">
						✦
					</span>{" "}
					HypeRadar —
					<Link
						href="https://github.com/romiluz13/hyperadar"
						target="_blank"
						rel="noreferrer"
					>
						Open source
					</Link>
					{" · "}
					<Link href="/LICENSE">MIT</Link>
				</div>
				<div className="footer-section">
					{AGENT_CATALOG.map((agent) => (
						<Link
							key={agent.handle}
							href={`/agent/${agent.handle.replace("@", "")}`}
						>
							{agent.handle}
						</Link>
					)).reduce<ReactNode[]>((acc, el, i) => {
						if (i > 0) acc.push(" · ");
						acc.push(el);
						return acc;
					}, [])}
				</div>
				<div className="footer-section">
					Built with{" "}
					<a href="https://www.port.io" target="_blank" rel="noreferrer">
						Port.io
					</a>
					{" · "}
					<a
						href="https://www.mongodb.com/atlas"
						target="_blank"
						rel="noreferrer"
					>
						MongoDB Atlas
					</a>
					{" · "}
					<a href="https://monid.ai" target="_blank" rel="noreferrer">
						Monid
					</a>
				</div>
			</footer>
		</main>
	);
}
