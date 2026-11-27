import assert from "node:assert/strict";
import test from "node:test";

import { currentThemes, filterByTheme, selectTheme } from "./feedThemes.ts";

const feed = [
	{ id: "1", topics: ["Agents", "ai", "mcp"] },
	{ id: "2", topics: ["agents", "rag"] },
	{ id: "3", topics: ["MCP", "Agents", "github"] },
	{ id: "4", topics: ["evals"] },
	{ id: "5", topics: ["rag", "coding"] },
	{ id: "6" },
];

test("a theme link selects the observed topic whatever its case", () => {
	assert.equal(selectTheme(feed, "agents"), "Agents");
	assert.equal(selectTheme(feed, "AGENTS"), "Agents");
	assert.equal(selectTheme(feed, "  mcp "), "mcp");
});

test("an unobserved theme stays selected as the trimmed request", () => {
	assert.equal(selectTheme(feed, "  Robotics "), "Robotics");
	assert.equal(selectTheme(feed, "x".repeat(100)), "x".repeat(80));
});

test("no theme or a blank theme selects nothing", () => {
	assert.equal(selectTheme(feed, null), undefined);
	assert.equal(selectTheme(feed, undefined), undefined);
	assert.equal(selectTheme(feed, "   "), undefined);
});

test("the cached feed filters to posts carrying the theme, ignoring case", () => {
	assert.deepEqual(
		filterByTheme(feed, "agents").map((item) => item.id),
		["1", "2", "3"],
	);
	assert.deepEqual(filterByTheme(feed, "Robotics"), []);
	assert.equal(filterByTheme(feed, undefined), feed);
});

test("current themes are the four most common non-generic topics", () => {
	assert.deepEqual(currentThemes(feed), [
		["Agents", 2],
		["rag", 2],
		["mcp", 1],
		["agents", 1],
	]);
});
