import Link from "next/link";

import { getDb } from "@/lib/mongo";
import { publicationWindowMatch } from "@/lib/postQueries";
import { projectHref } from "@/lib/routes";
import { PUBLIC_DIGEST_FILTER, PUBLIC_POST_FILTER } from "@/lib/publication";
import {
	isFreshWaveWindow,
	isMultiAgentTheme,
	themeAnchor,
	visibleWaves,
} from "@/lib/waves";

export const dynamic = "force-dynamic";

type Wave = {
	label: string;
	projects: { title: string; url: string; slug?: string; momentumScore: number }[];
	avgMomentum: number;
	count: number;
	agentCount?: number;
};

type WaveDigest = {
	weekId: string;
	waves?: Wave[];
	weekOf?: Date | string;
};

const windowFormatter = new Intl.DateTimeFormat("en", {
	month: "short",
	day: "numeric",
	timeZone: "UTC",
});

async function getWaves(weekId?: string) {
	const db = await getDb();
	const digestFilter = weekId
		? { ...PUBLIC_DIGEST_FILTER, weekId }
		: PUBLIC_DIGEST_FILTER;
	const digest = await db
		.collection<WaveDigest>("digests")
		.findOne(
			digestFilter,
			{ sort: { weekOf: -1 } },
		);
	const windowEnd = digest?.weekOf ? new Date(digest.weekOf) : null;
	if (!windowEnd || (!weekId && !isFreshWaveWindow(windowEnd, new Date()))) {
		return { waves: [], windowStart: null, windowEnd: null };
	}
	const windowStart = new Date(windowEnd.getTime() - 7 * 24 * 60 * 60 * 1000);
	const waves = digest?.waves ?? [];
	const projectUrls = [...new Set(waves.flatMap((wave) => wave.projects.map((p) => p.url)))];
	if (projectUrls.length === 0) return { waves: [], windowStart, windowEnd };
	const posts = await db
		.collection<{ agentHandle: string; project: { url: string } }>("posts")
		.find(
			publicationWindowMatch(
				{
					...PUBLIC_POST_FILTER,
					agentHandle: { $ne: "@weekly-digest" },
					"project.url": { $in: projectUrls },
				},
				windowStart,
				windowEnd,
			),
			{ projection: { _id: 0, agentHandle: 1, "project.url": 1 } },
		)
		.toArray();
	return { waves: visibleWaves(waves, posts), windowStart, windowEnd };
}

export const metadata = {
	title: "Hype Waves · HypeRadar",
	description: "Recent semantic themes across source-agent signals.",
};

export default async function WavesPage({
	searchParams,
}: {
	searchParams: Promise<{ week?: string }>;
}) {
	const { week } = await searchParams;
	const requestedWeek = week?.trim().slice(0, 32);
	const { waves, windowStart, windowEnd } = await getWaves(requestedWeek);
	const shared = waves.filter(isMultiAgentTheme);
	const otherThemes = waves.filter((wave) => !isMultiAgentTheme(wave));

	return (
		<main className="detail-page">
			<Link className="back-link" href="/">
				← All signals
			</Link>

			<header className="detail-header">
				<p className="eyebrow">
					{windowStart && windowEnd
						? `${requestedWeek ? `${requestedWeek} · ` : ""}Measured ${windowFormatter.format(windowStart)}–${windowFormatter.format(windowEnd)}`
						: "No current measured window"}
				</p>
				<h1>Hype waves</h1>
				<p>
					Projects from the measured seven-day window are grouped by semantic
					similarity. Multi-agent means separate source agents surfaced projects in
					the theme—not that performance movement is confirmed.
				</p>
			</header>

			<div className="detail-grid">
				<section className="surface">
					<h2>Multi-agent themes</h2>
					{shared.length === 0 ? (
						<p className="empty-panel">
							No current measured theme contains projects surfaced by multiple
							source agents.
						</p>
					) : (
						<div className="wave-list">
							{shared.map((wave) => (
								<article
									className="wave-card"
									id={themeAnchor(wave.label)}
									key={wave.label}
								>
									<div className="wave-card-head">
										<div>
											<p className="eyebrow">
												{wave.agentCount} source agents · {wave.count} projects
											</p>
											<h3>{wave.label}</h3>
										</div>
										<span className="score">
											Avg momentum {wave.avgMomentum.toFixed(1)} / 100
										</span>
									</div>
									{wave.projects.map((project) => (
										<Link
											className="project-row"
											key={project.url}
											href={projectHref(project)}
										>
											<span>{project.title}</span>
											<span>Momentum {project.momentumScore} / 100</span>
										</Link>
									))}
								</article>
							))}
						</div>
					)}
				</section>

				<aside>
					<section className="surface">
						<h2>How to read this</h2>
						<p className="lede">
							A wave is a semantic cluster, not a measured trend or prediction. Its
							score averages project momentum values; dossiers keep source units
							separate.
						</p>
						<Link className="next-link" href="/">
							Browse live signals →
						</Link>
					</section>

					{otherThemes.length > 0 ? (
						<section className="surface forming-signals">
							<h2>Other themes</h2>
							<p>Themes still missing either multiple projects or multiple source agents.</p>
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
