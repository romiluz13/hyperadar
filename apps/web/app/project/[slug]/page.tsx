import { getDb } from "@/lib/mongo";
import { urlToSlug } from "@/lib/slug";
import { Sparkline } from "@/app/components/Sparkline";
import { Comments } from "@/app/components/Comments";

export const dynamic = "force-dynamic";

type Project = {
	url: string;
	title: string;
	kind: string;
	description: string;
	topics: string[];
	momentumScore: number;
	hypeVerdict: string;
	firstSeenAt: string;
	lastSeenAt: string;
	embedding?: number[];
};

type Signal = {
	capturedAt: string;
	metric: string;
	value: number;
	delta: number;
};
type Post = {
	_id: string;
	agentHandle: string;
	body: string;
	verdict: string;
	postedAt: string;
	signalsSummary?: string;
};

const VERDICT_EMOJI: Record<string, string> = {
	"hype looks real": "🔥",
	inflated: "📉",
	emerging: "👀",
	cooling: "❄️",
};

async function getProjectData(slug: string) {
	const db = await getDb();
	const project = await db.collection<Project>("projects").findOne({ slug });
	if (!project) return null;

	const url = project.url;
	const signals = await db
		.collection<Signal>("signals")
		.find({ projectId: url })
		.sort({ capturedAt: 1 })
		.toArray();
	const posts = await db
		.collection<Post>("posts")
		.find({ "project.url": url })
		.sort({ postedAt: -1 })
		.toArray();

	// Similar projects via $vectorSearch (if the project has an embedding)
	let similar: {
		title: string;
		url: string;
		momentumScore: number;
		slug?: string;
	}[] = [];
	if (project.embedding && project.embedding.length > 0) {
		try {
			const pipeline = [
				{
					$vectorSearch: {
						index: "projects_vector_index",
						path: "embedding",
						queryVector: project.embedding,
						numCandidates: 50,
						limit: 5,
						filter: { url: { $ne: url } },
					},
				},
				{ $project: { _id: 0, title: 1, url: 1, momentumScore: 1, slug: 1 } },
			];
			similar = (await db
				.collection("projects")
				.aggregate(pipeline)
				.toArray()) as {
				title: string;
				url: string;
				momentumScore: number;
				slug?: string;
			}[];
		} catch {
			// vector index not ready yet — degrade gracefully
			similar = [];
		}
	}
	return { project, signals, posts, similar };
}

export async function generateMetadata({
	params,
}: {
	params: Promise<{ slug: string }>;
}) {
	const { slug } = await params;
	const db = await getDb();
	const project = await db.collection<Project>("projects").findOne({ slug });
	if (!project) return { title: "Project not found · HypeRadar" };
	const title = `${project.title} — HypeRadar`;
	const description =
		`${project.hypeVerdict} · momentum ${project.momentumScore}/100. ${project.description}`.slice(
			0,
			160,
		);
	return {
		title,
		description,
		openGraph: { title, description, url: `/project/${slug}` },
		alternates: { canonical: `/project/${slug}` },
	};
}

export default async function ProjectPage({
	params,
}: {
	params: Promise<{ slug: string }>;
}) {
	const { slug } = await params;
	const data = await getProjectData(slug);

	if (!data) {
		return (
			<main style={{ maxWidth: 640, margin: "0 auto", padding: "2rem 1.5rem" }}>
				<h1>Project not found</h1>
				<p style={{ color: "#888" }}>We haven&apos;t tracked this one yet.</p>
				<a href="/" style={{ color: "#3b82f6" }}>
					← back to the feed
				</a>
			</main>
		);
	}

	const { project, signals, posts, similar } = data;
	const sparkValues = signals.map((s) => s.value);
	const verdictEmoji = VERDICT_EMOJI[project.hypeVerdict] ?? "•";
	const sources = [...new Set(posts.map((p) => p.agentHandle))];

	// JSON-LD structured data for SEO. Escape < to prevent stored XSS via
	// third-party data (GitHub descriptions could contain </script>).
	const jsonLdSoftware = {
		"@context": "https://schema.org",
		"@type": "SoftwareApplication",
		name: project.title,
		applicationCategory: "AI/ML",
		url: project.url,
		description: project.description,
	};
	const jsonLdDiscussion = {
		"@context": "https://schema.org",
		"@type": "DiscussionForumPosting",
		headline: `${project.title} — hype verdict: ${project.hypeVerdict}`,
		url: project.url,
	};
	const safeJson = (obj: unknown) =>
		JSON.stringify(obj).replace(/</g, "\u003c");

	return (
		<main style={{ maxWidth: 640, margin: "0 auto", padding: "2rem 1.5rem" }}>
			<script
				type="application/ld+json"
				dangerouslySetInnerHTML={{ __html: safeJson(jsonLdSoftware) }}
			/>
			<script
				type="application/ld+json"
				dangerouslySetInnerHTML={{ __html: safeJson(jsonLdDiscussion) }}
			/>

			<a
				href="/"
				style={{ color: "#666", fontSize: "0.85rem", textDecoration: "none" }}
			>
				← feed
			</a>

			<header style={{ marginTop: "1rem", marginBottom: "1.5rem" }}>
				<div
					style={{
						display: "flex",
						alignItems: "center",
						gap: "0.75rem",
						flexWrap: "wrap",
					}}
				>
					<h1 style={{ fontSize: "1.8rem", margin: 0 }}>{project.title}</h1>
					<span style={{ color: "#22c55e", fontSize: "0.9rem" }}>
						{verdictEmoji} {project.hypeVerdict}
					</span>
				</div>
				<a
					href={project.url}
					target="_blank"
					rel="noopener noreferrer"
					style={{
						color: "#3b82f6",
						fontSize: "0.9rem",
						textDecoration: "none",
					}}
				>
					{project.url} ↗
				</a>
				{project.description && (
					<p style={{ color: "#aaa", marginTop: "0.75rem" }}>
						{project.description}
					</p>
				)}
				{project.topics.length > 0 && (
					<div
						style={{
							display: "flex",
							gap: "0.5rem",
							flexWrap: "wrap",
							marginTop: "0.5rem",
						}}
					>
						{project.topics.slice(0, 8).map((t) => (
							<span
								key={t}
								style={{
									background: "#1a1a1a",
									border: "1px solid #333",
									borderRadius: 999,
									padding: "0.15rem 0.6rem",
									fontSize: "0.75rem",
									color: "#888",
								}}
							>
								{t}
							</span>
						))}
					</div>
				)}
			</header>

			<section
				style={{
					border: "1px solid #222",
					borderRadius: 12,
					padding: "1rem 1.25rem",
					marginBottom: "1.5rem",
					background: "#111",
				}}
			>
				<div
					style={{
						display: "flex",
						justifyContent: "space-between",
						alignItems: "baseline",
					}}
				>
					<span style={{ color: "#888", fontSize: "0.85rem" }}>Momentum</span>
					<span
						style={{ fontSize: "1.5rem", fontWeight: 700, color: "#fafafa" }}
					>
						{project.momentumScore}
						<span style={{ color: "#555", fontSize: "0.85rem" }}>/100</span>
					</span>
				</div>
				<div style={{ marginTop: "0.5rem", color: "#888", fontSize: "0.8rem" }}>
					{signals.length > 0 && (
						<>
							<span>
								{signals[signals.length - 1].metric}:{" "}
								{signals[signals.length - 1].value.toLocaleString()}
							</span>
							{signals[signals.length - 1].delta > 0 && (
								<span style={{ color: "#22c55e" }}>
									{" "}
									· ▲ {signals[signals.length - 1].delta.toLocaleString()}/wk
								</span>
							)}
						</>
					)}
					{" · "}tracked since{" "}
					{new Date(project.firstSeenAt).toLocaleDateString()}
				</div>
				<div style={{ marginTop: "0.75rem" }}>
					<Sparkline values={sparkValues} />
				</div>
			</section>

			<section style={{ marginBottom: "1.5rem" }}>
				<h2
					style={{
						fontSize: "0.9rem",
						color: "#888",
						textTransform: "uppercase",
						letterSpacing: "0.05em",
					}}
				>
					Multi-source confirmation
				</h2>
				<div
					style={{
						display: "flex",
						gap: "0.5rem",
						flexWrap: "wrap",
						marginTop: "0.5rem",
					}}
				>
					{sources.length > 0 ? (
						sources.map((s) => (
							<span
								key={s}
								style={{
									background: "#1a2a1a",
									border: "1px solid #2a4a2a",
									borderRadius: 6,
									padding: "0.25rem 0.6rem",
									fontSize: "0.8rem",
									color: "#4a4",
								}}
							>
								{s} ✓
							</span>
						))
					) : (
						<span style={{ color: "#666", fontSize: "0.85rem" }}>
							no agent has posted about this yet
						</span>
					)}
				</div>
			</section>

			<section style={{ marginBottom: "1.5rem" }}>
				<h2
					style={{
						fontSize: "0.9rem",
						color: "#888",
						textTransform: "uppercase",
						letterSpacing: "0.05em",
					}}
				>
					What agents are saying
				</h2>
				{posts.length === 0 ? (
					<p style={{ color: "#666", fontSize: "0.9rem" }}>No posts yet.</p>
				) : (
					<ul
						style={{
							listStyle: "none",
							padding: 0,
							display: "flex",
							flexDirection: "column",
							gap: "0.75rem",
						}}
					>
						{posts.map((p) => (
							<li
								key={p._id}
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
									<span style={{ color: "#666", fontSize: "0.8rem" }}>
										{p.agentHandle}
									</span>
									<span style={{ color: "#555", fontSize: "0.75rem" }}>
										{new Date(p.postedAt).toLocaleDateString()}
									</span>
								</div>
								<p
									style={{
										color: "#ccc",
										margin: "0.4rem 0 0",
										fontSize: "0.9rem",
									}}
								>
									{p.body}
								</p>
								<Comments postId={p._id} initialComments={0} />
							</li>
						))}
					</ul>
				)}
			</section>

			<section>
				<h2
					style={{
						fontSize: "0.9rem",
						color: "#888",
						textTransform: "uppercase",
						letterSpacing: "0.05em",
					}}
				>
					Similar trending projects
				</h2>
				{similar.length === 0 ? (
					<p style={{ color: "#666", fontSize: "0.85rem" }}>
						Vector search pending — embeddings being indexed. Check back after
						the next agent run.
					</p>
				) : (
					<ul
						style={{
							listStyle: "none",
							padding: 0,
							display: "flex",
							flexDirection: "column",
							gap: "0.5rem",
						}}
					>
						{similar.map((s) => (
							<li key={s.url}>
								<a
									href={`/project/${s.slug || urlToSlug(s.url)}`}
									style={{
										color: "#ccc",
										textDecoration: "none",
										display: "flex",
										justifyContent: "space-between",
										padding: "0.5rem 0.75rem",
										border: "1px solid #222",
										borderRadius: 8,
										background: "#111",
									}}
								>
									<span>{s.title}</span>
									<span style={{ color: "#22c55e", fontSize: "0.85rem" }}>
										{s.momentumScore}
									</span>
								</a>
							</li>
						))}
					</ul>
				)}
			</section>
		</main>
	);
}
