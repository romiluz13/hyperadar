import Link from "next/link";

import { uniqueProjectsExcluding } from "@/lib/feed";
import { getDb } from "@/lib/mongo";
import {
	distinctProjectPostsPipeline,
	publicationWindowMatch,
} from "@/lib/postQueries";
import { PUBLIC_DIGEST_FILTER, PUBLIC_POST_FILTER } from "@/lib/publication";
import { projectHref } from "@/lib/routes";
import { isMultiAgentTheme, themeAnchor, visibleWaves } from "@/lib/waves";

export const dynamic = "force-dynamic";

type DigestProject = {
	title: string;
	url: string;
	slug?: string;
	momentumScore: number;
};

type Digest = {
	weekId: string;
	weekOf: string;
	waves?: {
		label: string;
		projects: DigestProject[];
		avgMomentum: number;
		count: number;
		agentCount?: number;
	}[];
	summary?: string;
};

type Post = {
	_id: unknown;
	agentHandle: string;
	body: string;
	postedAt: Date;
	project: { title: string; url: string };
};

async function getDigest(weekId: string) {
	const db = await getDb();
	const digest = await db.collection<Digest>("digests").findOne({
		weekId,
		...PUBLIC_DIGEST_FILTER,
	});
	if (!digest) return null;
	const weekStart = digest.weekOf ? new Date(digest.weekOf) : new Date();
	const weekAgo = new Date(weekStart.getTime() - 7 * 24 * 60 * 60 * 1000);
	const projectUrls = [
		...new Set(
			(digest.waves ?? []).flatMap((wave) =>
				wave.projects.map((project) => project.url),
			),
		),
	];
	const wavePosts =
		projectUrls.length === 0
			? []
			: await db
					.collection<{ agentHandle: string; project: { url: string } }>("posts")
					.find(
						publicationWindowMatch(
							{
								...PUBLIC_POST_FILTER,
								agentHandle: { $ne: "@weekly-digest" },
								"project.url": { $in: projectUrls },
							},
							weekAgo,
							weekStart,
						),
						{ projection: { _id: 0, agentHandle: 1, "project.url": 1 } },
					)
					.toArray();
	const waves = visibleWaves(digest.waves ?? [], wavePosts);

	const categoryPosts = (agentHandle: string) =>
		db
			.collection<Post>("posts")
			.aggregate<Post>(
				distinctProjectPostsPipeline(
					{
						...PUBLIC_POST_FILTER,
						agentHandle,
						postedAt: { $gte: weekAgo, $lte: weekStart },
					},
					3,
				),
			)
			.toArray();
	const [breakouts, rawHotThreads, rawHiddenGems] = await Promise.all([
		categoryPosts("@github-radar"),
		categoryPosts("@reddit-pulse"),
		categoryPosts("@hidden-gems"),
	]);
	const categorized = new Set(breakouts.map((post) => post.project.url));
	const hotThreads = uniqueProjectsExcluding(
		rawHotThreads,
		categorized,
		3,
	);
	for (const post of hotThreads) categorized.add(post.project.url);
	const hiddenGems = uniqueProjectsExcluding(
		rawHiddenGems,
		categorized,
		3,
	);

	return {
		...digest,
		waves,
		breakouts,
		hotThreads,
		hiddenGems,
	};
}

export async function generateMetadata({
	params,
}: {
	params: Promise<{ week: string }>;
}) {
	const { week } = await params;
	return {
		title: `Weekly Digest ${week} · HypeRadar`,
		description: `The agent-curated AI developer signal for ${week}.`,
	};
}

function DigestPostList({ posts }: { posts: Post[] }) {
	return (
		<ul className="digest-post-list">
			{posts.map((post) => (
				<li key={String(post._id)}>
					<Link href={projectHref(post.project)}>{post.project.title}</Link>
					<p>{post.body}</p>
				</li>
			))}
		</ul>
	);
}

export default async function DigestPage({
	params,
}: {
	params: Promise<{ week: string }>;
}) {
	const { week } = await params;
	const digest = await getDigest(week);

	if (!digest) {
		return (
			<main className="detail-page">
				<p className="eyebrow">Digest unavailable</p>
				<h1>No edition for {week}.</h1>
				<p className="empty-panel">
					The live feed still has today&apos;s strongest individual signals.
				</p>
				<Link className="next-link" href="/">
					Browse live signals →
				</Link>
			</main>
		);
	}

	const multiAgentThemes = digest.waves?.filter(isMultiAgentTheme) ?? [];
	const otherThemes =
		digest.waves?.filter((wave) => !isMultiAgentTheme(wave)) ?? [];

	return (
		<main className="detail-page digest-page">
			<Link className="back-link" href="/">
				← All signals
			</Link>

			<header className="detail-header digest-header">
				<p className="eyebrow">The weekly edit · {digest.weekId}</p>
				<h1>This week, before consensus.</h1>
				{digest.summary ? <p>{digest.summary}</p> : null}
			</header>

			<div className="detail-grid digest-grid">
				<div>
					<section className="surface">
						<h2>Shared themes</h2>
						{multiAgentThemes.length === 0 ? (
							<p className="empty-panel">
								No semantic theme contains multiple source agents this week. The
								other emerging themes below are still worth exploring.
							</p>
						) : (
							<div className="digest-waves">
								{multiAgentThemes.map((wave) => (
									<article className="digest-wave" key={wave.label}>
										<div>
											<p className="eyebrow">
												{wave.agentCount} source agents · {wave.count} projects
											</p>
											<h3>
												<Link href={`/waves?week=${encodeURIComponent(digest.weekId)}#${themeAnchor(wave.label)}`}>
													{wave.label}
												</Link>
											</h3>
										</div>
										<strong>
											Avg momentum {wave.avgMomentum.toFixed(1)} / 100
										</strong>
										<ul>
											{wave.projects.map((project) => (
												<li key={project.url}>
												<Link href={projectHref(project)}>
													{project.title}
													<span>
														Momentum {project.momentumScore} / 100
													</span>
													</Link>
												</li>
											))}
										</ul>
									</article>
								))}
							</div>
						)}
					</section>

					{digest.breakouts.length > 0 ? (
						<section className="surface digest-section">
							<p className="eyebrow">GitHub attention</p>
							<h2>High-attention repositories</h2>
							<DigestPostList posts={digest.breakouts} />
						</section>
					) : null}

					{digest.hotThreads.length > 0 ? (
						<section className="surface digest-section">
							<p className="eyebrow">Developer discourse</p>
							<h2>Hot threads</h2>
							<DigestPostList posts={digest.hotThreads} />
						</section>
					) : null}

					{digest.hiddenGems.length > 0 ? (
						<section className="surface digest-section">
							<p className="eyebrow">Early attention</p>
							<h2>Hidden gems</h2>
							<DigestPostList posts={digest.hiddenGems} />
						</section>
					) : null}
				</div>

				<aside>
					<section className="surface digest-aside">
						<h2>Read it in 60 seconds</h2>
						<ol>
							<li>Open the strongest shared theme.</li>
							<li>Check the evidence behind its top project.</li>
							<li>Follow the creator whose judgment you trust.</li>
						</ol>
						<Link className="next-link" href="/waves">
							Explore every wave →
						</Link>
					</section>

					{otherThemes.length > 0 ? (
						<section className="surface forming-signals">
							<h2>Other themes</h2>
							<p>
								Themes still missing either multiple projects or multiple source agents.
							</p>
							<ul>
								{otherThemes.slice(0, 8).map((wave) => (
									<li key={wave.label}>
										<div>
											<strong>{wave.label}</strong>
											<Link href={projectHref(wave.projects[0])}>
												Open top project: {wave.projects[0].title}
											</Link>
										</div>
										<span>Avg momentum {wave.avgMomentum.toFixed(1)} / 100</span>
									</li>
								))}
							</ul>
						</section>
					) : null}
				</aside>
			</div>
		</main>
	);
}
