/** Fixture replies for the admin Policy assistant (mock only — no RAG). */

export type PolicyCitation = {
  id: string;
  label: string;
  excerpt: string;
};

export type PolicyChatRole = "user" | "assistant";

export type PolicyChatMessage = {
  id: string;
  role: PolicyChatRole;
  content: string;
  citations?: PolicyCitation[];
  createdAt: string;
};

export const POLICY_CHAT_DISCLAIMER =
  "Mock retrieval only. Answers and citations are fixtures — no policy handbook or vector search is connected.";

export const POLICY_CHAT_SEED: PolicyChatMessage[] = [
  {
    id: "msg-seed-1",
    role: "assistant",
    content:
      "I can help you look up school policy topics (attendance, assessments, enrollment). Ask a question to see a fixture reply with fake citations.",
    createdAt: "2026-08-10T09:00:00.000Z",
  },
];

type FixtureReply = {
  match: RegExp;
  content: string;
  citations: PolicyCitation[];
};

const FIXTURE_REPLIES: FixtureReply[] = [
  {
    match: /attend|absent|late/i,
    content:
      "Students must report absences by 9:00 AM via the parent portal. Three unexcused absences in a term trigger a counselor check-in.",
    citations: [
      {
        id: "cite-att-1",
        label: "Policy handbook §3.2 — Attendance",
        excerpt: "Unexcused absences are logged daily; escalation begins at three per term.",
      },
      {
        id: "cite-att-2",
        label: "Operations memo 2025-11 — Parent portal",
        excerpt: "Absence reports submitted after 9:00 AM are marked late for same-day coverage.",
      },
    ],
  },
  {
    match: /assess|exam|quiz|grade|mark/i,
    content:
      "Formative quizzes may be retaken once within the topic window. Summative exams require a documented makeup plan approved by the subject lead.",
    citations: [
      {
        id: "cite-assess-1",
        label: "Policy handbook §5.1 — Assessment",
        excerpt: "One formative retake is allowed before the topic mastery quiz closes.",
      },
      {
        id: "cite-assess-2",
        label: "Academic integrity guide §2",
        excerpt: "Makeup summatives need subject-lead approval and a recorded reason code.",
      },
    ],
  },
  {
    match: /enroll|transfer|section|class/i,
    content:
      "Section transfers mid-term require administrator approval and both class teachers’ acknowledgement. Capacity and timetable conflicts are checked first.",
    citations: [
      {
        id: "cite-enroll-1",
        label: "Policy handbook §2.4 — Enrollment changes",
        excerpt: "Mid-term section moves are exceptional and must preserve instructional continuity.",
      },
    ],
  },
];

const DEFAULT_REPLY: FixtureReply = {
  match: /.*/,
  content:
    "I don’t have a specific fixture for that yet. Try asking about attendance, assessments, or enrollment — those map to sample handbook citations.",
  citations: [
    {
      id: "cite-default-1",
      label: "Policy handbook §1.0 — Scope",
      excerpt: "This assistant surfaces mocked excerpts for demo navigation only.",
    },
  ],
};

let messageSeq = 0;

export function nextPolicyMessageId(prefix = "msg"): string {
  messageSeq += 1;
  return `${prefix}-${Date.now()}-${messageSeq}`;
}

/** Pick a fixture assistant reply for the latest user text. */
export function replyToPolicyQuestion(userText: string): Omit<PolicyChatMessage, "id" | "createdAt"> {
  const hit = FIXTURE_REPLIES.find((r) => r.match.test(userText)) ?? DEFAULT_REPLY;
  return {
    role: "assistant",
    content: hit.content,
    citations: hit.citations,
  };
}
