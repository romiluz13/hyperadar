export function themeAnchor(label: string): string {
	const slug = label
		.trim()
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-|-$/g, "");
	return `theme-${slug || "untitled"}`;
}

export function isMultiAgentTheme(wave: {
	agentCount?: number;
	count: number;
}) {
	return (wave.agentCount ?? 0) > 1 && wave.count > 1;
}

const MAX_WAVE_AGE_MS = 8 * 24 * 60 * 60 * 1000;

export function isFreshWaveWindow(weekOf: Date, now: Date): boolean {
	const age = now.getTime() - weekOf.getTime();
	return Number.isFinite(age) && age >= 0 && age <= MAX_WAVE_AGE_MS;
}

type WaveProject = { url: string; momentumScore: number };
type WaveShape<Project extends WaveProject> = {
	projects: Project[];
	avgMomentum: number;
	count: number;
	agentCount?: number;
	agentHandles?: string[];
};
type PublishedWavePost = { agentHandle: string; project: { url: string } };

export function visibleWaves<
	Project extends WaveProject,
	Wave extends WaveShape<Project>,
>(waves: Wave[], posts: PublishedWavePost[]): Wave[] {
	const handlesByProject = new Map<string, Set<string>>();
	for (const post of posts) {
		const handles = handlesByProject.get(post.project.url) ?? new Set<string>();
		handles.add(post.agentHandle);
		handlesByProject.set(post.project.url, handles);
	}
	return waves.flatMap((wave) => {
		const projects = wave.projects.filter((project) =>
			handlesByProject.has(project.url),
		);
		if (projects.length === 0) return [];
		const agentHandles = [
			...new Set(
				projects.flatMap((project) => [
					...(handlesByProject.get(project.url) ?? []),
				]),
			),
		].sort();
		return [
			{
				...wave,
				projects,
				count: projects.length,
				agentHandles,
				agentCount: agentHandles.length,
				avgMomentum:
					Math.round(
						(projects.reduce(
							(total, project) => total + project.momentumScore,
							0,
						) /
							projects.length) *
							10,
					) / 10,
			} as Wave,
		];
	});
}
