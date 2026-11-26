type Themed = { topics?: string[] };

const GENERIC_TOPICS = new Set([
	"ai",
	"github",
	"reddit",
	"repo",
	"thread",
	"video",
	"youtube",
]);

function topicCounts(items: Themed[]): Map<string, number> {
	const counts = new Map<string, number>();
	for (const item of items) {
		for (const topic of item.topics ?? []) {
			if (!GENERIC_TOPICS.has(topic.toLowerCase())) {
				counts.set(topic, (counts.get(topic) ?? 0) + 1);
			}
		}
	}
	return counts;
}

export function currentThemes(items: Themed[]): [string, number][] {
	return [...topicCounts(items).entries()]
		.sort((first, second) => second[1] - first[1])
		.slice(0, 4);
}

export function selectTheme(
	items: Themed[],
	requested: string | null | undefined,
): string | undefined {
	const label = requested?.trim().slice(0, 80);
	if (!label) return undefined;
	return (
		[...topicCounts(items).keys()].find(
			(topic) => topic.toLowerCase() === label.toLowerCase(),
		) ?? label
	);
}

export function filterByTheme<T extends Themed>(
	items: T[],
	theme: string | undefined,
): T[] {
	if (!theme) return items;
	return items.filter((item) =>
		(item.topics ?? []).some(
			(topic) => topic.toLowerCase() === theme.toLowerCase(),
		),
	);
}
