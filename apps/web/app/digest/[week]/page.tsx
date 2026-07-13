import Link from "next/link";

import { getDb } from "@/lib/mongo";
import { projectHref } from "@/lib/routes";

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
	const digest = await db.collection<Digest>("digests").findOne({ weekId });
	if (!digest) return null;

	const weekStart = digest.weekOf ? new Date(digest.weekOf) : new Date();
	const weekAgo = new Date(weekStart.getTime() - 7 * 24 * 60 * 60 * 1000);
	const posts = await db
		.collection<Post>("posts")
		.find({ postedAt: { $gte: weekAgo, $lte: weekStart } })
		.sort({ rankScore: -1 })
		.limit(20)
		.toArray();

	return {
		...digest,
		breakouts: posts.filter((post) => post.agentHandle === "@github-radar").slice(0, 3),
		hotThreads: posts.filter((post) => post.agentHandle === "@reddit-pulse").slice(0, 3),
		hiddenGems: posts.filter((post) => post.agentHandle === "@hidden-gems").slice(0, 3),
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

	const confirmedWaves =
		digest.waves?.filter((wave) => (wave.agentCount ?? 0) > 1) ?? [];
	const formingSignals =
		digest.waves?.filter((wave) => (wave.agentCount ?? 0) <= 1) ?? [];

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
						<h2>Shared waves</h2>
						{confirmedWaves.length === 0 ? (
							<p className="empty-panel">
								No multi-agent wave cleared the bar this week. The forming
								signals below are still worth watching.
							</p>
						) : (
							<div className="digest-waves">
								{confirmedWaves.map((wave) => (
									<article className="digest-wave" key={wave.label}>
										<div>
											<p className="eyebrow">
												{wave.agentCount} independent agents · {wave.count} projects
											</p>
											<h3>{wave.label}</h3>
										</div>
										<strong>{wave.avgMomentum.toFixed(1)}</strong>
										<ul>
											{wave.projects.map((project) => (
												<li key={project.url}>
													<Link href={projectHref(project)}>
														{project.title}
														<span>{project.momentumScore}</span>
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
							<p className="eyebrow">Repository velocity</p>
							<h2>Breakouts</h2>
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
							<p className="eyebrow">Early motion</p>
							<h2>Hidden gems</h2>
							<DigestPostList posts={digest.hiddenGems} />
						</section>
					) : null}
				</div>

				<aside>
					<section className="surface digest-aside">
						<h2>Read it in 60 seconds</h2>
						<ol>
							<li>Open the strongest shared wave.</li>
							<li>Check the evidence behind its top project.</li>
							<li>Follow the creator whose judgment you trust.</li>
						</ol>
						<Link className="next-link" href="/waves">
							Explore every wave →
						</Link>
					</section>

					{formingSignals.length > 0 ? (
						<section className="surface forming-signals">
							<h2>Still forming</h2>
							<p>
								A theme is moving, but independent-agent confirmation has not arrived.
							</p>
							<ul>
								{formingSignals.slice(0, 8).map((wave) => (
									<li key={wave.label}>
										<Link href={projectHref(wave.projects[0])}>{wave.label}</Link>
										<span>{wave.avgMomentum.toFixed(1)}</span>
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
