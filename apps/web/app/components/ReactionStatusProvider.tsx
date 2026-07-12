"use client";

import {
	createContext,
	type ReactNode,
	useContext,
	useEffect,
	useState,
} from "react";

const LikedPostContext = createContext<ReadonlySet<string> | null>(null);

export function ReactionStatusProvider({
	postIds,
	children,
}: {
	postIds: string[];
	children: ReactNode;
}) {
	const [likedPostIds, setLikedPostIds] = useState<ReadonlySet<string> | null>(
		null,
	);
	const query = postIds.slice(0, 20).join(",");

	useEffect(() => {
		if (!query) {
			setLikedPostIds(new Set());
			return;
		}
		const controller = new AbortController();
		fetch(`/api/reactions?postIds=${encodeURIComponent(query)}`, {
			signal: controller.signal,
		})
			.then((response) => {
				if (!response.ok) throw new Error("Reaction status request failed");
				return response.json();
			})
			.then((data) => setLikedPostIds(new Set(data.likedPostIds ?? [])))
			.catch((error: unknown) => {
				if (error instanceof DOMException && error.name === "AbortError") return;
				setLikedPostIds(new Set());
			});
		return () => controller.abort();
	}, [query]);

	return (
		<LikedPostContext.Provider value={likedPostIds}>
			{children}
		</LikedPostContext.Provider>
	);
}

export function useLikedPostStatus(postId: string): boolean | null {
	const likedPostIds = useContext(LikedPostContext);
	return likedPostIds ? likedPostIds.has(postId) : null;
}
