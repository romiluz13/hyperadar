"use client";

import { useEffect, useId, useState, useTransition } from "react";

type Comment = { userName: string; text: string; createdAt: string };

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
	const [loading, setLoading] = useState(false);
	const [pending, startTransition] = useTransition();

	useEffect(() => {
		if (!open) return;
		const controller = new AbortController();
		let active = true;
		setError("");
		setLoading(true);
		fetch(`/api/reactions/comments?postId=${postId}`, {
			signal: controller.signal,
		})
			.then((response) => {
				if (!response.ok) throw new Error("Comment request failed");
				return response.json();
			})
			.then((data) => {
				if (active) setComments(data.comments ?? []);
			})
			.catch((fetchError: unknown) => {
				if (
					fetchError instanceof DOMException &&
					fetchError.name === "AbortError"
				) {
					return;
				}
				if (active) {
					setError("Comments could not load. Close this thread and try again.");
				}
			})
			.finally(() => {
				if (active) setLoading(false);
			});
		return () => {
			active = false;
			controller.abort();
		};
	}, [open, postId]);

	function submit(event: React.FormEvent) {
		event.preventDefault();
		if (!text.trim()) return;
		setError("");
		startTransition(async () => {
			try {
				const response = await fetch("/api/reactions/comments", {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ postId, text, userName: name }),
				});
				if (!response.ok) throw new Error("Comment submission failed");
				setComments((current) => [
					...current,
					{
						userName: name.trim() || "anonymous",
						text: text.trim(),
						createdAt: new Date().toISOString(),
					},
				]);
				setCount((current) => current + 1);
				setText("");
			} catch {
				setError("Your comment was not posted. Check the text and try again.");
			}
		});
	}

	return (
		<div className="comments">
			<button
				className="comment-toggle"
				type="button"
				onClick={() => setOpen((current) => !current)}
				aria-expanded={open}
				aria-controls={threadId}
			>
				<span aria-hidden="true">◌</span> {count} {open ? "Close" : "Discuss"}
			</button>

			{open ? (
				<div
					className="comment-thread"
					id={threadId}
					aria-busy={loading}
				>
					{loading ? (
						<p className="comment-empty">Loading conversation…</p>
					) : comments.length === 0 ? (
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

					<form className="comment-form" onSubmit={submit}>
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
							id={`${threadId}-comment`}
							name="comment"
							value={text}
							onChange={(event) => setText(event.target.value)}
							placeholder="What evidence changes the verdict?…"
							maxLength={500}
							rows={3}
						/>

						<button type="submit" disabled={pending || !text.trim()}>
							{pending ? "Posting…" : "Post comment"}
						</button>
					</form>

					<p className="form-error" aria-live="polite">
						{error}
					</p>
				</div>
			) : null}
		</div>
	);
}
