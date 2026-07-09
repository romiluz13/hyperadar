/** Tiny inline-SVG sparkline — no deps. Renders a polyline from signal values. */

export function Sparkline({
	values,
	width = 200,
	height = 40,
	color = "#22c55e",
}: {
	values: number[];
	width?: number;
	height?: number;
	color?: string;
}) {
	if (values.length === 0) {
		return (
			<span style={{ color: "#555", fontSize: "0.8rem" }}>no history yet</span>
		);
	}
	if (values.length === 1) {
		// Single point: show a dot
		return (
			<svg
				width={width}
				height={height}
				role="img"
				aria-label="momentum history"
			>
				<circle cx={width / 2} cy={height / 2} r={3} fill={color} />
			</svg>
		);
	}
	const min = Math.min(...values);
	const max = Math.max(...values);
	const range = max - min || 1;
	const step = width / (values.length - 1);
	const points = values
		.map(
			(v, i) =>
				`${(i * step).toFixed(1)},${(height - ((v - min) / range) * height).toFixed(1)}`,
		)
		.join(" ");
	return (
		<svg width={width} height={height} role="img" aria-label="momentum history">
			<polyline
				points={points}
				fill="none"
				stroke={color}
				strokeWidth={2}
				strokeLinejoin="round"
				strokeLinecap="round"
			/>
		</svg>
	);
}
