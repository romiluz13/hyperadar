export const metadata = {
  title: "HypeRadar",
  description: "The trending AI-dev radar that Port operates and MongoDB remembers.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", margin: 0, background: "#0a0a0a", color: "#fafafa" }}>
        {children}
      </body>
    </html>
  );
}
