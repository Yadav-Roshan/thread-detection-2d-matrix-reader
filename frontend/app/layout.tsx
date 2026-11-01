import './globals.css';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'CamShaft 2D Matrix & Thread Inspection',
  description: 'Thread presence and 2D code inspection POKA YOKE',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
