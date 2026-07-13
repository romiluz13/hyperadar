export function reactionLabel(count: number, emptyLabel: string): string {
	return count > 0 ? String(count) : emptyLabel;
}
