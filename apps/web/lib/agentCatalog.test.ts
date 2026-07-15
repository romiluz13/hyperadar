import assert from "node:assert/strict";
import test from "node:test";

import { AGENT_CATALOG, agentByHandle } from "./agentCatalog.ts";

test("one catalog drives every public agent identity", () => {
	assert.equal(AGENT_CATALOG.length, 5);
	assert.equal(new Set(AGENT_CATALOG.map((agent) => agent.handle)).size, 5);
	assert.match(agentByHandle("@youtube-trends")?.bio ?? "", /hype amplifier/i);
	assert.equal(agentByHandle("@missing"), undefined);
});
