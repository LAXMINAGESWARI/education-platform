# API load test

The script is `k6-api.js`. `API_URL` and `WEB_URL` are required. There is no default host.

Stages are 2 minutes at 10, 50, 100, 200, 300, and 400 requests per second, then 6 minutes idle so scale-down is visible. `handleSummary` prints `K6_STAGE_SUMMARY` JSON with RPS, p50, p95, p99, and error rate per stage. p95 under 800ms and errors under 1% are thresholds for the report. They do not abort the run.

Authenticated reads (60%):

- `GET /api/v1/materials` (`backend/src/education_platform/modules/materials/router.py`)
- `GET /api/v1/me/enrollments` and `GET /api/v1/me/learning-directory` (`modules/academics/router.py`)
- `GET /api/v1/me/feedback` (`modules/assessments/router.py`)
- `GET /api/v1/insights/dashboard` with the teacher token (`modules/insights/router.py`)

25% is `GET /health/ready` plus the web Service `/` and `/favicon.png`. 15% is `POST /api/v1/chats/{id}/messages` with `{"content":"..."}` (`modules/assistant/router.py`, `PostMessageIn`). `setup()` logs in `student1@alnoor.school`, `student2@alnoor.school`, `student3@alnoor.school`, `meera.krishnan@alnoor.school`, and `fatima.almansouri@alnoor.school`. The password is `demo1234`.

From a laptop, through the Traefik NodePort:

```sh
API_URL=http://192.0.2.10:30080 WEB_URL=http://192.0.2.10:30080 HOST=curioed.online \
  k6 run deploy/loadtest/k6-api.js
```

The load-test Helm values run the same file as a Job. `collect.sh` records HPA replicas, pod and node usage, MemAvailable, Postgres session counts, and k6 percentiles. It deletes that Job if available memory drops below 3 GiB or `PROD_HEALTH_URL` fails.

The chart README covers install, the mock, and cleanup.
