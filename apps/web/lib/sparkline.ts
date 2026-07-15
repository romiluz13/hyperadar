export function sparklinePoints(
	values: number[],
	width: number,
	height: number,
	inset: number,
): [number, number][] {
	if (values.length === 0) return [];
	if (values.length === 1) return [[width / 2, height / 2]];
	const min = Math.min(...values);
	const max = Math.max(...values);
	const range = max - min;
	const drawableWidth = width - inset * 2;
	const drawableHeight = height - inset * 2;
	const step = drawableWidth / (values.length - 1);
	return values.map((value, index) => [
		Number((inset + index * step).toFixed(2)),
		range === 0
			? height / 2
			: Number(
					(
						height -
						inset -
						((value - min) / range) * drawableHeight
					).toFixed(2),
				),
	]);
}
