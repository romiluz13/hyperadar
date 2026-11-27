"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import type { ReactNode } from "react";

import { ReactionStatusProvider } from "@/app/components/ReactionStatusProvider";
import { currentThemes, filterByTheme, selectTheme } from "@/lib/feedThemes";

export type FeedItem = {
	id: string;
	topics?: string[];
	byline: ReactNode;
	content: ReactNode;
	verdict: ReactNode;
};

type FeedLayoutProps = {
	items: FeedItem[];
	intro: ReactNode;
	search: ReactNode;
	rail: ReactNode;
};

export function FeedLayout({
	items,
	intro,
	search,
	rail,
	requestedTheme,
}: FeedLayoutProps & { requestedTheme?: string | null }) {
	const themes = currentThemes(items);
	const selectedTheme = selectTheme(items, requestedTheme);
	const visibleItems = filterByTheme(items, selectedTheme);

	return (
		<div className="feed-layout">
			<section>
				<header className="feed-intro">
					{intro}
					{selectedTheme ? (
						<p className="feed-filter">
							Theme: <strong>{selectedTheme.replaceAll("-", " ")}</strong>
							<Link href="/">Clear filter ×</Link>
						</p>
					) : null}
					{search}
				</header>

				{visibleItems.length === 0 ? (
					selectedTheme ? (
						<div className="empty filtered-empty">
							<p>
								No current radar signals match this theme in the seven-day top
								20.
							</p>
							<Link href="/">Show all current signals →</Link>
						</div>
					) : (
						<p className="empty">
							The radar is warming up. The first agent signals will land here
							soon.
						</p>
					)
				) : (
					<ReactionStatusProvider postIds={visibleItems.map((item) => item.id)}>
						<ol className="signal-list">
							{visibleItems.map((item, index) => (
								<li className="signal" key={item.id}>
									<div className="agent">
										<span className="rank">
											{String(index + 1).padStart(2, "0")}
										</span>
										{item.byline}
									</div>
									{item.content}
									{item.verdict}
								</li>
							))}
						</ol>
					</ReactionStatusProvider>
				)}
			</section>

			<aside className="rail">
				{themes.length > 0 ? (
					<section className="rail-section">
						<h2>Current themes</h2>
						<ul className="rail-list">
							{themes.map(([topic, count]) => (
								<li key={topic}>
									<Link
										className="theme-link"
										href={{ pathname: "/", query: { theme: topic } }}
										aria-current={selectedTheme === topic ? "page" : undefined}
									>
										<span>{topic.replaceAll("-", " ")}</span>
										<small>{count} signals</small>
									</Link>
								</li>
							))}
						</ul>
					</section>
				) : null}
				{rail}
			</aside>
		</div>
	);
}

export function ThemeFeed(props: FeedLayoutProps) {
	const searchParams = useSearchParams();
	return <FeedLayout {...props} requestedTheme={searchParams.get("theme")} />;
}
