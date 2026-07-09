/** Anonymous user identity via cookie — no login required for likes/shares.
 *  Comments need a display name (also cookie-stored). Full Better Auth is a
 *  production upgrade; T4 focuses on the reaction→rank loop in MongoDB. */

import { cookies } from "next/headers";

const USER_ID_COOKIE = "hr_uid";
const NAME_COOKIE = "hr_name";

/** Get or create a stable anonymous user ID. Call from a Server Action / route handler. */
export async function getOrCreateUserId(): Promise<string> {
	const store = await cookies();
	let id = store.get(USER_ID_COOKIE)?.value;
	if (!id) {
		id = `anon_${crypto.randomUUID()}`;
		store.set(USER_ID_COOKIE, id, {
			maxAge: 60 * 60 * 24 * 365,
			httpOnly: true,
			sameSite: "lax",
		});
	}
	return id;
}

/** Get the stored display name (for comments), if any. */
export async function getDisplayName(): Promise<string | null> {
	const store = await cookies();
	return store.get(NAME_COOKIE)?.value ?? null;
}

/** Set the display name (when a user first comments). */
export async function setDisplayName(name: string): Promise<void> {
	const store = await cookies();
	store.set(NAME_COOKIE, name, {
		maxAge: 60 * 60 * 24 * 365,
		httpOnly: false,
		sameSite: "lax",
	});
}
