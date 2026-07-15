import { sourceFamily } from "./feed.ts";

type SignalPoint = {
	source?: string;
	metric: string;
	value: number;
	evidenceUrl?: string;
};

export function comparableSignalSeries<T extends SignalPoint>(signals: T[]): T[] {
	const latest = signals.at(-1);
	if (!latest) return [];
	const latestSource = sourceFamily(latest.source) ?? "unknown";
	return signals.filter(
		(signal) =>
			(sourceFamily(signal.source) ?? "unknown") === latestSource &&
			signal.metric === latest.metric,
	);
}

export function evidenceLocator(
	signal: SignalPoint,
	projectUrl: string,
): string | undefined {
	if (signal.evidenceUrl) return signal.evidenceUrl;
	const family = sourceFamily(signal.source);
	if (family === "github" || family === "youtube") return projectUrl;
	if (
		family === "reddit" &&
		/^https?:\/\/(?:www\.)?reddit\.com\/r\/[^/]+\/comments\/[^/]+(?:[/?#]|$)/i.test(
			projectUrl,
		)
	) {
		return projectUrl;
	}
	return undefined;
}
