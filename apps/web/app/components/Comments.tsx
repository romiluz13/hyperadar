"use client";

import { useEffect, useId, useRef, useState, useTransition } from "react";

import {
	type CommentOperation,
	operationForComment,
} from "@/lib/commentOperation";
import { commentFailureMessage } from "@/lib/commentResponse";

type Comment = {
	operationId?: string;
	userName: string;
	text: string;
	createdAt: string;
};

type CommentLoadState = "idle" | "loading" | "success" | "error";

const dateFormatter = new Intl.DateTimeFormat("en", {
	month: "short",
	day: "numeric",
});

export function Comments({
	postId,
	initialComments,
}: {
	postId: string;
	initialComments: number;
}) {
	const threadId = useId();
	const [comments, setComments] = useState<Comment[]>([]);
	const [open, setOpen] = useState(false);
	const [text, setText] = useState("");
	const [name, setName] = useState("");
	const [count, setCount] = useState(initialComments);
	const [error, setError] = useState("");
	const [loadError, setLoadError] = useState("");
	const [loadState, setLoadState] = useState<CommentLoadState>("idle");
	const [retryToken, setRetryToken] = useState(0);
	const [pending, startTransition] = useTransition();
	const commentInput = useRef<HTMLTextAreaElement>(null);
	const pendingOperation = useRef<CommentOperation | null>(null);
	const commentLabel = count === 1 ? "1 comment" : `${count} comments`;

	useEffect(() => {
		if (!open) return;
		const controller = new AbortController();
		let active = true;
		setLoadError("");
		setLoadState("loading");
		fetch(`/api/reactions/comments?postId=${postId}`, {
			signal: controller.signal,
		})
			.then((response) => {
				if (!response.ok) throw new Error("Comment request failed");
				return response.json();
			})
			.then((data) => {
				if (active) {
					setComments(data.comments ?? []);
					setLoadState("success");
				}
			})
			.catch((fetchError: unknown) => {
				if (
					fetchError instanceof DOMException &&
					fetchError.name === "AbortError"
				) {
					return;
				}
				if (active) {
					setLoadError("Comments could not load. Your existing thread is unchanged.");
					setLoadState("error");
				}
			});
		return () => {
			active = false;
			controller.abort();
		};
	}, [open, postId, retryToken]);

	function toggleThread() {
		if (open) {
			setOpen(false);
			return;
		}
		setLoadState("loading");
		setOpen(true);
	}

	function retryLoadingComments() {
		setLoadState("loading");
		setRetryToken((current) => current + 1);
	}

	function submit(event: React.FormEvent) {
		event.preventDefault();
		if (!text.trim()) {
			setError("Write your take before posting.");
			commentInput.current?.focus();
			return;
		}
		setError("");
		const normalizedText = text.trim();
		const normalizedName = name.trim().slice(0, 50);
		const operation = operationForComment(
			pendingOperation.current,
			normalizedText,
			normalizedName,
		);
		pendingOperation.current = operation;
		startTransition(async () => {
			let responseFailure = "";
			try {
				const response = await fetch("/api/reactions/comments", {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({
						postId,
						text: normalizedText,
						userName: normalizedName,
						operationId: operation.operationId,
					}),
				});
				if (!response.ok) {
					if (response.status === 409) pendingOperation.current = null;
					responseFailure = commentFailureMessage(
						response.status,
						response.headers.get("Retry-After"),
					);
					throw new Error("Comment response rejected");
				}
				const data = await response.json();
				setComments((current) =>
					current.some(
						(comment) => comment.operationId === operation.operationId,
					)
						? current
						: [...current, data.comment],
				);
				setCount(data.counts.comments);
				setText("");
				pendingOperation.current = null;
			} catch {
				setError(
					responseFailure || "Comments are unavailable right now. Try again later.",
				);
			}
		});
	}

	return (
		<div className="comments">
			<button
				className="comment-toggle"
				type="button"
				onClick={toggleThread}
				aria-expanded={open}
				aria-controls={threadId}
				aria-label={
					open ? "Close comments" : count > 0 ? `Open ${commentLabel}` : "Discuss"
				}
			>
				<span aria-hidden="true">◌</span>{" "}
				{open ? "Close comments" : count > 0 ? commentLabel : "Discuss"}
			</button>

			{open ? (
				<div
					className="comment-thread"
					id={threadId}
					aria-busy={loadState === "loading"}
				>
					<div className="comment-load-status" aria-live="polite">
						{loadState === "loading" || loadState === "idle" ? (
							<p className="comment-empty">Loading conversation…</p>
						) : loadState === "error" ? (
							<div className="comment-load-error">
								<p>{loadError}</p>
								<button type="button" onClick={retryLoadingComments}>
									Retry loading comments
								</button>
							</div>
						) : null}
					</div>

					{loadState !== "success" ? null : comments.length === 0 ? (
						<p className="comment-empty">No comments yet. Start the debate.</p>
					) : (
						<ul className="comment-list">
							{comments.map((comment, index) => (
								<li key={`${comment.createdAt}-${index}`}>
									<div className="comment-byline">
										<strong>{comment.userName}</strong>
										<time dateTime={comment.createdAt}>
											{dateFormatter.format(new Date(comment.createdAt))}
										</time>
									</div>
									<p>{comment.text}</p>
								</li>
							))}
						</ul>
					)}

					{loadState === "success" ? (
						<>
							<form className="comment-form" onSubmit={submit}>
								<p className="comment-note">
									Comments are public. No account needed; name is optional.
								</p>
								<label htmlFor={`${threadId}-name`}>Name (optional)</label>
								<input
									id={`${threadId}-name`}
									name="display-name"
									value={name}
									onChange={(event) => setName(event.target.value)}
									placeholder="Ada…"
									maxLength={50}
									autoComplete="off"
									spellCheck={false}
								/>

								<label htmlFor={`${threadId}-comment`}>Your take</label>
								<textarea
									ref={commentInput}
									id={`${threadId}-comment`}
									name="comment"
									value={text}
									onChange={(event) => setText(event.target.value)}
									placeholder="What evidence changes the verdict?…"
									maxLength={500}
									rows={3}
									autoComplete="off"
								/>

								<button type="submit" disabled={pending}>
									{pending ? "Posting…" : "Post comment"}
								</button>
							</form>

							<p className="form-error" aria-live="polite">
								{error}
							</p>
						</>
					) : null}
				</div>
			) : null}
		</div>
	);
}
