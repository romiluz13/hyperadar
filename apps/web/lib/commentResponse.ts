export function commentFailureMessage(
	status: number,
	retryAfter: string | null,
): string {
	if (status === 429) {
		const seconds = Number.parseInt(retryAfter ?? "", 10);
		const minutes = Number.isFinite(seconds) ? Math.max(1, Math.ceil(seconds / 60)) : 10;
		return `Too many comments from this network. Try again in ${minutes} ${minutes === 1 ? "minute" : "minutes"}.`;
	}
	if (status === 400) return "Check the comment text and try again.";
	if (status === 404) return "This signal is no longer open for comments.";
	if (status === 409) {
		return "This comment changed while it was being retried. Review it and post again.";
	}
	return "Comments are unavailable right now. Try again later.";
}
