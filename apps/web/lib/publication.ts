import type { ObjectId } from "mongodb";

export const PUBLIC_POST_FILTER = {
	portSyncStatus: "synced",
	evidenceContractVersion: 2,
	legacyDuplicateOf: { $exists: false },
};

export const PUBLIC_DIGEST_FILTER = {
	publicationSyncStatus: "synced",
	evidenceContractVersion: 2,
};

export function publicSourceProjectUrls(urls: string[]): string[] {
	return urls.filter((url) => /^https?:\/\//i.test(url));
}

export function publishedPostFilter<T extends Record<string, unknown>>(filter: T) {
	return { ...filter, ...PUBLIC_POST_FILTER };
}

export function publishedSignalFilter(
	projectId: string,
	canonicalSignalIds: ObjectId[],
	verifiedLegacySignalIds: ObjectId[],
) {
	return {
		projectId,
		_id: { $in: [...canonicalSignalIds, ...verifiedLegacySignalIds] },
	};
}
