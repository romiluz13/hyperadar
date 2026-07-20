import assert from "node:assert/strict";
import test from "node:test";

import { whatsappUrl } from "./whatsapp.ts";

test("whatsappUrl generates a wa.me link with the correct phone number", () => {
	const url = whatsappUrl("Agent Security Crisis");
	assert.ok(url.startsWith("https://wa.me/972559874713?text="));
});

test("whatsappUrl includes the topic title in the pre-filled message", () => {
	const url = whatsappUrl("Agent Security Crisis");
	const decoded = decodeURIComponent(url.split("?text=")[1]);
	assert.match(decoded, /Agent Security Crisis/);
});

test("whatsappUrl URL-encodes the message", () => {
	const url = whatsappUrl("Context Lakes & Shared Brains");
	// & must be encoded as %26 in a URL query parameter
	assert.ok(url.includes("%26"));
});

test("whatsappUrl message format references HypeRadar", () => {
	const url = whatsappUrl("GPT 5.6 Sol");
	const decoded = decodeURIComponent(url.split("?text=")[1]);
	assert.match(
		decoded,
		/I saw the discussion about "GPT 5.6 Sol" on HypeRadar/,
	);
});
