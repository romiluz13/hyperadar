"use client";

import Link from "next/link";
import { useEffect, useState, useTransition } from "react";

import { useLikedPostStatus } from "@/app/components/ReactionStatusProvider";
import { absoluteShareUrl } from "@/lib/share";

type Props = {
	postId: string;
	permalink: string;
	initialLikes: number;
	initialShares: number;
	initialComments: number;
};

export function ReactionBar({
	postId,
	permalink,
	initialLikes,
	initialShares,
	initialComments,
}: Props) {
	const [liked, setLiked] = useState(false);
	const [likes, setLikes] = useState(initialLikes);
	const [shares, setShares] = useState(initialShares);
	const [feedback, setFeedback] = useState("");
	const [pending, startTransition] = useTransition();
	const storedLiked = useLikedPostStatus(postId);

	useEffect(() => {
		if (storedLiked !== null) setLiked(storedLiked);
	}, [storedLiked]);

	function toggleLike() {
		setFeedback("");
		startTransition(async () => {
			try {
				const response = await fetch("/api/reactions", {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ postId, type: "like" }),
				});
				if (!response.ok) throw new Error("Like request failed");
				const data = await response.json();
				setLiked(Boolean(data.liked));
				setLikes(data.counts.likes);
			} catch {
				setFeedback("Could not save your reaction. Try again.");
			}
		});
	}

	function share() {
		setFeedback("");
		startTransition(async () => {
			try {
				if (!navigator.clipboard) throw new Error("Clipboard unavailable");
				await navigator.clipboard.writeText(
					absoluteShareUrl(permalink, window.location.origin),
				);
			} catch {
				setFeedback("Could not copy the link. Open the signal and try again.");
				return;
			}

			try {
				const response = await fetch("/api/reactions", {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ postId, type: "share" }),
				});
				if (!response.ok) throw new Error("Share request failed");
				const data = await response.json();
				setShares(data.counts.shares);
				setFeedback("Link copied.");
			} catch {
				setFeedback("Link copied. The public share count could not update.");
			}
		});
	}

	return (
		<div className="reaction-group">
			<div className="reaction-bar" aria-label="Post reactions">
				<button
					type="button"
					onClick={toggleLike}
					disabled={pending}
					aria-pressed={liked}
					aria-label={`${liked ? "Unlike" : "Like"} this signal. ${likes} likes`}
				>
					<span aria-hidden="true">{liked ? "♥" : "♡"}</span> {likes}
				</button>
				<Link
					className="reaction-stat"
					href={`${permalink}#conversation`}
					aria-label={`Open ${initialComments} comments`}
				>
					<span aria-hidden="true">◌</span> {initialComments}
				</Link>
				<button
					type="button"
					onClick={share}
					disabled={pending}
					aria-label={`Copy a link to this signal. ${shares} shares`}
				>
					<span aria-hidden="true">↗</span> {shares}
				</button>
			</div>
			<p className="reaction-feedback" aria-live="polite">
				{feedback}
			</p>
		</div>
	);
}
