import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Sticker Studio — Telegram to WhatsApp Converter',
  description: 'Convert any public Telegram sticker pack into ready-to-import WhatsApp (.wastickers) packs. Hosted on Vercel and Modal.com.',
  icons: {
    icon: 'data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>✨</text></svg>',
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="antialiased selection:bg-mint selection:text-black">
        {children}
      </body>
    </html>
  );
}
