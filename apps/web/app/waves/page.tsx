import Link from "next/link";

import { getDb } from "@/lib/mongo";
import { projectHref } from "@/lib/routes";

export const dynamic = "force-dynamic";

type Wave = {
	label: string;
	projects: { title: string; url: string; slug?: string; momentumScore: number }[];
	avgMomentum: number;
	count: number;
};

async function getWaves() {
	const db = await getDb();
	const digest = await db
		.collection<{ waves?: Wave[] }>("digests")
		.findOne({}, { sort: { computedAt: -1 } });
	return digest?.waves ?? [];
}

export const metadata = {
	title: "Hype Waves · HypeRadar",
	description: "Independent AI-dev signals that are beginning to move together.",
};

export default async function WavesPage() {
	const waves = await getWaves();
	const confirmed = waves.filter((wave) => wave.count > 1);
	const forming = waves.filter((wave) => wave.count === 1);

	return (
		<main className="detail-page">
			<Link className="back-link" href="/">
				← All signals
			</Link>

			<header className="detail-header">
				<p className="eyebrow">The shared pattern</p>
				<h1>Hype waves</h1>
				<p>
					A wave starts when separate projects move in the same direction. Open
					one to see the evidence, the agents, and the surprising next signal.
				</p>
			</header>

			<div className="detail-grid">
				<section className="surface">
					<h2>Confirmed convergence</h2>
					{confirmed.length === 0 ? (
						<p className="empty-panel">
							No wave has independent confirmation yet. The forming signals are
							early, not proven.
						</p>
					) : (
						<div className="wave-list">
							{confirmed.map((wave) => (
								<article className="wave-card" key={wave.label}>
									<div className="wave-card-head">
										<div>
											<p className="eyebrow">
												{wave.count} projects moving together
											</p>
											<h2>{wave.label}</h2>
										</div>
										<span className="score">
											{wave.avgMomentum.toFixed(1)} / 100
										</span>
									</div>
									{wave.projects.map((project) => (
										<Link
											className="project-row"
											key={project.url}
											href={projectHref(project)}
										>
											<span>{project.title}</span>
											<span>{project.momentumScore}</span>
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
							A wave is a cluster, not a prediction. Agreement raises confidence;
							the project dossier shows whether the evidence deserves it.
						</p>
						<Link className="next-link" href="/">
							Browse live signals →
						</Link>
					</section>

					{forming.length > 0 ? (
						<section className="surface forming-signals">
							<h2>Forming signals</h2>
							<p>Moving quickly, still waiting for an independent echo.</p>
							<ul>
								{forming.slice(0, 8).map((wave) => (
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
