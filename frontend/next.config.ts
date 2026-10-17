import type { NextConfig } from 'next';
const qa = process.env.HEXING_QA === '1';
// Demo builds set both values at build time; rewrites are compiled into the build output.
const api = process.env.HEXING_API_ORIGIN || (qa ? 'http://127.0.0.1:18060' : 'http://127.0.0.1:8010');
const config: NextConfig = {
  poweredByHeader: false,
  distDir: process.env.HEXING_DIST_DIR || (qa ? '.next-qa' : '.next'),
  async rewrites() {
    return [{ source: '/api/v1/:path*', destination: api + '/api/v1/:path*' }];
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
