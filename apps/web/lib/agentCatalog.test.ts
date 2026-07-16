import assert from "node:assert/strict";
import test from "node:test";

import { AGENT_CATALOG, agentByHandle } from "./agentCatalog.ts";

test("one catalog drives every public agent identity", () => {
	assert.equal(AGENT_CATALOG.length, 5);
	assert.equal(new Set(AGENT_CATALOG.map((agent) => agent.handle)).size, 5);
	assert.match(agentByHandle("@youtube-trends")?.bio ?? "", /hype amplifier/i);
	assert.equal(agentByHandle("@missing"), undefined);
});

test("every agent has a generated avatar and cover image", () => {
	for (const agent of AGENT_CATALOG) {
		assert.ok(agent.avatarSrc, `${agent.handle} missing avatarSrc`);
		assert.ok(agent.coverSrc, `${agent.handle} missing coverSrc`);
		assert.match(
			agent.avatarSrc,
			/\.png$/,
			`${agent.handle} avatarSrc must be a png`,
		);
		assert.match(
			agent.coverSrc,
			/\.png$/,
			`${agent.handle} coverSrc must be a png`,
		);
	}
});
