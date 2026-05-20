import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';

const errorRate = new Rate('errors');

const BASE_URL = __ENV.BASE_URL || 'http://localhost:80';

export const options = {
  stages: [
    { duration: '30s', target: 50 },
    { duration: '1m', target: 200 },
    { duration: '30s', target: 0 },
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'],
    http_req_failed: ['rate<0.05'],
    errors: ['rate<0.1'],
  },
};

let token = '';

export function setup() {
  const regRes = http.post(`${BASE_URL}/api/v1/auth/register`, JSON.stringify({
    username: 'loadtest_user',
    password: 'testpass123',
    role: 'patient',
  }), {
    headers: { 'Content-Type': 'application/json' },
  });
  check(regRes, { 'registered': (r) => r.status === 200 || r.status === 400 });
  const body = regRes.json();
  return body.access_token || '';
}

export default function (setupToken) {
  token = setupToken;

  const headers = {
    'Authorization': `Bearer ${token}`,
    'Accept': 'application/json',
  };

  const res = http.get(`${BASE_URL}/api/v1/doctors`, { headers });
  check(res, { 'doctors loaded': (r) => r.status === 200 });
  errorRate.add(res.status !== 200);

  sleep(0.5);
}
