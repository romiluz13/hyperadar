export type CommentOperation = {
	operationId: string;
	text: string;
	userName: string;
};

export function operationForComment(
	current: CommentOperation | null,
	text: string,
	userName: string,
	createOperationId: () => string = () => crypto.randomUUID(),
): CommentOperation {
	if (current?.text === text && current.userName === userName) return current;
	return { operationId: createOperationId(), text, userName };
}
