# Struggle Flag Feature — Team Readout

> Branch: `feature/struggle_flag`
> Date: September 2026
> From: Arathe

---

## What We're Building

The **Struggle Flag System** — a layer on top of the existing quiz platform that detects when a student is genuinely struggling (not just scoring low) and surfaces that signal both to the student and to teachers.

The core insight driving this: **a student who scores 80% on their 5th attempt struggled more than one who scored 75% on their first attempt**. Raw score alone misses this. We're building a richer signal that combines score, attempt count, time on lesson slides, and score trajectory.

---

## What's Done (on `feature/struggle_flag`)

### Student Feedback Page — fully redesigned

The `SubjectFeedbackPage` now has three new sections:

**1. Score Hero**
A circle at the top of the page showing the student's overall best score across all subtopics, with a motivational message and per-subtopic achievement titles.

**2. Focus / Achievement Banner**
- If the student has weak subtopics (best score < 70%): red "Focus on these first" banner listing the subtopic names
- If the student has passed everything: a tiered achievement banner based on their score:
  - 🌱 On Track (70–79%)
  - ⭐ Proficient! (80–89%)
  - 🔥 Advanced! (90–99%)
  - 🏆 Mastery! (100%)

**3. Achievement Title System (15 unique titles)**
Every subtopic card now shows a badge based on *how* the student passed — not just *that* they passed. The title comes from a score band × attempt count matrix:

| Attempts | Pass (70–89%) | High (90–99%) | Perfect (100%) |
|---|---|---|---|
| 1st | 🎯 First Try! | 🌟 Star Performer | 👑 Flawless |
| 2nd | ⚡ Quick Learner | 🔥 Sharp Mind | 💎 Diamond |
| 3rd | 💪 Persistent | 🚀 Rising Star | 🏅 Tenacious |
| 4th | 🔄 Determined | 💡 Breakthrough | 🥇 Champion |
| 5+ | 🌱 Never Give Up | ⭐ Hard Earned | 🏆 Legendary |

Cards are sorted: weak subtopics first → no data → passed.

**Other fixes shipped:**
- Score now uses best attempt per subtopic (not average of all attempts)
- Goal status overrides to "Achieved ✓" if latest score meets the target, even if backend hasn't updated yet
- All colours updated to vibrant, child-friendly palettes (no more light neon green)

---

## What's Coming Next

### 1. Slide Time Tracking (next)
Track how long students spend on each lesson slide (active time only — pauses when tab is hidden). A heartbeat POST every 5s records `(student_id, subtopic_id, slide_index, seconds_active)` to a new `slide_engagement` table.

Struggle signal: if a student spends >3× the class average on a slide, that's a pre-quiz struggle indicator.

### 2. Backend Struggle Flag API
A computed endpoint returning a `struggle_score` (0–1) and `struggle_flag` boolean per subtopic, combining:
- Attempt count vs class average (30%)
- Score trajectory — improving or declining (25%)
- Time on slides vs class average (25%)
- Regression — previously correct questions now wrong (20%)

### 3. Regression Alerts
More prominent UI for when a previously-passing student is backsliding.

### 4. Teacher Dashboard
`/teacher/struggles` — class-wide view of struggle flags per student per subtopic, filterable, colour-coded. Teachers see at a glance who needs to be pulled aside.

---

## Research Backing

We reviewed 19 sources across Google Research, arXiv, and industry deployments. Key findings:

- **AUSS (arxiv 2604.16566)** — the closest academic parallel to our architecture. Three-tier: student personalisation → educator automation → institutional intelligence.
- **LLM Knowledge Tracing (arxiv 2502.19915)** — dual-channel approach separating question difficulty from student-specific difficulty. Validates our approach.
- **Khan Academy Khanmigo** — only 15% of students engaged with reactive AI. The fix: proactive, always-visible help. Same lesson for our struggle flag — surface it to students, don't wait for them to find it.
- **Industry consensus**: heartbeat-based engagement tracking (not flush-on-exit) is the standard in real LMS platforms (Canvas does this). We're following that pattern.

Full research doc: `docs/research/agentic-education-platforms.md`
Full feature spec: `docs/features/struggle-flag.md`

---

## How to Run the Branch

```bash
git checkout feature/struggle_flag
cd frontend && npm run dev          # localhost:5173
cd backend && uvicorn app.main:app --reload --port 8000
# Login: student@demo.school / demo1234
# Navigate to: localhost:5173/feedback → pick Mathematics
```

---

## Questions / Decisions Needed from the Team

1. **Pass threshold** — currently hardcoded at 70%. Should this be configurable per subject or per teacher?
2. **Struggle score weights** — the 30/25/25/20 split is a starting assumption. Do we want to run an experiment to calibrate this against actual student outcomes?
3. **Teacher dashboard access** — who should see it? All teachers, or only the teacher assigned to that subject?
4. **Slide time data retention** — how long do we keep raw heartbeat data? (suggestion: 90 days rolling)
