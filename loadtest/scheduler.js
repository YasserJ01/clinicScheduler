import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const errorRate = new Rate('errors');
const bookingSuccessRate = new Rate('booking_success');
const bookingConflictRate = new Rate('booking_conflict');
const bookingLatency = new Trend('booking_latency', true);
const doctorsLatency = new Trend('doctors_latency', true);
const bookingsPerSecond = new Rate('bookings_per_second');

const BASE_URL = __ENV.BASE_URL || 'http://localhost:80';

export const options = {
  scenarios: {
    mixed_load: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 50 },
        { duration: '1m', target: 200 },
        { duration: '30s', target: 0 },
      ],
      gracefulRampDown: '10s',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<500'],
    http_req_failed: ['rate<0.05'],
    errors: ['rate<0.1'],
    booking_success: ['rate>0.8'],
    booking_latency: ['p(95)<500'],
    doctors_latency: ['p(95)<300'],
    bookings_per_second: ['rate>0.1'],
  },
};

function registerOrLogin() {
  const username = `loadtest_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const regRes = http.post(`${BASE_URL}/api/v1/auth/register`, JSON.stringify({
    username: username,
    password: 'testpass123',
    role: 'patient',
  }), {
    headers: { 'Content-Type': 'application/json' },
  });

  let token = '';
  if (regRes.status === 200) {
    const body = regRes.json();
    token = body.access_token || '';
  } else if (regRes.status === 400) {
    const loginRes = http.post(`${BASE_URL}/api/v1/auth/login`, JSON.stringify({
      username: username,
      password: 'testpass123',
    }), {
      headers: { 'Content-Type': 'application/json' },
    });
    if (loginRes.status === 200) {
      token = loginRes.json().access_token || '';
    }
  }

  return token;
}

function createPatient(token) {
  const patientName = `LoadTest Patient ${Date.now()}`;
  const res = http.post(`${BASE_URL}/api/v1/patients`, JSON.stringify({
    name: patientName,
    email: `loadtest_${Date.now()}@example.com`,
  }), {
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
    },
  });

  if (res.status === 200 || res.status === 201) {
    const body = res.json();
    return body.id || body.patient?.id || null;
  }
  return null;
}

export function setup() {
  const token = registerOrLogin();
  if (!token) {
    throw new Error('Failed to authenticate during setup');
  }
  const patientId = createPatient(token);
  if (!patientId) {
    throw new Error('Failed to create patient during setup');
  }

  return { token, patientId };
}

export default function (data) {
  const token = data.token;
  const patientId = data.patientId;

  if (!token || !patientId) {
    errorRate.add(true);
    return;
  }

  const headers = {
    'Authorization': `Bearer ${token}`,
    'Accept': 'application/json',
  };

  const rand = Math.random();

  if (rand < 0.7) {
    const res = http.get(`${BASE_URL}/api/v1/doctors`, { headers });
    check(res, { 'doctors loaded': (r) => r.status === 200 });
    doctorsLatency.add(res.timings.duration);
    errorRate.add(res.status !== 200);
    sleep(0.3);
  } else if (rand < 0.9) {
    const doctorRes = http.get(`${BASE_URL}/api/v1/doctors`, { headers });
    const doctors = doctorRes.status === 200 ? doctorRes.json().items || [] : [];
    if (doctors.length === 0) {
      errorRate.add(true);
      sleep(0.5);
      return;
    }

    const doctor = doctors[Math.floor(Math.random() * doctors.length)];
    const hour = Math.floor(Math.random() * 12) + 8;
    const minute = Math.floor(Math.random() * 60);
    const day = Math.floor(Math.random() * 28) + 1;
    const timeSlot = `2027-08-${String(day).padStart(2, '0')}T${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}:00Z`;

    const bookingRes = http.post(`${BASE_URL}/api/v1/appointments`, JSON.stringify({
      doctor_id: doctor.id,
      patient_id: patientId,
      time_slot: timeSlot,
    }), { headers });

    const bookingOk = check(bookingRes, {
      'booking succeeded or conflicted': (r) => r.status === 201 || r.status === 409,
    });

    bookingLatency.add(bookingRes.timings.duration);
    bookingsPerSecond.add(1);

    if (bookingRes.status === 201) {
      bookingSuccessRate.add(true);
      bookingConflictRate.add(false);
    } else if (bookingRes.status === 409) {
      bookingSuccessRate.add(false);
      bookingConflictRate.add(true);
    } else {
      bookingSuccessRate.add(false);
      bookingConflictRate.add(false);
      errorRate.add(true);
    }

    sleep(0.5);
  } else {
    const apptRes = http.get(`${BASE_URL}/api/v1/appointments`, { headers });
    const appts = apptRes.status === 200 ? apptRes.json().items || [] : [];
    if (appts.length > 0) {
      const appt = appts[Math.floor(Math.random() * appts.length)];
      if (appt.status === 'scheduled') {
        const cancelRes = http.patch(
          `${BASE_URL}/api/v1/appointments/${appt.id}/status`,
          JSON.stringify({ status: 'cancelled' }),
          { headers: { ...headers, 'Content-Type': 'application/json' } }
        );
        check(cancelRes, { 'status update succeeded': (r) => r.status === 200 || r.status === 409 });
        errorRate.add(cancelRes.status !== 200 && cancelRes.status !== 409);
      }
    }
    sleep(0.3);
  }
}
