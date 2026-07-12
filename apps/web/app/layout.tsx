export const metadata = {
  title: "HypeRadar",
  description: "The trending AI-dev radar that Port operates and MongoDB remembers.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#main-content">Skip to signals</a>
        <nav className="site-nav" aria-label="Main navigation">
          <a className="brand" href="/"><span className="brand-mark">✦</span> HypeRadar</a>
          <div className="nav-links"><a href="/">Signals</a><a href="/waves">Waves</a></div>
          <a className="nav-cta" href="/waves">See the wave →</a>
        </nav>
        <div id="main-content" tabIndex={-1}>{children}</div>
      </body>
    </html>
  );
}
import "./globals.css";
