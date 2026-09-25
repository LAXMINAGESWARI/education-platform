# Feature: Struggle Flag System

> Branch: `feature/struggle_flag`
> Status: 🟡 In Progress
> Owner: Arathe (ISB Capstone)
> Last updated: September 2026

---

## Overview

The Struggle Flag system detects when a student is genuinely struggling with a subtopic — not just scoring low, but showing behavioural patterns that signal difficulty — and surfaces that signal to both the student (via the feedback page) and eventually to teachers (via a class dashboard).

The core insight: **quiz score alone is not enough**. A student who scores 80% on their 5th attempt struggled more than a student who scored 75% on their first attempt. The system combines score trajectory, attempt count, time-on-slides, and regression patterns to produce a richer picture.

---

## What's Been Built (Completed ✅)

### 1. SubjectFeedbackPage — Student View (`frontend/src/pages/SubjectFeedbackPage.tsx`)

**Score Hero**
- Circular badge showing the student's overall best score (average of best score per subtopic — not average of all attempts)
- Colour-coded: green ≥70%, red <70%
- Per-subtopic achievement titles shown inline

**Achievement Title System** (`attemptTitle` function)
- 15 unique titles across a score band × attempt count matrix
- 3 bands: `pass` (70–89%), `high` (90–99%), `perfect` (100%)
- 5 attempt tiers per band (1st, 2nd, 3rd, 4th, 5+)
- Examples: "🎯 First Try!" / "💪 Persistent" / "👑 Flawless" / "💎 Diamond"

**Focus / Achievement Banner**
- Red banner when weak subtopics remain (`best score < 70%`), listing subtopics by name
- Tiered achievement banner when all subtopics passed:
  - 🌱 **On Track** (70–79%) — teal `#0f766e`
  - ⭐ **Proficient!** (80–89%) — purple `#6b21a8`
  - 🔥 **Advanced!** (90–99%) — indigo `#312e81`
  - 🏆 **Mastery!** (100%) — amber `#78350f`

**Subtopic Cards**
- Sorted: weak first → no data → passed
- Attempt chips with trend arrows (↑ green / ↓ red)
- Achievement badge per card using the title matrix
- Goal section with frontend override: if latest score ≥ target → show "Achieved ✓"

---

## What's In Progress (🟡)

### 2. Slide Time Tracking
**Goal:** Record how long students spend on each lesson slide (active time only, tab-visible) before unlocking the quiz.

**Architecture:**
- Frontend: heartbeat POST every 5s from `LessonSlidesPage.tsx` using `Page Visibility API` to pause when tab is hidden
- Backend: `POST /api/v1/me/lessons/{subtopic_id}/engagement` endpoint
- DB: new `slide_engagement` table

**Schema:**
```sql
CREATE TABLE slide_engagement (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id    UUID REFERENCES users(id),
  subtopic_id   UUID REFERENCES subtopics(id),
  slide_index   INTEGER,
  seconds_active INTEGER,
  recorded_at   TIMESTAMPTZ DEFAULT now()
);
```

**Struggle signal:** `seconds_active > 3 × class_avg_seconds` for the same slide = pre-quiz struggle indicator.

---

### 3. Backend Struggle Flag API
**Goal:** A computed endpoint that returns a `struggle_score` (0.0–1.0) and `struggle_flag` boolean per subtopic per student.

**Endpoint:** `GET /api/v1/me/subjects/{subject_id}/feedback` (extend existing) or `GET /api/v1/subtopics/{id}/struggle`

**Input signals:**
| Signal | Weight |
|---|---|
| Attempt count vs class avg | 30% |
| Score trajectory (improving / declining) | 25% |
| Time on slides vs class avg | 25% |
| Regression (previously correct → now wrong) | 20% |

**Output:**
```json
{
  "subtopic_id": "...",
  "struggle_score": 0.72,
  "struggle_flag": true,
  "signals": {
    "attempts_above_avg": true,
    "declining_trajectory": false,
    "high_slide_time": true,
    "regression_detected": true
  }
}
```

---

### 4. Regression Alerts
**Goal:** Surface a warning on the feedback page when a previously-passing student's score is declining.

Already partially implemented — `RegressionOut` type exists in the API types and `regression` data is passed to `SubtopicCard`. Need to make the alert more prominent.

---

### 5. Teacher Dashboard
**Goal:** A `/teacher/struggles` page showing all students, their struggle flags per subtopic, colour-coded.

**Columns:** Student name · Subtopic · Struggle score · Attempts · Slide time · Last attempt date
**Filters:** Subject · Subtopic · Flag only

---

## Key Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| Score metric | Best score per subtopic (not avg) | Fairer — rewards eventual mastery |
| Pass threshold | 70% | Matches curriculum standard |
| Engagement tracking | Heartbeat (every 5s) not flush-on-exit | Survives tab crashes, richer signal |
| Struggle standard | xAPI-inspired, custom FastAPI+Postgres | Avoids LRS complexity for prototype |
| Title system | Score band × attempts matrix | Captures both achievement level and effort |

---

## Scoring Logic Reference

```typescript
// computeOverallScore — average of best score per subtopic
function computeOverallScore(subtopics) {
  const scores = subtopics
    .filter(s => s.attempts.length > 0)
    .map(s => Math.max(...s.attempts.map(a => pct(a.percent) ?? 0)));
  return scores.reduce((a, b) => a + b, 0) / scores.length;
}

// attemptTitle — 15-title matrix (score band × attempt count)
// band: "pass" (70-89) | "high" (90-99) | "perfect" (100)
// index: Math.min(attempts - 1, 4)
```

---

## Files Changed

| File | Change |
|---|---|
| `frontend/src/pages/SubjectFeedbackPage.tsx` | All student feedback UI (hero, banners, cards, titles) |
| `frontend/src/pages/LessonSlidesPage.tsx` | *(Planned)* Heartbeat engagement tracking |
| `backend/app/routers/engagement.py` | *(Planned)* Slide engagement endpoint |
| `backend/app/models/slide_engagement.py` | *(Planned)* DB model |
| `backend/alembic/versions/xxxx_slide_engagement.py` | *(Planned)* Migration |

---

## Research Backing

See [`docs/research/agentic-education-platforms.md`](../research/agentic-education-platforms.md) for the full research digest.

Key papers:
- AUSS multi-agent framework (arxiv 2604.16566) — architecture parallel
- LLM-Driven Knowledge Tracing (arxiv 2502.19915) — struggle detection methodology
- Khan Academy Khanmigo case study — engagement tracking lessons
