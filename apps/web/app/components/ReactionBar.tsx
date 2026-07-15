"use client";

import Link from "next/link";
import { useEffect, useRef, useState, useTransition } from "react";

import { useLikedPostStatus } from "@/app/components/ReactionStatusProvider";
import { reactionLabel } from "@/lib/reactionLabel";
import { absoluteShareUrl } from "@/lib/share";
import { operationForShare } from "@/lib/shareOperation";

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
	const pendingShareOperation = useRef<string | null>(null);

	useEffect(() => {
		if (storedLiked !== null) setLiked(storedLiked);
	}, [storedLiked]);

	function toggleLike() {
		setFeedback("");
		const desiredLiked = !liked;
		startTransition(async () => {
			try {
				const response = await fetch("/api/reactions", {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ postId, type: "like", liked: desiredLiked }),
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
		const operationId = operationForShare(pendingShareOperation.current);
		pendingShareOperation.current = operationId;
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
					body: JSON.stringify({ postId, type: "share", operationId }),
				});
				if (!response.ok) throw new Error("Share request failed");
				const data = await response.json();
				setShares(data.counts.shares);
				setFeedback("Link copied.");
				pendingShareOperation.current = null;
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
					aria-label={
						likes > 0
							? `${liked ? "Unlike" : "Like"} this signal. ${likes} ${likes === 1 ? "like" : "likes"}`
							: `${liked ? "Unlike" : "Like"} this signal`
					}
				>
					<span aria-hidden="true">{liked ? "♥" : "♡"}</span>{" "}
					{reactionLabel(likes, "Like")}
				</button>
				<Link
					className="reaction-stat"
					href={`${permalink}#conversation-${postId}`}
					aria-label={
						initialComments > 0
							? `Discuss this signal. Open ${initialComments} comments`
							: "Discuss this signal"
					}
				>
					<span aria-hidden="true">◌</span>{" "}
					{reactionLabel(initialComments, "Discuss")}
				</Link>
				<button
					type="button"
					onClick={share}
					disabled={pending}
					aria-label={
						shares > 0
							? `Share this signal by copying its link. ${shares} ${shares === 1 ? "share" : "shares"}`
							: "Share this signal by copying its link"
					}
				>
					<span aria-hidden="true">↗</span>{" "}
					{reactionLabel(shares, "Share")}
				</button>
			</div>
			<p className="reaction-feedback" aria-live="polite">
				{feedback}
			</p>
		</div>
	);
}
