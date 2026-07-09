"use client";

import { useState, useEffect, useTransition } from "react";

type Comment = { userName: string; text: string; createdAt: string };

export function Comments({
	postId,
	initialComments,
}: {
	postId: string;
	initialComments: number;
}) {
	const [comments, setComments] = useState<Comment[]>([]);
	const [open, setOpen] = useState(false);
	const [text, setText] = useState("");
	const [name, setName] = useState("");
	const [count, setCount] = useState(initialComments);
	const [pending, startTransition] = useTransition();

	useEffect(() => {
		if (!open) return;
		fetch(`/api/reactions/comments?postId=${postId}`)
			.then((r) => r.json())
			.then((d) => setComments(d.comments ?? []))
			.catch(() => {});
	}, [open, postId]);

	function submit(e: React.FormEvent) {
		e.preventDefault();
		if (!text.trim()) return;
		startTransition(async () => {
			const res = await fetch("/api/reactions/comments", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ postId, text, userName: name }),
			});
			if (res.ok) {
				setComments((cs) => [
					...cs,
					{
						userName: name || "anonymous",
						text: text.trim(),
						createdAt: new Date().toISOString(),
					},
				]);
				setCount((c) => c + 1);
				setText("");
			}
		});
	}

	return (
		<div style={{ marginTop: "0.5rem" }}>
			<button
				onClick={() => setOpen((o) => !o)}
				style={{
					background: "transparent",
					border: "none",
					color: "#666",
					cursor: "pointer",
					fontSize: "0.8rem",
					fontFamily: "inherit",
					padding: 0,
				}}
			>
				💬 {count} {open ? "−" : "+"}
			</button>
			{open && (
				<div
					style={{
						marginTop: "0.5rem",
						borderTop: "1px solid #222",
						paddingTop: "0.5rem",
					}}
				>
					{comments.length === 0 ? (
						<p
							style={{
								color: "#555",
								fontSize: "0.8rem",
								margin: "0 0 0.5rem",
							}}
						>
							No comments yet — start the debate.
						</p>
					) : (
						<ul
							style={{
								listStyle: "none",
								padding: 0,
								margin: "0 0 0.5rem",
								display: "flex",
								flexDirection: "column",
								gap: "0.4rem",
							}}
						>
							{comments.map((c, i) => (
								<li key={i} style={{ fontSize: "0.8rem", color: "#aaa" }}>
									<strong style={{ color: "#888" }}>{c.userName}</strong>{" "}
									<span style={{ color: "#555" }}>
										{new Date(c.createdAt).toLocaleDateString()}
									</span>
									<br />
									{c.text}
								</li>
							))}
						</ul>
					)}
					<form
						onSubmit={submit}
						style={{
							display: "flex",
							flexDirection: "column",
							gap: "0.4rem",
							marginTop: "0.5rem",
						}}
					>
						<input
							value={name}
							onChange={(e) => setName(e.target.value)}
							placeholder="your name (optional)"
							maxLength={50}
							style={{
								background: "#0a0a0a",
								border: "1px solid #333",
								borderRadius: 4,
								padding: "0.35rem 0.5rem",
								color: "#ccc",
								fontSize: "0.8rem",
								fontFamily: "inherit",
							}}
						/>
						<textarea
							value={text}
							onChange={(e) => setText(e.target.value)}
							placeholder="is the hype real?"
							maxLength={500}
							rows={2}
							style={{
								background: "#0a0a0a",
								border: "1px solid #333",
								borderRadius: 4,
								padding: "0.35rem 0.5rem",
								color: "#ccc",
								fontSize: "0.8rem",
								fontFamily: "inherit",
								resize: "vertical",
							}}
						/>
						<button
							type="submit"
							disabled={pending || !text.trim()}
							style={{
								alignSelf: "flex-start",
								background: "#1a2a1a",
								border: "1px solid #2a4a2a",
								borderRadius: 4,
								padding: "0.3rem 0.8rem",
								color: "#4a4",
								cursor: "pointer",
								fontSize: "0.8rem",
								fontFamily: "inherit",
							}}
						>
							{pending ? "posting..." : "comment"}
						</button>
					</form>
				</div>
			)}
		</div>
	);
}
