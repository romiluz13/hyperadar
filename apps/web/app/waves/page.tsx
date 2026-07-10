import { getDb } from "@/lib/mongo";
import { urlToSlug } from "@/lib/slug";

export const dynamic = "force-dynamic";

type Wave = {
	label: string;
	projects: { title: string; url: string; slug: string; momentumScore: number; kind: string }[];
	avgMomentum: number;
	count: number;
};

async function getWaves() {
	const db = await getDb();
	// Get the latest digest with waves
	const digest = await db
		.collection("digests")
		.findOne({ waves: { $exists: true } }, { sort: { computedAt: -1 } });
	if (!digest?.waves) return [];
	return digest.waves as Wave[];
}

export async function generateMetadata() {
	return {
		title: "Hype Waves — HypeRadar",
		description: "This week's trending AI dev themes, clustered by semantic similarity.",
	};
}

export default async function WavesPage() {
	const waves = await getWaves();

	return (
		<main style={{ maxWidth: 800, margin: "0 auto", padding: "2rem 1.5rem" }}>
			<a href="/" style={{ color: "#666", fontSize: "0.85rem", textDecoration: "none" }}>← feed</a>

			<header style={{ marginTop: "1rem", marginBottom: "2rem" }}>
				<h1 style={{ fontSize: "2rem", margin: 0 }}>🌊 Hype Waves</h1>
				<p style={{ color: "#888", marginTop: "0.5rem" }}>
					This week&apos;s trending AI dev themes — clustered by MongoDB Vector Search + Grove.
				</p>
			</header>

			{waves.length === 0 ? (
				<p style={{ color: "#666" }}>No waves yet — run the hype wave clustering job to see this week&apos;s themes.</p>
			) : (
				<div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
					{waves.map((wave, i) => (
						<div
							key={`${wave.label}-${i}`}
							style={{
								border: "1px solid #222",
								borderRadius: 12,
								padding: "1.25rem 1.5rem",
								background: "#111",
							}}
						>
							<div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
								<h2 style={{ fontSize: "1.2rem", margin: 0, color: "#fafafa" }}>
									{wave.label}
								</h2>
								<span style={{ color: "#22c55e", fontSize: "0.85rem", fontWeight: 600 }}>
									avg {wave.avgMomentum}/100
								</span>
							</div>
							<p style={{ color: "#666", fontSize: "0.8rem", margin: "0.3rem 0 0.75rem" }}>
								{wave.count} project{wave.count !== 1 ? "s" : ""}
							</p>
							<ul style={{ listStyle: "none", padding: 0, display: "flex", flexDirection: "column", gap: "0.4rem" }}>
								{wave.projects.map((p) => (
									<li key={p.url}>
										<a
											href={`/project/${p.slug || urlToSlug(p.url)}`}
											style={{
												color: "#ccc",
												textDecoration: "none",
												display: "flex",
												justifyContent: "space-between",
												padding: "0.4rem 0.6rem",
												border: "1px solid #1a1a1a",
												borderRadius: 6,
												background: "#0a0a0a",
												fontSize: "0.9rem",
											}}
										>
											<span>{p.title}</span>
											<span style={{ color: "#22c55e", fontSize: "0.85rem" }}>{p.momentumScore}</span>
										</a>
									</li>
								))}
							</ul>
						</div>
					))}
				</div>
			)}

			<footer style={{ marginTop: "3rem", color: "#444", fontSize: "0.8rem", textAlign: "center" }}>
				Clustered by cosine similarity on MongoDB Vector Search embeddings · Labeled by Grove LLM
			</footer>
		</main>
	);
}
