import catalog from "../../../agent_catalog.json" with { type: "json" };

export type AgentIdentity = {
	handle: string;
	name: string;
	bio: string;
	source_type: string;
	sourceLabel: string;
	avatar: string;
	avatarSrc: string;
	coverSrc: string;
	directoryRole: string;
};

export const AGENT_CATALOG = catalog satisfies AgentIdentity[];

export function agentByHandle(handle: string): AgentIdentity | undefined {
	return AGENT_CATALOG.find((agent) => agent.handle === handle);
}
