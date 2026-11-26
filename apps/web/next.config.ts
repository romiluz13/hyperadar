import type { NextConfig } from "next";

const nextConfig: NextConfig = {
	async redirects() {
		return [
			{
				source: "/",
				has: [{ type: "query", key: "q" }],
				destination: "/search",
				permanent: false,
			},
		];
	},
};

export default nextConfig;
