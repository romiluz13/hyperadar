import assert from "node:assert/strict";
import test from "node:test";

import { isMultiAgentTheme, themeAnchor, visibleWaves } from "./waves.ts";
import * as wavesModule from "./waves.ts";

test("a multi-agent theme needs multiple agents and multiple projects", () => {
	assert.equal(isMultiAgentTheme({ agentCount: 2, count: 2 }), true);
	assert.equal(isMultiAgentTheme({ agentCount: 2, count: 1 }), false);
	assert.equal(isMultiAgentTheme({ agentCount: 1, count: 3 }), false);
});

test("theme destinations are stable, readable fragment identifiers", () => {
	assert.equal(themeAnchor("Agent Skill Specs"), "theme-agent-skill-specs");
	assert.equal(themeAnchor("  Memory + RAG  "), "theme-memory-rag");
});

test("the live waves page rejects an arbitrarily stale digest window", () => {
	const isFreshWaveWindow = (
		wavesModule as {
			isFreshWaveWindow?: (weekOf: Date, now: Date) => boolean;
		}
	).isFreshWaveWindow;
	const now = new Date("2026-07-13T12:00:00.000Z");
	assert.equal(typeof isFreshWaveWindow, "function");
	assert.equal(
		isFreshWaveWindow?.(new Date("2026-07-06T12:00:00.000Z"), now),
		true,
	);
	assert.equal(
		isFreshWaveWindow?.(new Date("2026-07-05T11:59:59.000Z"), now),
		false,
	);
});

test("cached themes hide projects without a currently published source post", () => {
	const waves = [
		{
			label: "agent memory",
			projects: [
				{ title: "Public", url: "https://example.com/public", momentumScore: 70 },
				{ title: "Pending", url: "https://example.com/pending", momentumScore: 90 },
			],
			avgMomentum: 80,
			count: 2,
			agentCount: 2,
		},
	];

	assert.deepEqual(
		visibleWaves(waves, [
			{
				agentHandle: "@github-radar",
				project: { url: "https://example.com/public" },
			},
		]),
		[
			{
				label: "agent memory",
				projects: [
					{
						title: "Public",
						url: "https://example.com/public",
						momentumScore: 70,
					},
				],
				avgMomentum: 70,
				count: 1,
				agentCount: 1,
				agentHandles: ["@github-radar"],
			},
		],
	);
});
