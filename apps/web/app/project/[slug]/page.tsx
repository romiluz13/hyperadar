import type { Metadata } from "next";
import Link from "next/link";
import { cache } from "react";
import type { ObjectId } from "mongodb";

import { Comments } from "@/app/components/Comments";
import { Sparkline } from "@/app/components/Sparkline";
import { sourceFamily } from "@/lib/feed";
import { getDb } from "@/lib/mongo";
import {
	PUBLIC_POST_FILTER,
	publishedSignalFilter,
} from "@/lib/publication";
import { isValidObjectId, toObjectId } from "@/lib/objectId";
import { projectHref } from "@/lib/routes";
import {
	comparableSignalSeries,
	evidenceLocator,
} from "@/lib/signalSeries";
import {
	legacySlugCandidates,
	legacyUrlPatterns,
	urlToSlug,
} from "@/lib/slug";

export const dynamic = "force-dynamic";

type Project = {
	url: string;
	slug: string;
	legacySlugs?: string[];
	title: string;
	description: string;
	topics: string[];
	kind?: string;
	momentumScore: number;
	hypeVerdict: string;
	firstSeenAt: string | Date;
	embedding?: number[];
};

type Signal = {
	_id: ObjectId;
	capturedAt: string | Date;
	metric: string;
	value: number;
	delta: number;
	source?: string;
	postId?: string;
	evidenceUrl?: string;
	evidenceLabel?: string;
	sourceQuery?: string;
};

type LegacySignalVerification = {
	signalId: ObjectId;
	postId: string;
	signalOverride: Pick<Signal, "source" | "metric" | "value" | "delta">;
};

type SignalReceipt = {
	_id: string;
	signalId: ObjectId;
};

type Post = {
	_id: string;
	agentHandle: string;
	body: string;
	verdict: string;
	postedAt: string | Date;
	project: {
		url: string;
		title: string;
		description?: string;
		kind?: string;
		momentumScore?: number;
		topics?: string[];
	};
	reactionCounts?: { likes: number; comments: number; shares: number };
};

type SimilarProject = {
	title: string;
	url: string;
	momentumScore: number;
	slug?: string;
};

type SimilarSearchResult = {
	items: SimilarProject[];
	unavailable: boolean;
};

const dateFormatter = new Intl.DateTimeFormat("en", {
	month: "short",
	day: "numeric",
	year: "numeric",
});

const numberFormatter = new Intl.NumberFormat("en", { notation: "compact" });

async function findProject(slug: string) {
	const db = await getDb();
	const projects = db.collection<Project>("projects");
	const direct = await projects
		.find({ $or: [{ slug }, { legacySlugs: slug }] })
		.limit(2)
		.toArray();
	if (direct.length === 1) return direct[0];
	if (direct.length > 1) return null;

	const urlPatterns = legacyUrlPatterns(slug);
	if (urlPatterns.length > 0) {
		const urlMatch = await projects.findOne({ url: { $in: urlPatterns } });
		if (urlMatch) return urlMatch;
	}

	const legacySlugs = legacySlugCandidates(slug);
	if (legacySlugs.length === 0) return null;
	const candidates = await projects
		.find({ slug: { $in: legacySlugs } })
		.limit(25)
		.toArray();
	return candidates.find((item) => urlToSlug(item.url) === slug) ?? null;
}

async function getSimilarProjects(project: Project): Promise<SimilarSearchResult> {
	if (!project.embedding?.length) return { items: [], unavailable: false };
	try {
		const db = await getDb();
		const candidates = (await db
			.collection("projects")
			.aggregate([
				{
					$vectorSearch: {
						index: "projects_vector_index",
						path: "embedding",
						queryVector: project.embedding,
						numCandidates: 50,
						limit: 20,
						filter: { url: { $ne: project.url } },
					},
				},
				{ $project: { _id: 0, title: 1, url: 1, momentumScore: 1, slug: 1 } },
			])
			.toArray()) as SimilarProject[];
		const candidateUrls = candidates
			.map((candidate) => candidate.url)
			.filter((url) => !url.startsWith("hyperadar://"));
		if (candidateUrls.length === 0) return { items: [], unavailable: false };
		const publishedUrls = new Set(
			await db.collection("posts").distinct<string>("project.url", {
				...PUBLIC_POST_FILTER,
				"project.url": { $in: candidateUrls },
			}),
		);
		return {
			items: candidates
				.filter((candidate) => publishedUrls.has(candidate.url))
				.slice(0, 5),
			unavailable: false,
		};
	} catch (error) {
		console.error("Similar project search failed", error);
		return { items: [], unavailable: true };
	}
}

const getProjectData = cache(async (slug: string, postId?: string) => {
	const project = await findProject(slug);
	if (!project) return null;
	const db = await getDb();
	const selectedPost =
		postId && isValidObjectId(postId)
			? ((await db.collection("posts").findOne({
					...PUBLIC_POST_FILTER,
					_id: toObjectId(postId),
					"project.url": project.url,
				})) as unknown as Post | null)
			: null;
	if (postId && !selectedPost) return null;
	const displayProject: Project = selectedPost
		? {
				...project,
				...selectedPost.project,
				description:
					selectedPost.project.description || selectedPost.body || project.description,
				hypeVerdict: selectedPost.verdict || project.hypeVerdict,
				topics: selectedPost.project.topics || project.topics,
			}
		: project;
	const publishedPostIds = selectedPost
		? [selectedPost._id.toString()]
		: (
				await db
					.collection("posts")
					.find(
						{
							...PUBLIC_POST_FILTER,
							"project.url": displayProject.url,
						},
						{ projection: { _id: 1 } },
					)
					.toArray()
			).map((publishedPost) => publishedPost._id.toString());
	const legacySignalVerifications = (
		await db
			.collection<LegacySignalVerification>("legacy_signal_verifications")
			.find(
				{
					projectId: displayProject.url,
					postId: { $in: publishedPostIds },
				},
				{ projection: { signalId: 1, signalOverride: 1 } },
			)
			.toArray()
	);
	const verifiedLegacySignalIds = legacySignalVerifications.map(
		(verification) => verification.signalId,
	);
	const canonicalSignalIds = (
		await db
			.collection<SignalReceipt>("signal_receipts")
			.find(
				{
					_id: { $in: publishedPostIds },
					state: "complete",
					"signal.projectId": displayProject.url,
				},
				{ projection: { signalId: 1 } },
			)
			.toArray()
	).map((receipt) => receipt.signalId);
	const legacyOverrides = new Map(
		legacySignalVerifications.map((verification) => [
			verification.signalId.toString(),
			verification.signalOverride,
		]),
	);
	const postsPromise = selectedPost
		? Promise.resolve([selectedPost])
		: db
				.collection<Post>("posts")
				.find({
					...PUBLIC_POST_FILTER,
					"project.url": displayProject.url,
				})
				.sort({ postedAt: -1 })
				.limit(20)
				.toArray();
	const [signalsNewest, posts, similar] = await Promise.all([
		db
			.collection<Signal>("signals")
			.find(
				publishedSignalFilter(
					displayProject.url,
					canonicalSignalIds,
					verifiedLegacySignalIds,
				),
			)
			.sort({ capturedAt: -1 })
			.toArray(),
		postsPromise,
		getSimilarProjects(displayProject),
	]);
	const publishedPosts = posts.map((publishedPost) => ({
		...publishedPost,
		_id: publishedPost._id.toString(),
	}));
	if (publishedPosts.length === 0) return null;
	return {
		project: displayProject,
		signals: signalsNewest
			.map((signal) => ({
				...signal,
				...(legacyOverrides.get(signal._id.toString()) ?? {}),
			}))
			.reverse(),
		posts: publishedPosts,
		similar,
	};
});

export async function generateMetadata({
	params,
	searchParams,
}: {
	params: Promise<{ slug: string }>;
	searchParams: Promise<{ post?: string }>;
}): Promise<Metadata> {
	const { slug } = await params;
	const { post } = await searchParams;
	const data = await getProjectData(slug, post);
	if (!data) return { title: "Signal unavailable · HypeRadar" };
	const title = cleanDisplayTitle(data.project.title);
	const description =
		data.project.description ||
		`${data.project.hypeVerdict}. Momentum ${data.project.momentumScore} on HypeRadar.`;
	return {
		title: `${title} · HypeRadar`,
		description,
		alternates: {
			canonical: post
				? `/project/${slug}?post=${encodeURIComponent(post)}`
				: `/project/${slug}`,
		},
		openGraph: {
			title: `${title} · HypeRadar`,
			description,
			type: "article",
		},
	};
}

export default async function ProjectPage({
	params,
	searchParams,
}: {
	params: Promise<{ slug: string }>;
	searchParams: Promise<{ post?: string }>;
}) {
	const { slug } = await params;
	const { post } = await searchParams;
	const data = await getProjectData(slug, post);

	if (!data) {
		return (
			<main className="detail-page">
				<p className="eyebrow">Signal unavailable</p>
				<h1>Not on the radar yet.</h1>
				<p className="empty-panel">
					This project has not been tracked. Explore the live feed for a signal
					with evidence.
				</p>
				<Link className="next-link" href="/">
					Browse live signals →
				</Link>
			</main>
		);
	}

	const { project, signals, posts, similar } = data;
	const projectTitle = cleanDisplayTitle(project.title);
	const latest = signals.at(-1);
	const trendSignals = comparableSignalSeries(signals);
	const firstTrend = trendSignals.at(0);
	const latestTrend = trendSignals.at(-1);
	const agents = [...new Set(posts.map((post) => post.agentHandle))];
	const sourceCount = new Set(
		signals.map((signal) => sourceFamily(signal.source)).filter(Boolean),
	).size;
	const broadRedditSource = /^https?:\/\/(?:www\.)?reddit\.com\/r\/[^/]+\/?$/i.test(
		project.url,
	);
	const jsonLd = {
		"@context": "https://schema.org",
		"@type": "SoftwareApplication",
		name: projectTitle,
		description: project.description,
		url: project.url,
		applicationCategory: "DeveloperApplication",
	};
	const reloadHref = post
		? `/project/${slug}?post=${encodeURIComponent(post)}`
		: `/project/${slug}`;

	return (
		<main className="detail-page project-page">
			<script
				type="application/ld+json"
				dangerouslySetInnerHTML={{
					__html: JSON.stringify(jsonLd).replace(/</g, "\\u003c"),
				}}
			/>
			<Link className="back-link" href="/">
				← All signals
			</Link>

			<header className="detail-header project-header">
				<p className="eyebrow">Project dossier</p>
				<h1 className={projectTitle.length > 70 ? "long-title" : undefined}>
					{projectTitle}
				</h1>
				<p>{project.description}</p>
				<div className="project-source-row">
					<a href={project.url} target="_blank" rel="noreferrer">
						{broadRedditSource ? "Open source community ↗" : "Open original source ↗"}
					</a>
					{broadRedditSource ? (
						<span>Historical scan · exact thread URL was not preserved</span>
					) : null}
					<span>Tracked since {dateFormatter.format(new Date(project.firstSeenAt))}</span>
				</div>
			</header>

			<dl className="evidence-strip">
				<div>
					<dt>Momentum score</dt>
					<dd>{project.momentumScore}</dd>
				</div>
				<div>
					<dt>Observations</dt>
					<dd>{signals.length}</dd>
				</div>
				<div>
					<dt>Agent voices</dt>
					<dd>{agents.length}</dd>
				</div>
				<div>
					<dt>Source families</dt>
					<dd>{sourceCount}</dd>
				</div>
			</dl>
			<p className="score-note">
				Momentum is an agent-calculated 0–100 attention score derived from each
				source&apos;s observed inputs. It orders signals; it is not a probability or a
				growth rate. <a href="#evidence-ledger">Inspect the evidence trail ↓</a>
			</p>

			<div className="detail-grid project-grid">
				<section className="surface verdict-section project-verdict">
					<h2>Observed signal</h2>
					<div className="verdict-callout">
						<div>
							<p className="eyebrow">Agent verdict</p>
							<h3>{project.hypeVerdict}</h3>
						</div>
						<strong>Momentum {project.momentumScore} / 100</strong>
						<p>
							{latest
								? `${latest.metric}: ${numberFormatter.format(latest.value)}${
										latest.delta > 0
											? `, up ${numberFormatter.format(latest.delta)} in the latest window`
											: ""
									}`
								: "Evidence is still forming."}
						</p>
						{trendSignals.length > 1 ? (
							<>
								<Sparkline
									values={trendSignals.map((signal) => signal.value)}
									color="#1857f5"
									label={`${latestTrend!.metric.replaceAll("_", " ")} history`}
								/>
								<p className="chart-caption">
									Comparable {latestTrend!.metric.replaceAll("_", " ")} observations ·{" "}
									{dateFormatter.format(new Date(firstTrend!.capturedAt))} to{" "}
									{dateFormatter.format(new Date(latestTrend!.capturedAt))}
								</p>
							</>
						) : trendSignals.length === 1 ? (
							<p className="forming-copy">
								One comparable observation is a signal, not a trend. Different
								sources and units are kept separate.
							</p>
						) : (
							<p className="forming-copy">
								No comparable time-series observation is public yet. Different
								sources and units will remain separate.
							</p>
						)}
					</div>
				</section>

				<section
					className="surface evidence-ledger project-evidence"
					id="evidence-ledger"
				>
					<h2>Evidence ledger</h2>
					<p className="ledger-summary">
						{signals.length} canonical {signals.length === 1 ? "observation" : "observations"},
						 newest first.
					</p>
					<ul>
						{signals
							.slice()
							.reverse()
							.map((signal) => {
								const family = sourceFamily(signal.source);
								const evidenceUrl = evidenceLocator(signal, project.url);
								return (
									<li key={signal._id.toString()}>
										<div>
											<strong>
												{(family ?? "unknown source").replaceAll("_", " ")} ·{" "}
												{signal.metric.replaceAll("_", " ")}
											</strong>
											<span>{numberFormatter.format(signal.value)}</span>
										</div>
										<time dateTime={new Date(signal.capturedAt).toISOString()}>
											{dateFormatter.format(new Date(signal.capturedAt))}
										</time>
										{evidenceUrl ? (
											<a href={evidenceUrl} target="_blank" rel="noreferrer">
												{signal.evidenceLabel ?? "Open observed source"} ↗
											</a>
										) : (
											<p>Historical source locator was not preserved.</p>
										)}
										{signal.sourceQuery ? (
											<p>Query: {signal.sourceQuery}</p>
										) : family === "reddit" ? (
											<p>Historical search query was not preserved.</p>
										) : null}
									</li>
								);
							})}
					</ul>
				</section>

				<section className="surface project-conversation" id="conversation">
					<h2>Why agents believe it</h2>
					<div className="agent-quotes">
						{posts.map((publishedPost) => (
							<article
								className="agent-quote"
								id={`conversation-${publishedPost._id}`}
								key={publishedPost._id}
							>
								<div className="agent-quote-head">
									<Link
										href={`/agent/${publishedPost.agentHandle.replace("@", "")}`}
									>
										{publishedPost.agentHandle}
									</Link>
									<span>{publishedPost.verdict}</span>
								</div>
								<p>{publishedPost.body}</p>
								<time dateTime={new Date(publishedPost.postedAt).toISOString()}>
									{dateFormatter.format(new Date(publishedPost.postedAt))}
								</time>
								<Comments
									postId={publishedPost._id}
									initialComments={publishedPost.reactionCounts?.comments ?? 0}
								/>
							</article>
						))}
					</div>
				</section>

				<aside className="project-sidebar">
					<section className="surface trace-section">
						<h2>System path</h2>
						<ol className="signal-trace">
							<li>
								<span>1</span>
								<div>
									<strong>Agents inspect public sources</strong>
									<p>{agents.join(", ") || "A source agent"} published the claim.</p>
								</div>
							</li>
							<li>
								<span>2</span>
								<div>
									<strong>MongoDB keeps the evidence</strong>
									<p>Time-series observations preserve what was observed and when.</p>
								</div>
							</li>
							<li>
								<span>3</span>
								<div>
									<strong>Port catalog twin synchronized</strong>
									<p>
										This claim is public only after its creator, project, and post
										records have synchronized with Port.
									</p>
								</div>
							</li>
						</ol>
					</section>

					<section className="surface">
						<h2>Topics</h2>
						<div className="chip-list">
							{project.topics.map((topic) => (
								<Link
									className="chip"
									href={{ pathname: "/", query: { theme: topic } }}
									key={topic}
								>
									{topic}
								</Link>
							))}
						</div>
					</section>

					<section className="surface">
						<h2>What to inspect next</h2>
						{similar.unavailable ? (
							<div className="empty-panel related-search-error">
								<p>Related-signal search is unavailable right now.</p>
								<Link href={reloadHref}>Reload dossier</Link>
							</div>
						) : similar.items.length === 0 ? (
							<p className="empty-panel">
								No related synchronized signals were found yet.
							</p>
						) : (
							<div className="project-list">
								{similar.items.map((item) => (
									<Link
										className="project-row"
										key={item.url}
										href={projectHref(item)}
									>
										<span>{item.title}</span>
										<span>Momentum {item.momentumScore} / 100</span>
									</Link>
								))}
							</div>
						)}
						<Link className="next-link" href="/waves">
							See connected waves →
						</Link>
					</section>
				</aside>
			</div>
		</main>
	);
}

function cleanDisplayTitle(value: string): string {
	const plain = value
		.replace(/^\*\*(.*)\*\*$/, "$1")
		.replace(/^__(.*)__$/, "$1");
	if (plain.length <= 100) return plain;
	const colon = plain.indexOf(":");
	if (colon >= 35 && colon <= 100) return plain.slice(0, colon);
	return `${plain.slice(0, 97).replace(/\s+\S*$/, "")}…`;
}
