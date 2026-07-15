export function rankWithHumanSignal(
	momentumScore: number,
	recentParticipants: number,
): number {
	const momentum = Math.min(Math.max(momentumScore, 0), 100);
	const humanBonus = Math.min(Math.max(recentParticipants, 0) * 2, 10);
	return Math.round(Math.min(momentum + humanBonus, 100) * 10) / 10;
}
