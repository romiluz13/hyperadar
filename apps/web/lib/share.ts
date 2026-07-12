export function absoluteShareUrl(permalink: string, origin: string): string {
	return new URL(permalink, origin).href;
}
