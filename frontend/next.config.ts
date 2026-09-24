import type { NextConfig } from 'next';
const config: NextConfig = {
  poweredByHeader: false,
  distDir: process.env.HEXING_QA === '1' ? '.next-qa' : '.next',
  async rewrites() {
    return [{ source: '/api/v1/:path*', destination: process.env.HEXING_QA === '1' ? 'http://127.0.0.1:18060/api/v1/:path*' : 'http://127.0.0.1:8010/api/v1/:path*' }];
  },
  async headers() {
    return [{ source: '/:path*', headers: [
      { key: 'X-Content-Type-Options', value: 'nosniff' },
      { key: 'Referrer-Policy', value: 'no-referrer' },
      { key: 'X-Frame-Options', value: 'DENY' },
    ] }];
  }
};
export default config;
