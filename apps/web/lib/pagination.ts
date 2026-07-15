const ARCHIVE_PAGE_SIZE = 20;

export function archiveWindow(requested: string | undefined, total: number) {
	const parsed = Number.parseInt(requested ?? "1", 10);
	const totalPages = Math.max(1, Math.ceil(total / ARCHIVE_PAGE_SIZE));
	const page = Math.min(
		totalPages,
		Number.isFinite(parsed) && parsed > 0 ? parsed : 1,
	);
	const skip = (page - 1) * ARCHIVE_PAGE_SIZE;
	return {
		page,
		totalPages,
		skip,
		start: total === 0 ? 0 : skip + 1,
		end: Math.min(total, skip + ARCHIVE_PAGE_SIZE),
	};
}
