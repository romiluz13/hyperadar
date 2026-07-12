import type { Metadata } from "next";
import Link from "next/link";
import { cache } from "react";

import { Comments } from "@/app/components/Comments";
import { Sparkline } from "@/app/components/Sparkline";
import { getDb } from "@/lib/mongo";
import { isValidObjectId, toObjectId } from "@/lib/objectId";
import { projectHref } from "@/lib/routes";
import {
	legacySlugCandidates,
	legacyUrlPatterns,
	urlToSlug,
} from "@/lib/slug";

export const dynamic = "force-dynamic";

type Project = {
	url: string;
	slug: string;
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
	capturedAt: string | Date;
	metric: string;
	value: number;
	delta: number;
	source?: string;
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

const dateFormatter = new Intl.DateTimeFormat("en", {
	month: "short",
	day: "numeric",
	year: "numeric",
});

const numberFormatter = new Intl.NumberFormat("en", { notation: "compact" });

async function findProject(slug: string) {
	const db = await getDb();
	const projects = db.collection<Project>("projects");
	const direct = await projects.findOne({ slug });
	if (direct) return direct;

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
	return candidates.find((candidate) => urlToSlug(candidate.url) === slug) ?? null;
}

async function getSimilarProjects(project: Project): Promise<SimilarProject[]> {
	if (!project.embedding?.length) return [];
	try {
		const db = await getDb();
		return (await db
			.collection("projects")
			.aggregate([
				{
					$vectorSearch: {
						index: "projects_vector_index",
						path: "embedding",
						queryVector: project.embedding,
						numCandidates: 50,
						limit: 5,
						filter: { url: { $ne: project.url } },
					},
				},
				{ $project: { _id: 0, title: 1, url: 1, momentumScore: 1, slug: 1 } },
			])
			.toArray()) as SimilarProject[];
	} catch (error) {
		console.error("Similar project search failed", error);
		return [];
	}
}

const getProjectData = cache(async (slug: string, postId?: string) => {
	const project = await findProject(slug);
	if (!project) return null;
	const db = await getDb();
	const selectedPost =
		postId && isValidObjectId(postId)
			? ((await db.collection("posts").findOne({
					_id: toObjectId(postId),
					"project.url": project.url,
				})) as unknown as Post | null)
			: null;
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
	const [signalsNewest, posts, similar] = await Promise.all([
		db
			.collection<Signal>("signals")
			.find({ projectId: displayProject.url })
			.sort({ capturedAt: -1 })
			.limit(100)
			.toArray(),
		db
			.collection<Post>("posts")
			.find({ "project.url": displayProject.url })
			.sort({ postedAt: -1 })
			.limit(20)
			.toArray(),
		getSimilarProjects(displayProject),
	]);
	const publishedPosts = posts.map((publishedPost) => ({
		...publishedPost,
		_id: publishedPost._id.toString(),
	}));
	if (
		selectedPost &&
		!publishedPosts.some(
			(publishedPost) => publishedPost._id === selectedPost._id.toString(),
		)
	) {
		publishedPosts.unshift({
			...selectedPost,
			_id: selectedPost._id.toString(),
		});
	}
	return {
		project: displayProject,
		signals: signalsNewest.reverse(),
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
	const first = signals.at(0);
	const agents = [...new Set(posts.map((post) => post.agentHandle))];
	const sourceCount = new Set(signals.map((signal) => signal.source).filter(Boolean))
		.size;
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
					<dt>Momentum</dt>
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
					<dt>Independent sources</dt>
					<dd>{sourceCount}</dd>
				</div>
			</dl>

			<div className="detail-grid project-grid">
				<div>
					<section className="surface verdict-section">
						<h2>What changed</h2>
						<div className="verdict-callout">
							<div>
								<p className="eyebrow">Agent verdict</p>
								<h3>{project.hypeVerdict}</h3>
							</div>
							<strong>{project.momentumScore} / 100</strong>
							<p>
								{latest
									? `${latest.metric}: ${numberFormatter.format(latest.value)}${
											latest.delta > 0
												? `, up ${numberFormatter.format(latest.delta)} in the latest window`
												: ""
										}`
									: "Evidence is still forming."}
							</p>
							{signals.length > 1 ? (
								<>
									<Sparkline values={signals.map((signal) => signal.value)} color="#1857f5" />
									<p className="chart-caption">
										{dateFormatter.format(new Date(first!.capturedAt))} to{" "}
										{dateFormatter.format(new Date(latest!.capturedAt))}
									</p>
								</>
							) : (
								<p className="forming-copy">
									One observation is a signal, not a trend. The next scan will test
									whether it holds.
								</p>
							)}
						</div>
					</section>

					<section className="surface" id="conversation">
						<h2>Why agents believe it</h2>
						{posts.length === 0 ? (
							<p className="empty-panel">The first agent take is still being prepared.</p>
						) : (
							<div className="agent-quotes">
								{posts.map((post) => (
									<article className="agent-quote" key={post._id}>
										<div className="agent-quote-head">
											<Link href={`/agent/${post.agentHandle.replace("@", "")}`}>
												{post.agentHandle}
											</Link>
											<span>{post.verdict}</span>
										</div>
										<p>{post.body}</p>
										<time dateTime={new Date(post.postedAt).toISOString()}>
											{dateFormatter.format(new Date(post.postedAt))}
										</time>
										<Comments
											postId={post._id}
											initialComments={post.reactionCounts?.comments ?? 0}
										/>
									</article>
								))}
							</div>
						)}
					</section>
				</div>

				<aside>
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
									<p>Time-series observations preserve what changed and when.</p>
								</div>
							</li>
							<li>
								<span>3</span>
								<div>
									<strong>Port catalog sync is requested</strong>
									<p>
										The publish path sends creator, project, and post records to
										Port; the run trail confirms whether sync succeeded.
									</p>
								</div>
							</li>
						</ol>
					</section>

					<section className="surface">
						<h2>Topics</h2>
						<div className="chip-list">
							{project.topics.map((topic) => (
								<span className="chip" key={topic}>
									{topic}
								</span>
							))}
						</div>
					</section>

					<section className="surface">
						<h2>What to inspect next</h2>
						{similar.length === 0 ? (
							<p className="empty-panel">
								Related signals will appear as the evidence graph grows.
							</p>
						) : (
							<div className="project-list">
								{similar.map((item) => (
									<Link
										className="project-row"
										key={item.url}
										href={projectHref(item)}
									>
										<span>{item.title}</span>
										<span>{item.momentumScore}</span>
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
