# Frontend

Vite + React + TypeScript **multi-role web client** for the Agentic Education Platform: student
(live API), administrator (live API including policy chats), and teacher (mock fixtures).

See the [documentation hub](../docs/README.md) for product vision and implementation status.

## Route map

| Route | Role | Data source |
| --- | --- | --- |
| `/`, `/subjects/...`, `/quizzes/...` | Student | Live API + enrollment gate |
| `/admin/dashboard` | Admin | Live `GET /insights/dashboard` + at-risk flags + learning directory (admin home) |
| `/admin/materials/...` | Admin | Live `GET /me/learning-directory` + curriculum PDF ingest |
| `/admin/documents` | Admin | Live knowledge-document upload/list (ingest status) |
| `/admin/policy` | Admin | Live chats API (`src/api/chats.ts`) |
| `/teacher/...` | Teacher | Mock (`src/mocks/teacherAssignments.ts`) |

Post-login routing uses `MeResponse.roles` (priority admin → teacher → student): administrators →
`/admin`, teachers → `/teacher`, students → `/` (then enrollment gate).

## Prerequisites

- Node 18+ (20+ preferred)
- API running at `http://127.0.0.1:8000` (see [repo README](../README.md))

## Setup

```bash
cd frontend
cp .env.example .env
npm install
npm run dev
```

Open `http://localhost:5173`.

## Scripts

| Command | Purpose |
| --- | --- |
| `npm run dev` | Dev server on port 5173 |
| `npm run build` | Typecheck + production build |
| `npm test` | Vitest unit/component tests |
| `npm run preview` | Preview production build |

## Student POC flow

1. **Sign in** on `/login` (`student@demo.school` / `demo1234`)
2. **Enroll** in Grade 8 Math (`POST /api/v1/me/enrollments/poc-math`), or use **Quick demo**
3. Open a topic lesson, then **Start the quiz**
4. Submit answers; review score (pass ≥ 70%). Correct option labels are never shown.

## Admin flow

1. **Sign in** as `admin@demo.school` / `demo1234` (real JWT)
2. Browse `/admin/materials` — grades, subjects, topics from `GET /me/learning-directory`
3. Use **Upload** on Materials (or a unit’s lesson row) to POST a curriculum PDF and poll ingest status
4. `/admin/documents` — upload/list knowledge PDFs and watch processing/ready/failed
5. `/admin/policy` — live policy assistant (multi-chat via `/chats*`; retrieval over indexed docs; set `OPENROUTER_API_KEY` for real LLM stages)

Administrators do not require student enrollments to read the learning directory.

## Teacher flow (mock)

1. In dev, use **Enter as teacher** on the login page
2. Explore `/teacher` classes, roster, and subject materials — all from fixtures
3. No JWT is issued; session persists under `localStorage` key `ep_dev_mock_role`

## DEV shortcuts and mock rules

In `npm run dev` only, the login page offers:

- **Enter as admin** — real `signIn("admin@demo.school", "demo1234")` (JWT). Requires seeded
  administrator on the API.
- **Enter as teacher** — fixture `MeResponse` only (no JWT), persisted under `ep_dev_mock_role`.
- **Quick demo** — calls `POST /me/demo/bootstrap` after student sign-in.

Sign out clears mock role and tokens. Real email/password sign-in also clears any mock role.
Production builds do not show dev role buttons. A leftover admin fixture session cannot load
materials (clear error; use real admin sign-in). Teacher fixtures remain the only mock data path;
policy chat is live.

## Tests

| Location | Covers |
| --- | --- |
| `src/auth/gates.test.tsx` | Enrollment and role gates |
| `src/pages/admin/*.test.tsx` | Admin materials browser |
| `src/pages/teacher/*.test.tsx` | Teacher workspace fixtures |

UI mockups for design reference: [docs/assets/](../docs/assets/).
