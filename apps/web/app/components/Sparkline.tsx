/** Tiny inline-SVG sparkline — no deps. Renders a polyline from signal values. */

import { sparklinePoints } from "@/lib/sparkline";

export function Sparkline({
	values,
	width = 200,
	height = 40,
	color = "#22c55e",
	label = "Signal history",
}: {
	values: number[];
	width?: number;
	height?: number;
	color?: string;
	label?: string;
}) {
	const accessibleLabel = `${label}: ${values.length} ${values.length === 1 ? "observation" : "observations"}${
		values.length > 0 ? `, from ${values[0]} to ${values.at(-1)}` : ""
	}`;
	if (values.length === 0) {
		return (
			<span style={{ color: "#555", fontSize: "0.8rem" }}>no history yet</span>
		);
	}
	if (values.length === 1) {
		return (
			<svg
				width={width}
				height={height}
				viewBox={`0 0 ${width} ${height}`}
				preserveAspectRatio="none"
				role="img"
				aria-label={accessibleLabel}
			>
				<circle cx={width / 2} cy={height / 2} r={3} fill={color} />
			</svg>
		);
	}
	const points = sparklinePoints(values, width, height, 3)
		.map(([x, y]) => `${x},${y}`)
		.join(" ");
	return (
		<svg
			width={width}
			height={height}
			viewBox={`0 0 ${width} ${height}`}
			preserveAspectRatio="none"
			role="img"
			aria-label={accessibleLabel}
		>
			<polyline
				points={points}
				fill="none"
				stroke={color}
				strokeWidth={2}
				strokeLinejoin="round"
				strokeLinecap="round"
				vectorEffect="non-scaling-stroke"
			/>
		</svg>
	);
}
