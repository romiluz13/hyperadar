import assert from "node:assert/strict";
import test from "node:test";

import {
	findProject,
	PROJECT_POST_PROJECTION,
	PROJECT_PROJECTION,
	type ProjectReader,
} from "./projectLookup.ts";

type Read = { method: "find" | "findOne"; options: unknown };

function recordingProjects(results: {
	find?: unknown[][];
	findOne?: unknown;
}): { projects: ProjectReader; reads: Read[] } {
	const reads: Read[] = [];
	const findResults = [...(results.find ?? [])];
	const projects = {
		find(_filter: unknown, options: unknown) {
			reads.push({ method: "find", options });
			const docs = findResults.shift() ?? [];
			return { limit: () => ({ toArray: async () => docs }) };
		},
		async findOne(_filter: unknown, options: unknown) {
			reads.push({ method: "findOne", options });
			return results.findOne ?? null;
		},
	} as unknown as ProjectReader;
	return { projects, reads };
}

function assertProjectsWithoutEmbedding(options: unknown) {
	const projection = (options as { projection?: Record<string, unknown> })
		?.projection;
	assert.ok(projection, "read must pass a projection");
	const keys = Object.keys(projection);
	assert.ok(keys.length > 0 && keys.every((key) => projection[key] === 1));
	assert.ok(!keys.some((key) => key.startsWith("embedding")));
}

test("every project lookup read projects the project without its embedding", async () => {
	const { projects, reads } = recordingProjects({});

	assert.equal(
		await findProject(projects, "youtube-rp5ewoogwew-1234abcd"),
		null,
	);

	assert.deepEqual(
		reads.map((read) => read.method),
		["find", "findOne", "find"],
	);
	for (const read of reads) assertProjectsWithoutEmbedding(read.options);
});

test("a direct slug match is returned from one projected read", async () => {
	const project = { url: "https://github.com/a/b", slug: "a-b" };
	const { projects, reads } = recordingProjects({ find: [[project]] });

	assert.equal(await findProject(projects, "a-b"), project);
	assert.equal(reads.length, 1);
	assertProjectsWithoutEmbedding(reads[0].options);
});

test("an ambiguous slug matches no project", async () => {
	const { projects } = recordingProjects({
		find: [[{ url: "https://a.com" }, { url: "https://b.com" }]],
	});

	assert.equal(await findProject(projects, "a-b"), null);
});

test("project projections keep every field the project page renders", () => {
	for (const field of [
		"url",
		"slug",
		"legacySlugs",
		"title",
		"description",
		"topics",
		"kind",
		"momentumScore",
		"hypeVerdict",
		"firstSeenAt",
	]) {
		assert.equal(
			(PROJECT_PROJECTION as Record<string, unknown>)[field],
			1,
			field,
		);
	}
	for (const field of [
		"_id",
		"agentHandle",
		"body",
		"verdict",
		"postedAt",
		"project.url",
		"project.title",
		"project.description",
		"project.kind",
		"project.momentumScore",
		"project.topics",
		"reactionCounts",
	]) {
		assert.equal(
			(PROJECT_POST_PROJECTION as Record<string, unknown>)[field],
			1,
			field,
		);
	}
	assertProjectsWithoutEmbedding({ projection: PROJECT_PROJECTION });
	assertProjectsWithoutEmbedding({ projection: PROJECT_POST_PROJECTION });
});
