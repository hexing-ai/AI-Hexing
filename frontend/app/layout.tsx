import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = { title: 'AI-Hexing · 创作工作台', description: '从 GitHub 资料复核到本人音色验证。' };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  // Translation extensions may add attributes to <html> before hydration.
  // Limit this compatibility escape hatch to the root element.
  return <html lang="zh-CN" suppressHydrationWarning><body>{children}</body></html>;
}
