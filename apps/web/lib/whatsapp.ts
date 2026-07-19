/**
 * Generate a WhatsApp deep link to RomBot with a pre-filled message
 * referencing the specific community topic.
 *
 * The phone number +972559874713 is RomBot's public WhatsApp — an AI
 * community brain serving a 4,000-member AI developer community.
 */
const ROMBOT_WHATSAPP_NUMBER = "972559874713";

export function whatsappUrl(topicTitle: string): string {
	const message = `I saw the discussion about "${topicTitle}" on HypeRadar — tell me more`;
	return `https://wa.me/${ROMBOT_WHATSAPP_NUMBER}?text=${encodeURIComponent(message)}`;
}
