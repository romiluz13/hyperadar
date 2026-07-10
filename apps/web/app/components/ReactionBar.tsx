"use client";

import { useState, useEffect, useTransition } from "react";

type Props = {
	postId: string;
	initialLikes: number;
	initialShares: number;
	initialComments: number;
};

export function ReactionBar({
	postId,
	initialLikes,
	initialShares,
	initialComments,
}: Props) {
	const [liked, setLiked] = useState(false);
	const [likes, setLikes] = useState(initialLikes);
	const [shares, setShares] = useState(initialShares);
	const [comments] = useState(initialComments);
	const [pending, startTransition] = useTransition();

	useEffect(() => {
		// Check if already liked
		fetch(`/api/reactions?postId=${postId}`)
			.then((r) => r.json())
			.then((d) => {
				if (d.liked) setLiked(true);
			})
			.catch(() => {});
	}, [postId]);

	function toggleLike() {
		startTransition(async () => {
			const res = await fetch("/api/reactions", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ postId, type: "like" }),
			});
			const d = await res.json();
			if (d.liked !== undefined) {
				setLiked(d.liked);
				setLikes(d.counts.likes);
			}
		});
	}

	function share() {
		startTransition(async () => {
			await fetch("/api/reactions", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ postId, type: "share" }),
			});
			setShares((s) => s + 1);
			// copy link to clipboard
			if (typeof navigator !== "undefined" && navigator.clipboard) {
				navigator.clipboard.writeText(window.location.href).catch(() => {});
			}
		});
	}

	return (
		<div
			style={{
				display: "flex",
				gap: "1rem",
				alignItems: "center",
				marginTop: "0.5rem",
			}}
		>
			<button
				onClick={toggleLike}
				disabled={pending}
				style={{
					background: liked ? "#1a2a1a" : "transparent",
					border: liked ? "1px solid #2a4a2a" : "1px solid #333",
					borderRadius: 6,
					padding: "0.25rem 0.6rem",
					cursor: "pointer",
					color: liked ? "#4a4" : "#888",
					fontSize: "0.8rem",
					fontFamily: "inherit",
				}}
				aria-pressed={liked}
			>
				{liked ? "❤️" : "🤍"} {likes}
			</button>
			<span style={{ color: "#555", fontSize: "0.8rem" }}>💬 {comments}</span>
			<button
				onClick={share}
				disabled={pending}
				style={{
					background: "transparent",
					border: "1px solid #333",
					borderRadius: 6,
					padding: "0.25rem 0.6rem",
					cursor: "pointer",
					color: "#888",
					fontSize: "0.8rem",
					fontFamily: "inherit",
				}}
			>
				🔗 {shares}
			</button>
		</div>
	);
}
