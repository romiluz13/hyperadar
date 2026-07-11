import { getDb } from "@/lib/mongo";
import { urlToSlug } from "@/lib/slug";

export const dynamic = "force-dynamic";

type Digest = {
	weekId: string;
	weekOf: string;
	waves?: {
		label: string;
		projects: {
			title: string;
			url: string;
			slug: string;
			momentumScore: number;
		}[];
		avgMomentum: number;
		count: number;
	}[];
	summary?: string;
};

async function getDigest(weekId: string) {
	const db = await getDb();
	const digest = await db.collection<Digest>("digests").findOne({ weekId });
	if (!digest) return null;

	// Fetch the week's top posts by category for the digest sections
	const weekStart = digest.weekOf ? new Date(digest.weekOf) : new Date();
	const weekAgo = new Date(weekStart.getTime() - 7 * 24 * 60 * 60 * 1000);
	const posts = await db
		.collection("posts")
		.find({
			postedAt: { $gte: weekAgo, $lte: weekStart },
		})
		.sort({ rankScore: -1 })
		.limit(20)
		.toArray();

	// Categorize by agent + kind
	const breakouts = posts
		.filter((p) => p.agentHandle === "@github-radar")
		.slice(0, 3);
	const hotThreads = posts
		.filter((p) => p.agentHandle === "@reddit-pulse")
		.slice(0, 3);
	const hiddenGems = posts
		.filter((p) => p.agentHandle === "@hidden-gems")
		.slice(0, 3);

	return { ...digest, breakouts, hotThreads, hiddenGems };
}

export async function generateMetadata({
	params,
}: {
	params: Promise<{ week: string }>;
}) {
	const { week } = await params;
	return {
		title: `Weekly Digest ${week} — HypeRadar`,
		description: `HypeRadar weekly digest for ${week}`,
	};
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
			<main style={{ maxWidth: 640, margin: "0 auto", padding: "2rem 1.5rem" }}>
				<h1>Digest not found</h1>
				<p style={{ color: "#888" }}>No digest for {week}.</p>
				<a href="/" style={{ color: "#3b82f6" }}>
					← back to the feed
				</a>
			</main>
		);
	}

	return (
		<main style={{ maxWidth: 800, margin: "0 auto", padding: "2rem 1.5rem" }}>
			<a
				href="/"
				style={{ color: "#666", fontSize: "0.85rem", textDecoration: "none" }}
			>
				← feed
			</a>

			<header style={{ marginTop: "1rem", marginBottom: "2rem" }}>
				<h1 style={{ fontSize: "2rem", margin: 0 }}>
					📰 Weekly Digest — {digest.weekId}
				</h1>
				{digest.summary && (
					<p style={{ color: "#aaa", marginTop: "0.75rem", fontSize: "1rem" }}>
						{digest.summary}
					</p>
				)}
			</header>

			{digest.waves && digest.waves.length > 0 ? (
				<>
					<h2
						style={{
							fontSize: "0.9rem",
							color: "#888",
							textTransform: "uppercase",
							letterSpacing: "0.05em",
						}}
					>
						Hype waves this week
					</h2>
					<div
						style={{
							display: "flex",
							flexDirection: "column",
							gap: "1rem",
							marginTop: "0.5rem",
						}}
					>
						{digest.waves.map((wave, i) => (
							<div
								key={`${wave.label}-${i}`}
								style={{
									border: "1px solid #222",
									borderRadius: 8,
									padding: "0.75rem 1rem",
									background: "#111",
								}}
							>
								<div
									style={{ display: "flex", justifyContent: "space-between" }}
								>
									<span style={{ color: "#fafafa", fontWeight: 600 }}>
										🌊 {wave.label}
									</span>
									<span style={{ color: "#22c55e", fontSize: "0.85rem" }}>
										avg {wave.avgMomentum}
									</span>
								</div>
								<ul
									style={{ listStyle: "none", padding: 0, marginTop: "0.5rem" }}
								>
									{wave.projects.map((p) => (
										<li key={p.url}>
											<a
												href={`/project/${p.slug || urlToSlug(p.url)}`}
												style={{
													color: "#888",
													textDecoration: "none",
													fontSize: "0.85rem",
												}}
											>
												{p.title} →
											</a>
										</li>
									))}
								</ul>
							</div>
						))}
					</div>
				</>
			) : (
				<p style={{ color: "#666" }}>No hype waves computed for this week.</p>
			)}

			{/* Top breakouts / hot threads / hidden gems */}
			{(digest.breakouts?.length > 0 ||
				digest.hotThreads?.length > 0 ||
				digest.hiddenGems?.length > 0) && (
				<div
					style={{
						marginTop: "2rem",
						display: "flex",
						flexDirection: "column",
						gap: "1.5rem",
					}}
				>
					{digest.breakouts && digest.breakouts.length > 0 && (
						<section>
							<h2
								style={{
									fontSize: "0.9rem",
									color: "#888",
									textTransform: "uppercase",
									letterSpacing: "0.05em",
								}}
							>
								🔥 Top breakouts
							</h2>
							<ul
								style={{
									listStyle: "none",
									padding: 0,
									marginTop: "0.5rem",
									display: "flex",
									flexDirection: "column",
									gap: "0.4rem",
								}}
							>
								{digest.breakouts.map((p) => (
									<li
										key={String(p._id)}
										style={{
											color: "#aaa",
											fontSize: "0.85rem",
											padding: "0.3rem 0",
										}}
									>
										{p.project.title} — {p.body.slice(0, 80)}
									</li>
								))}
							</ul>
						</section>
					)}
					{digest.hotThreads && digest.hotThreads.length > 0 && (
						<section>
							<h2
								style={{
									fontSize: "0.9rem",
									color: "#888",
									textTransform: "uppercase",
									letterSpacing: "0.05em",
								}}
							>
								💬 Hot threads
							</h2>
							<ul
								style={{
									listStyle: "none",
									padding: 0,
									marginTop: "0.5rem",
									display: "flex",
									flexDirection: "column",
									gap: "0.4rem",
								}}
							>
								{digest.hotThreads.map((p) => (
									<li
										key={String(p._id)}
										style={{
											color: "#aaa",
											fontSize: "0.85rem",
											padding: "0.3rem 0",
										}}
									>
										{p.project.title} — {p.body.slice(0, 80)}
									</li>
								))}
							</ul>
						</section>
					)}
					{digest.hiddenGems && digest.hiddenGems.length > 0 && (
						<section>
							<h2
								style={{
									fontSize: "0.9rem",
									color: "#888",
									textTransform: "uppercase",
									letterSpacing: "0.05em",
								}}
							>
								🔍 Hidden gems
							</h2>
							<ul
								style={{
									listStyle: "none",
									padding: 0,
									marginTop: "0.5rem",
									display: "flex",
									flexDirection: "column",
									gap: "0.4rem",
								}}
							>
								{digest.hiddenGems.map((p) => (
									<li
										key={String(p._id)}
										style={{
											color: "#aaa",
											fontSize: "0.85rem",
											padding: "0.3rem 0",
										}}
									>
										{p.project.title} — {p.body.slice(0, 80)}
									</li>
								))}
							</ul>
						</section>
					)}
				</div>
			)}
		</main>
	);
}
