export function operationForShare(
	current: string | null,
	createOperationId: () => string = () => crypto.randomUUID(),
): string {
	return current ?? createOperationId();
}
