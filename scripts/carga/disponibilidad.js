// Prueba de carga T09 (R21): 200 solicitudes/s sostenidas, ráfaga de 400/s y 8 000 usuarios simulados consultando disponibilidad (E01).
// Metas del informe: p95 <= 2 s y errores < 1 %. Además, un origen abusivo recibe 429 SIN afectar a los demás usuarios.
//
// Los usuarios simulados llegan con distinta IP porque k6 corre en la red de aplicación y hace de «proxy de confianza»: la aplicación toma la
// IP del cliente de X-Forwarded-For (que en producción escribe solo el proxy). Así se prueba la aplicación (límites por IP incluidos)
// sin que los 8 000 usuarios compartan la IP del anfitrión de pruebas.
import http from 'k6/http';
import exec from 'k6/execution';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

const BASE = __ENV.BASE || 'http://app:8000';
const USUARIOS = 8000;
const RAFAGA = 400;
const SOSTENIDA = 200;
const limitados = new Counter('abusador_limitado');

export const options = {
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  scenarios: {
    sostenida: {
      executor: 'constant-arrival-rate', rate: SOSTENIDA, timeUnit: '1s', duration: '40s',
      preAllocatedVUs: 200, maxVUs: 1000, exec: 'legitimo', tags: { escenario: 'sostenida' },
    },
    rafaga: {
      executor: 'ramping-arrival-rate', startRate: SOSTENIDA, timeUnit: '1s', startTime: '45s',
      preAllocatedVUs: 400, maxVUs: 1500, exec: 'legitimo', tags: { escenario: 'rafaga' },
      stages: [{ target: RAFAGA, duration: '5s' }, { target: RAFAGA, duration: '20s' }, { target: SOSTENIDA, duration: '5s' }],
    },
    abusador: {
      // Un solo origen insiste durante toda la prueba: debe recibir 429 (o 403 si la detección lo bloquea).
      executor: 'constant-arrival-rate', rate: 20, timeUnit: '1s', duration: '75s', startTime: '0s',
      preAllocatedVUs: 20, maxVUs: 60, exec: 'abusivo', tags: { escenario: 'abusador' },
    },
  },
  thresholds: {
    'http_req_duration{escenario:sostenida}': ['p(95)<2000'],
    'http_req_duration{escenario:rafaga}': ['p(95)<2000'],
    'http_req_failed{escenario:sostenida}': ['rate<0.01'],
    'http_req_failed{escenario:rafaga}': ['rate<0.01'],
    abusador_limitado: ['count>0'],
    'http_reqs{escenario:sostenida}': ['count>0'],   // fuerza a k6 a exportar el conteo por escenario
    'http_reqs{escenario:rafaga}': ['count>0'],
  },
};

function ip(n) { return `10.${(n >> 16) & 255}.${(n >> 8) & 255}.${n & 255}`; }
function cabeceras(origen) { return { 'X-Forwarded-For': origen, 'X-Forwarded-Proto': 'https', Host: 'localhost' }; }

export function legitimo() {
  const n = (exec.scenario.iterationInTest % USUARIOS) + 1;   // 8 000 usuarios, cada uno con su IP
  const r = http.get(`${BASE}/disponibilidad`, { headers: cabeceras(ip(n)) });
  check(r, { 'responde 200': (x) => x.status === 200 });
}

export function abusivo() {
  const r = http.get(`${BASE}/disponibilidad`, {
    headers: cabeceras('203.0.113.250'),
    responseCallback: http.expectedStatuses(200, 403, 429),
  });
  if (r.status === 429 || r.status === 403) { limitados.add(1); }
}
