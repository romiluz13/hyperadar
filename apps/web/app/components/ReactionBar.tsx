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
		<div className="reaction-bar" aria-label="Post reactions">
			<button
				onClick={toggleLike}
				disabled={pending}
				aria-pressed={liked}
				aria-label={`${liked ? "Unlike" : "Like"} this signal. ${likes} likes`}
			>
				{liked ? "❤️" : "🤍"} {likes}
			</button>
			<span className="reaction-stat" aria-label={`${comments} comments`}>💬 {comments}</span>
			<button
				onClick={share}
				disabled={pending}
				aria-label={`Share this signal. ${shares} shares`}
			>
				🔗 {shares}
			</button>
		</div>
	);
}
