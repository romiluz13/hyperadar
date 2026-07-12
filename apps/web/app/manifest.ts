import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
	return {
		name: "HypeRadar — Trending AI Dev Radar",
		short_name: "HypeRadar",
		description:
			"The trending AI-dev radar that Port operates and MongoDB remembers.",
		start_url: "/",
		display: "standalone",
		background_color: "#fbfaf5",
		theme_color: "#c8ff00",
		icons: [],
	};
}
