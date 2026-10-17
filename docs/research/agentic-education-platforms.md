# Research: Agentic Platforms for Education

> Compiled September 2026 — ISB Capstone Team
> Branch: `feature/struggle_flag`

---

## The Big Shift (2025 → 2026)

Most EdTech AI features in 2025 were **generative** — answer a question, summarise text.
Most being built in 2026 are **agentic** — they plan, observe, intervene, loop, and co-ordinate across specialist sub-agents.

> "Most EdTech AI systems being built in 2026 are agentic — with meaningfully different architecture and compliance requirements." — 8allocate

---

## What "Agentic" Means in Education

The best systems today do four things single-model chatbots cannot:

1. **Persistent learner modelling** — AI builds a live "Knowledge Map" per student, tracking conceptual gaps in real time. If a student fails Quantum Computing, an agent automatically triggers a Linear Algebra prerequisite refresher.

2. **Proactive intervention** — They don't wait for a question. They watch behavioural trace data from the LMS and flag struggling students *before* the first midterm.

3. **Multi-agent orchestration** — A coordinator agent (e.g. a "ProfessorAgent") delegates to specialist agents: one for learner modelling, one for content delivery, one for assessment, one for emotional support.

4. **Socratic dialogue, not answer-giving** — The best tutors refuse to just give the answer. They scaffold, ask questions, and generate infinite practice problems matched to real-time performance.

---

## Google Research Work

| Paper / Post | What it does | Link |
|---|---|---|
| Learn Your Way: Reimagining Textbooks with Generative AI | Agentic workflows to auto-generate personalised examples from any textbook | [research.google](https://research.google/blog/learn-your-way-reimagining-textbooks-with-generative-ai/) |
| AI as a Catalyst for Educational Equity | AI personalised learning + virtual tutoring to address global teacher shortages | [research.google](https://research.google/pubs/ai-as-a-catalyst-for-educational-equity-addressing-global-teacher-shortages-and-learning-disparities/) |
| How AI Agents Can Redefine Universal Design for Accessibility | Agents that adapt for diverse learners in real time | [research.google](https://research.google/blog/how-ai-agents-can-redefine-universal-design-to-increase-accessibility/) |
| Social Learning: Collaborative Learning with LLMs | LLMs simulating peer-like social learning and dialogue | [research.google](https://blog.research.google/2024/03/social-learning-collaborative-learning.html) |

---

## Key arXiv Papers

### AUSS — Agentic Unified Student Support System
**arxiv: 2604.16566** · April 2026
Three-tier architecture: student personalisation layer (knowledge maps, adaptive pathways), educator automation layer (content generation, grading), institutional intelligence layer (dropout prediction).
→ **Most directly relevant to this capstone's struggle flag work.**
[Read →](https://arxiv.org/abs/2604.16566)

### LectūraAgents — Hierarchical Multi-Agent Adaptive Learning
**arxiv: 2606.16428** · June 2026
ProfessorAgent co-ordinates validator and executor sub-agents through an orchestration layer with group-chat communication, enabling iterative planning and self-evaluation.
[Read →](https://arxiv.org/abs/2606.16428)

### AgentTutor — Multi-Turn Interactive Teaching
**arxiv: 2601.04219** · January 2026
Multi-turn interactive tutoring outperforms static Q&A systems. Persistence of context across sessions is the key differentiator.
[Read →](https://arxiv.org/pdf/2601.04219)

### DeepTutor — Agentic Personalised Tutoring
**arxiv: 2604.26962** · April 2026
Memory augmentation + long-horizon planning to create tutors that compound knowledge of the same student over time.
[Read →](https://arxiv.org/pdf/2604.26962)

### ITAS — Multi-Agent Architecture for Intelligent Tutoring
**arxiv: 2604.24808** · April 2026
Curriculum planning agent + Socratic dialogue agent + assessment agent communicating through a shared scratchpad.
[Read →](https://arxiv.org/pdf/2604.24808)

### LLM Agents for Education: Advances and Applications (Survey)
**arxiv: 2503.11733** · March 2025
Best survey paper in the space. Covers memory augmentation, tool use, planning, personalisation. Good for citations.
[Read →](https://arxiv.org/pdf/2503.11733)

### LLM-Driven Knowledge Tracing via Dual-Channel Difficulty
**arxiv: 2502.19915** · February 2025
Separates intrinsic question difficulty from student-specific difficulty — directly relevant to the struggle flag concept.
[Read →](https://arxiv.org/pdf/2502.19915)

### Agentic Orchestration for Adaptive Educational Recommendations (ACM WSDM)
Live system serving 6,000+ active users. Hierarchical agent orchestration with parallel domain-specific analysis and graceful degradation under partial failures.
[Read →](https://dl.acm.org/doi/10.1145/3779211.3795741)

---

## Industry Deployments

### Khan Academy — Khanmigo
- Built on Claude 3.5 Sonnet, launched free March 2026 after 2M-student pilot
- Problem: only ~15% of eligible students actively engaged
- Fix: moving from reactive chatbot to proactive agent that surfaces itself during assignments
- Access to learning history improved next-item correctness by **6.1%**
- [Khan Academy Blog](https://blog.khanacademy.org/how-khan-academy-is-building-a-better-ai-tutor-our-most-recent-learnings/) · [Chalkbeat Study](https://www.chalkbeat.org/2026/08/25/ai-tutoring-students-khanmigo-khan-academy-engagement-study/)

### Duolingo
- Autonomous tutoring agents deployed to ~50M students
- Combines spaced repetition with agentic personalisation — agents decide what to surface without explicit user requests

### Galaxy Education — ICAN
- Personalises for students AND gives real-time adaptive support to teachers simultaneously
- Two-sided agent system

---

## Engagement Tracking — Best Practices

The industry standard is **xAPI (Experience API / Tin Can API)** — captures statements like "student X watched slide Y for Z seconds" and sends to a Learning Record Store (LRS).

**For this capstone**: use heartbeat-based tracking (POST every 5s) over the existing FastAPI + Postgres stack rather than a full xAPI LRS. Rationale: lower complexity, same signal quality for a research prototype, and the data lands directly in tables the struggle flag algorithm can query.

Key metrics to track per slide:
- `seconds_active` — time with tab visible (using Page Visibility API)
- `slide_index` — which slide
- Replays / revisits

Struggle signal threshold: >3× class average time on a slide = early struggle indicator before the quiz begins.

Sources: [xAPI — Articulate](https://www.articulate.com/blog/what-is-xapi/) · [Engagement beyond completions](https://synergy-learning.com/blog/track-learner-engagement-beyond-completions/) · [LMS Analytics 2026](https://thirst.io/blog/lms-analytics/)

---

## GitHub Resource List

[Awesome AI / LLM for Education Papers](https://github.com/GeminiLight/awesome-ai-llm4education) — best living list of research papers in this space, organised by topic.
