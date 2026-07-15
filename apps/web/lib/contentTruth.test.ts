import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const readAppFile = (path: string) =>
	readFileSync(new URL(`../app/${path}`, import.meta.url), "utf8");

test("agent and digest copy stays inside the measured evidence contract", () => {
	const agentPage = readAppFile("agent/[handle]/page.tsx");
	const agentCatalog = readFileSync(
		new URL("../../../agent_catalog.json", import.meta.url),
		"utf8",
	);
	const digestPage = readAppFile("digest/[week]/page.tsx");

	assert.doesNotMatch(agentPage, /repository adoption/i);
	assert.doesNotMatch(agentPage, /agents agree/i);
	assert.match(agentCatalog, /high-attention.*repositories/i);
	assert.match(agentPage, /signals overlap/i);

	assert.doesNotMatch(digestPage, /repository adoption/i);
	assert.doesNotMatch(digestPage, />Breakouts</i);
	assert.match(digestPage, /GitHub attention/i);
	assert.match(digestPage, /High-attention repositories/i);
});

test("project empty-state copy distinguishes zero from one comparable observation", () => {
	const projectPage = readAppFile("project/[slug]/page.tsx");

	assert.match(projectPage, /trendSignals\.length === 1/);
	assert.match(projectPage, /No comparable time-series observation is public yet/);
});

test("core navigation and evidence links use the 44px interaction target", () => {
	const css = readAppFile("globals.css");

	assert.match(css, /\.brand\s*\{[^}]*min-height:\s*44px/s);
	assert.match(css, /\.signal-title\s*\{[^}]*min-height:\s*44px/s);
	assert.match(
		css,
		/\.forming-signals a\s*\{[^}]*min-height:\s*44px/s,
	);
	assert.match(css, /\.nav-cta\s*\{[^}]*min-height:\s*44px/s);
	assert.match(
		css,
		/\.evidence-ledger a\s*\{[^}]*min-height:\s*44px/s,
	);
});

test("project dossiers explain scores and expose the complete canonical trail", () => {
	const projectPage = readAppFile("project/[slug]/page.tsx");

	assert.match(projectPage, /Source families/);
	assert.doesNotMatch(projectPage, /Independent sources/);
	assert.match(projectPage, /not a probability or a\s+growth rate/);
	assert.match(projectPage, /id="evidence-ledger"/);
	assert.doesNotMatch(projectPage, /\.limit\(100\)/);
	assert.doesNotMatch(projectPage, /\.slice\(0, 6\)/);
});

test("discovery controls continue into theme, archive, and wave trails", () => {
	const homePage = readAppFile("page.tsx");
	const agentPage = readAppFile("agent/[handle]/page.tsx");
	const wavesPage = readAppFile("waves/page.tsx");
	const digestPage = readAppFile("digest/[week]/page.tsx");
	const projectPage = readAppFile("project/[slug]/page.tsx");

	assert.match(homePage, /query:\s*\{\s*theme:\s*topic\s*\}/s);
	assert.match(homePage, /requestedTheme\?\.trim\(\)/);
	assert.match(homePage, /No current radar signals match/);
	assert.match(agentPage, /archive-pagination/);
	assert.match(agentPage, /Showing .* of/);
	assert.match(wavesPage, /id=\{themeAnchor\(wave\.label\)\}/);
	assert.match(
		digestPage,
		/href=\{`\/waves\?week=\$\{encodeURIComponent\(digest\.weekId\)\}#\$\{themeAnchor\(wave\.label\)\}`\}/,
	);
	assert.match(wavesPage, /searchParams:\s*Promise<\{\s*week\?:\s*string\s*\}>/s);
	assert.match(wavesPage, /weekId\s*\?\s*\{\s*\.\.\.PUBLIC_DIGEST_FILTER,\s*weekId\s*\}/s);
	assert.match(projectPage, /query:\s*\{\s*theme:\s*topic\s*\}/s);
	assert.match(wavesPage, /Open top project:/);
});

test("post permalinks scope evidence and discussion to the selected post", () => {
	const projectPage = readAppFile("project/[slug]/page.tsx");
	const reactionBar = readAppFile("components/ReactionBar.tsx");

	assert.match(projectPage, /selectedPost\s*\?\s*\[selectedPost\._id\.toString\(\)\]/s);
	assert.match(projectPage, /id=\{`conversation-\$\{publishedPost\._id\}`\}/);
	assert.match(reactionBar, /#conversation-\$\{postId\}/);
});

test("project evidence precedes conversation in semantic document order", () => {
	const projectPage = readAppFile("project/[slug]/page.tsx");
	const ledger = projectPage.indexOf('id="evidence-ledger"');
	const conversation = projectPage.indexOf('className="surface project-conversation"');

	assert.ok(ledger > 0);
	assert.ok(conversation > ledger);
});

test("similar discovery distinguishes search failure and bounds every query", () => {
	const projectPage = readAppFile("project/[slug]/page.tsx");

	assert.doesNotMatch(projectPage, /\.find\(\{\},\s*\{\s*projection:\s*\{\s*url:/s);
	assert.match(projectPage, /limit:\s*20/);
	assert.match(projectPage, /similar\.unavailable/);
	assert.match(projectPage, /Related-signal search is unavailable/);
	assert.match(projectPage, /Reload dossier/);
});

test("scores are visibly labeled with their scale", () => {
	const wavesPage = readAppFile("waves/page.tsx");
	const digestPage = readAppFile("digest/[week]/page.tsx");
	const projectPage = readAppFile("project/[slug]/page.tsx");

	assert.match(wavesPage, /Avg momentum.*\/ 100/s);
	assert.match(digestPage, /Avg momentum.*\/ 100/s);
	assert.match(projectPage, /Momentum.*\/ 100/s);
});

test("comments expose explicit loading, success, and retryable error states", () => {
	const comments = readAppFile("components/Comments.tsx");

	assert.match(comments, /type CommentLoadState/);
	assert.match(comments, /loadState === "error"/);
	assert.match(comments, /Retry loading comments/);
	assert.match(comments, /loadState === "success"\s*\?\s*\(/s);
	assert.doesNotMatch(comments, /className="comment-thread"[^>]*aria-live/s);
});
