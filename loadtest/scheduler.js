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

// Each VU registers its own user + patient to bypass per-user rate limits.
// This is a well-known pattern for k6 load tests with authenticated APIs.

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
    booking_success: ['rate>0.5'],
    booking_latency: ['p(95)<500'],
    doctors_latency: ['p(95)<300'],
    bookings_per_second: ['rate>0.05'],
  },
};

export default function () {
  const username = `loadtest_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const password = 'testpass123';

  const regRes = http.post(`${BASE_URL}/api/v1/auth/register`, JSON.stringify({
    username: username,
    password: password,
    role: 'patient',
  }), {
    headers: { 'Content-Type': 'application/json' },
    tags: { name: 'register' },
  });

  let token;
  if (regRes.status === 200) {
    token = regRes.json('access_token');
  } else {
    const loginRes = http.post(`${BASE_URL}/api/v1/auth/login`, JSON.stringify({
      username: username,
      password: password,
    }), {
      headers: { 'Content-Type': 'application/json' },
      tags: { name: 'login' },
    });
    if (loginRes.status === 200) {
      token = loginRes.json('access_token');
    }
  }

  if (!token) {
    errorRate.add(true);
    sleep(1);
    return;
  }

  const headers = {
    'Authorization': `Bearer ${token}`,
    'Accept': 'application/json',
  };

  const patientRes = http.post(`${BASE_URL}/api/v1/patients`, JSON.stringify({
    name: `Patient ${Date.now()}`,
    email: `pt_${Date.now()}_${Math.random().toString(36).slice(2, 6)}@example.com`,
  }), {
    headers: { ...headers, 'Content-Type': 'application/json' },
    tags: { name: 'create_patient' },
  });
  const patientId = [200, 201].includes(patientRes.status)
    ? (patientRes.json('id') || patientRes.json('patient.id'))
    : null;

  if (!patientId) {
    errorRate.add(true);
    sleep(1);
    return;
  }

  // Now execute the mixed workload
  const rand = Math.random();

  if (rand < 0.7) {
    const res = http.get(`${BASE_URL}/api/v1/doctors`, { headers, tags: { name: 'list_doctors' } });
    check(res, { 'doctors loaded': (r) => r.status === 200 });
    doctorsLatency.add(res.timings.duration);
    errorRate.add(res.status !== 200);
  } else if (rand < 0.9) {
    const doctorRes = http.get(`${BASE_URL}/api/v1/doctors`, { headers, tags: { name: 'list_doctors' } });
    if (doctorRes.status !== 200) {
      errorRate.add(true);
      sleep(0.5);
      return;
    }
    const doctors = doctorRes.json('items') || [];
    if (doctors.length === 0) {
      errorRate.add(true);
      sleep(0.5);
      return;
    }
    const doctor = doctors[Math.floor(Math.random() * doctors.length)];
    const hour = Math.floor(Math.random() * 9) + 8;
    const minute = Math.floor(Math.random() * 60);
    const day = Math.floor(Math.random() * 28) + 1;
    const timeSlot = `2027-08-${String(day).padStart(2, '0')}T${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}:00Z`;

    const bookingRes = http.post(`${BASE_URL}/api/v1/appointments`, JSON.stringify({
      doctor_id: doctor.id,
      patient_id: patientId,
      time_slot: timeSlot,
    }), {
      headers: { ...headers, 'Content-Type': 'application/json' },
      tags: { name: 'book_appointment' },
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
  } else {
    const apptRes = http.get(`${BASE_URL}/api/v1/appointments`, { headers, tags: { name: 'list_appointments' } });
    if (apptRes.status === 200) {
      const appts = apptRes.json('items') || [];
      if (appts.length > 0) {
        const appt = appts[Math.floor(Math.random() * appts.length)];
        if (appt.status === 'scheduled') {
          const cancelRes = http.patch(
            `${BASE_URL}/api/v1/appointments/${appt.id}/status`,
            JSON.stringify({ status: 'cancelled' }),
            { headers: { ...headers, 'Content-Type': 'application/json' }, tags: { name: 'cancel_appointment' } }
          );
          check(cancelRes, { 'cancel ok': (r) => [200, 409].includes(r.status) });
          errorRate.add(![200, 409].includes(cancelRes.status));
        }
      }
    }
  }

  sleep(0.5);
}
