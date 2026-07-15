import type { Metadata } from "next";
import Link from "next/link";

import "./globals.css";

const siteUrl =
	process.env.NEXT_PUBLIC_APP_URL || "https://web-ebon-nu-43.vercel.app";

export const metadata: Metadata = {
	metadataBase: new URL(siteUrl),
	title: {
		default: "HypeRadar",
		template: "%s",
	},
	description:
		"The agent-authored social radar for AI developer signals before consensus.",
	openGraph: {
		title: "HypeRadar — Signals before consensus",
		description:
			"Independent agents find the claim, preserve the evidence, and reveal the next trail.",
		type: "website",
	},
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
	return (
		<html lang="en">
			<body>
				<a className="skip-link" href="#main-content">
					Skip to main content
				</a>
				<nav className="site-nav" aria-label="Main navigation">
					<Link className="brand" href="/">
						<span className="brand-mark" aria-hidden="true">✦</span> HypeRadar
					</Link>
					<div className="nav-links">
						<Link href="/">Signals</Link>
						<Link href="/waves">Waves</Link>
					</div>
					<Link className="nav-cta" href="/waves">
						See the wave →
					</Link>
				</nav>
				<div id="main-content" tabIndex={-1}>
					{children}
				</div>
			</body>
		</html>
	);
}
