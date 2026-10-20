/** Shared API types mirroring backend Pydantic schemas. */

export type TokenResponse = {
  access_token: string;
  refresh_token: string;
  token_type: string;
};

export type MeResponse = {
  id: string;
  email: string;
  full_name: string;
  institution_id: string;
  roles: string[];
  student_profile_id: string | null;
  status: string;
};

export type ProvisionStudentRequest = {
  email: string;
  password: string;
  full_name: string;
  student_identifier: string;
  institution_name?: string;
};

export type LoginRequest = {
  email: string;
  password: string;
  institution_name?: string;
};

export type GradeEnrollment = {
  id: string;
  academic_period_id: string;
  academic_period_name: string;
  academic_period_status: string;
  grade_id: string;
  grade_name: string;
  status: string;
};

export type SubjectEnrollment = {
  id: string;
  grade_subject_offering_id: string;
  academic_period_id: string;
  academic_period_name: string;
  grade_name: string;
  subject_id: string;
  subject_code: string;
  subject_name: string;
  status: string;
};

export type EnrollmentSummary = {
  grade_enrollments: GradeEnrollment[];
  subject_enrollments: SubjectEnrollment[];
  eligible?: boolean;
  blocked_reason?: string | null;
};

export type DemoBootstrapResponse = {
  subject_id: string;
  topic_id: string;
  topic_title: string;
  message: string;
};

export type AttemptHistoryItem = {
  id: string;
  attempt_number: number;
  status: string;
  score_percent: string | number | null;
  passed: boolean | null;
  started_at: string | null;
  submitted_at: string | null;
};

export type QuizSummary = {
  id: string;
  title: string;
  scope: "subtopic_mastery" | "topic_mastery";
  available: boolean;
  unlocked: boolean;
  locked_reason: string | null;
  pass_threshold_percent: number;
  attempt_count: number;
  best_score_percent: number | null;
  passed: boolean;
  in_progress_attempt_id: string | null;
  recent_attempts: AttemptHistoryItem[];
};

export type MaterialProgress = {
  status: "opened" | "completed";
  opened_at: string;
  last_opened_at: string;
  completed_at: string | null;
  last_unit_ordinal: number | null;
  source_material_version_id: string;
};

export type SubtopicNode = {
  id: string;
  title: string;
  slug: string;
  sequence: number;
  has_lesson: boolean;
  lesson_completed: boolean;
  progress_percent: number;
  progress?: MaterialProgress | null;
  source_material_version_id?: string | null;
  quiz: QuizSummary | null;
};

export type TopicNode = {
  id: string;
  title: string;
  slug: string;
  sequence: number;
  progress_percent: number;
  complete: boolean;
  objectives: string[];
  subtopics: SubtopicNode[];
  overall_quiz: QuizSummary | null;
  has_topic_lesson?: boolean;
  topic_lesson_completed?: boolean;
  topic_lesson_progress?: MaterialProgress | null;
  topic_source_material_version_id?: string | null;
};

export type SubjectNode = {
  id: string;
  code: string;
  name: string;
  grade_name: string;
  academic_period_name: string;
  progress_percent: number;
  done?: number;
  total?: number;
  topics: TopicNode[];
};

export type LearningDirectory = {
  subjects: SubjectNode[];
};

export type RemediationOut = {
  material_id: string | null;
  material_title: string | null;
  retake_quiz_id: string | null;
};

export type SubtopicAttemptScore = {
  sequence: number;
  percent: string | number;
  submitted_at: string | null;
};

export type GoalOut = {
  topic_id: string;
  topic_name: string;
  target_percent: string | number;
  due_at: string;
  current_percent: string | number | null;
  status: "on_track" | "achieved" | "missed";
};

export type SubtopicFeedback = {
  subtopic_id: string;
  subtopic_name: string;
  topic_id?: string;
  topic_name: string;
  percent: string | number;
  question_count: number;
  attempts: SubtopicAttemptScore[];
  percent_easy: string | number | null;
  easy_question_count: number;
  percent_medium: string | number | null;
  medium_question_count: number;
  percent_hard: string | number | null;
  hard_question_count: number;
  prior_percent: string | number | null;
  delta: string | number | null;
  class_avg_percent: string | number | null;
  class_sample_size: number;
  is_weak: boolean | null;
  remediation: RemediationOut | null;
  goal: GoalOut | null;
};

export type RegressionOut = {
  subtopic_id: string;
  subtopic_name: string;
  regressed_question_count: number;
};

export type ReviewNudgeOut = {
  subtopic_id: string;
  subtopic_name: string;
  topic_name: string;
  days_since_last_attempt: number;
  due_interval_days: number;
  remediation: RemediationOut | null;
};

export type TrajectoryOut = {
  subtopic_id: string;
  subtopic_name: string;
  trend: "improving" | "flat" | "declining";
  recent_change: string | number | null;
  evidence_level: "low" | "medium";
};

export type FeedbackHighlights = {
  due_reviews: ReviewNudgeOut[];
  trajectories: TrajectoryOut[];
};

export type FeedbackSummaryStatus = "ready" | "pending" | "fallback";

export type FeedbackTrend = "improving" | "flat" | "declining" | null;

export type FeedbackBadge = "not_attempted" | "keep_going" | "passed";

export type FeedbackBand = "strong" | "okay" | "needs_work";

export type FeedbackTopicRef = {
  topic_id: string;
  topic_name: string;
  best_score_percent: string | number;
};

export type FeedbackTopicScore = {
  topic_id: string;
  topic_name: string;
  sequence: number;
  best_score_percent: string | number | null;
  latest_score_percent: string | number | null;
  attempt_count: number;
  trend: FeedbackTrend;
  trend_delta: string | number | null;
  badge: FeedbackBadge;
  quiz_id: string | null;
  has_lesson: boolean;
  goal: GoalOut | null;
  attempt_scores: FeedbackAttemptScore[];
  concepts: FeedbackSubtopicStrength[];
  regression_count: number | null;
  class_avg_percent: string | number | null;
  class_sample_size: number;
};

export type FeedbackAchievement = "mastery" | "advanced" | "proficient" | "on_track";

export type FeedbackFocus = {
  topic_name: string;
  topic_id: string;
  gap_percent: number;
};

export type FeedbackSubjectCard = {
  subject_id: string;
  subject_name: string;
  grade_name: string;
  score_percent: string | number | null;
  quizzes_taken: number;
  trend: FeedbackTrend;
  trend_delta: string | number | null;
  strongest_topic: FeedbackTopicRef | null;
  weakest_topic: FeedbackTopicRef | null;
  concepts_mastered: number;
  concepts_attempted: number;
  focus: FeedbackFocus | null;
  achievement: FeedbackAchievement | null;
};

export type FeedbackOverview = {
  score_percent: string | number | null;
  subjects: FeedbackSubjectCard[];
};

export type FeedbackSubjectDetail = {
  subject_id: string;
  subject_name: string;
  grade_name: string;
  score_percent: string | number | null;
  quizzes_taken: number;
  trend: FeedbackTrend;
  trend_delta: string | number | null;
  topics_needing_attention: string[];
  summary: string;
  summary_status: "ready" | "fallback";
  topics: FeedbackTopicScore[];
  concepts_mastered: number;
  concepts_attempted: number;
  focus: FeedbackFocus | null;
  achievement: FeedbackAchievement | null;
};

export type FeedbackSubtopicStrength = {
  subtopic_id: string;
  name: string;
  correct_count: number;
  answered_count: number;
  percent: string | number;
  band: FeedbackBand;
};

export type FeedbackAttemptScore = {
  attempt_number: number;
  score_percent: string | number;
};

export type FeedbackDifficulty = "easy" | "medium" | "hard";

export type FeedbackDifficultyAccuracy = {
  difficulty: FeedbackDifficulty;
  correct_count: number;
  answered_count: number;
  percent: string | number;
};

export type FeedbackTopicDetail = {
  subject_id: string;
  subject_name: string;
  topic_id: string;
  topic_name: string;
  best_score_percent: string | number | null;
  latest_score_percent: string | number | null;
  attempt_count: number;
  trend: FeedbackTrend;
  trend_delta: string | number | null;
  badge: FeedbackBadge;
  quiz_id: string | null;
  has_lesson: boolean;
  summary: string;
  subtopics: FeedbackSubtopicStrength[];
  goal: GoalOut | null;
  concepts_mastered: number;
  concepts_attempted: number;
  average_score_percent: string | number | null;
  attempt_scores: FeedbackAttemptScore[];
  questions_answered: number | null;
  questions_correct: number | null;
  accuracy_percent: string | number | null;
  time_spent_seconds: number | null;
  difficulty_accuracy: FeedbackDifficultyAccuracy[];
};

export type SubjectFeedbackDashboard = {
  subject_id: string;
  subject_name: string;
  overall_percent: string | number | null;
  attempt_count: number;
  subtopics: SubtopicFeedback[];
  strength_subtopic_id: string | null;
  top_focus_subtopic_ids: string[];
  regressions: RegressionOut[];
  summary: string;
  summary_status: FeedbackSummaryStatus;
};

export type LessonSlide = {
  number: number;
  title: string;
  content: string;
  sectionTitle?: string;
};

export type LessonMaterial = {
  id: string;
  title: string;
  markdown: string;
  slides: LessonSlide[];
  source_material_version_id: string;
  published_at?: string | null;
  progress: MaterialProgress | null;
  quiz_unlocked: boolean;
  quiz_id: string | null;
};

export type MaterialProgressUpdate = {
  status: "opened" | "completed";
  last_unit_ordinal?: number | null;
};

export type QuizOption = {
  label: string;
  text: string;
};

export type QuizQuestion = {
  number: number;
  difficulty: string | null;
  prompt: string;
  options: QuizOption[];
};

export type QuizMaterial = {
  id: string;
  title: string;
  questions: QuizQuestion[];
  pass_threshold_percent: number;
  duration_seconds: number | null;
  max_attempts: number | null;
  result_release_mode: string;
};

export type StartAttemptResponse = {
  id: string;
  quiz_id: string;
  quiz_version_id: string;
  attempt_number: number;
  status: string;
  started_at: string | null;
  deadline_at: string | null;
  pass_threshold_percent: number;
  result_release_mode: string;
  title: string;
  scope: "subtopic_mastery" | "topic_mastery";
  target_id: string;
  questions: QuizQuestion[];
};

export type AnswerSubmission = {
  question_number: number;
  selected_option_label: string;
};

export type SubmitAttemptRequest = {
  answers: AnswerSubmission[];
};

export type AttemptAnswerOut = {
  question_number: number;
  selected_option_label: string | null;
  is_correct: boolean | null;
  marks_awarded: string | number | null;
};

export type AttemptResult = {
  id: string;
  quiz_id: string;
  target_id: string | null;
  scope: "subtopic_mastery" | "topic_mastery" | null;
  attempt_number: number;
  status: string;
  started_at: string | null;
  submitted_at: string | null;
  scored_at: string | null;
  score_raw: string | number | null;
  score_percent: string | number | null;
  pass_threshold_percent: number | null;
  passed: boolean | null;
  review_available: boolean;
  answers: AttemptAnswerOut[];
};

/** @deprecated flat catalog shape retained for transitional callers */
export type TopicSummary = {
  id: string;
  title: string;
  has_lesson: boolean;
  has_quiz: boolean;
};

/** Admin RAG ingest lifecycle (materials + knowledge docs). */
export type IngestLifecycleStatus =
  | "draft"
  | "processing"
  | "ready"
  | "published"
  | "failed"
  | "superseded"
  | "archived";

export type RunPhase =
  | "indexing"
  | "outlining"
  | "outline_review"
  | "generating"
  | "qa_review"
  | "published"
  | "failed"
  | "discarded";

export type GenerationJobStatus = {
  kind: string;
  status: string;
};

export type GenerationOutlineNode = {
  id: string;
  parent_id: string | null;
  slug: string;
  title: string;
  token_mass: number;
  prerequisite_score: string;
  centrality: string;
  weight: string;
  quota: number | null;
  matched_subtopic_id: string | null;
  force_create: boolean;
  accepted_subtopic_id: string | null;
  proposed_outcomes: string[];
  sequence: number;
};

export type GenerationOutline = {
  target_item_count: number;
  nodes: GenerationOutlineNode[];
};

export type OutlineNodeEdit = {
  id: string;
  parent_id: string | null;
  slug: string;
  title: string;
  weight: string;
  matched_subtopic_id: string | null;
  force_create: boolean;
  proposed_outcomes: string[];
  sequence: number;
};

export type OutlinePatch = {
  target_item_count?: number;
  nodes: OutlineNodeEdit[];
};

export type ChangeKind =
  | "curriculum_alignment"
  | "factual_accuracy"
  | "pedagogy"
  | "structure"
  | "assessment_validity"
  | "answer_key"
  | "accessibility"
  | "other";

export type OutlineField =
  | "whole_node"
  | "title"
  | "parent"
  | "weight"
  | "proposed_outcomes"
  | "subtopic_match"
  | "outline_structure";

export type LessonField = "whole_section" | "heading" | "body" | "order";

export type QuizField =
  | "whole_item"
  | "prompt"
  | "options"
  | "answer_key"
  | "rationales"
  | "order";

export type ReviewerStateKind = "pending" | "approved" | "changes_requested" | "abstained";

export type OutlineNodeSnapshot = {
  node_key: string;
  parent_node_key: string | null;
  slug: string;
  title: string;
  token_mass: number;
  prerequisite_score: string;
  centrality: string;
  weight: string;
  matched_subtopic_id: string | null;
  force_create: boolean;
  proposed_outcomes: string[];
  sequence: number;
  quota: number;
};

export type OutlineSnapshot = {
  target_item_count: number;
  nodes: OutlineNodeSnapshot[];
};

export type ActorStamp = {
  user_id?: string | null;
  display_name: string;
  job_id?: string | null;
  model?: string | null;
  occurred_at: string;
};

export type OutlineRevision = {
  stage: "outline";
  id: string;
  run_id: string;
  number: number;
  parent_id: string | null;
  origin: string;
  snapshot_hash: string;
  created_at: string;
  created_by: ActorStamp;
  snapshot: OutlineSnapshot;
};

export type LessonSectionSnapshot = {
  section_key: string;
  heading: string;
  markdown: string;
  sequence: number;
};

export type QuizItemSnapshot = {
  item_key: string;
  question_id: string;
  question_version_id: string;
  subtopic_id: string;
  prompt: string;
  options: Record<string, string>;
  correct_label: string;
  correct_rationale: string;
  distractor_rationales: Record<string, string>;
  sequence: number;
};

export type ContentSnapshot = {
  rendered_lesson_markdown: string;
  lesson_sections: LessonSectionSnapshot[];
  quiz_version_id: string;
  quiz_items: QuizItemSnapshot[];
};

export type ContentRevision = {
  stage: "qa";
  id: string;
  run_id: string;
  number: number;
  parent_id: string | null;
  origin: string;
  snapshot_hash: string;
  created_at: string;
  created_by: ActorStamp;
  snapshot: ContentSnapshot;
};

export type FrozenRevision = OutlineRevision | ContentRevision;

export type ReviewRound = {
  id: string;
  run_id: string;
  revision_id: string;
  stage: string;
  number: number;
  opened_at: string;
  due_at: string;
  sealed_at: string | null;
};

export type ChangeRequest = {
  id: string;
  decision_id: string;
  revision_id: string;
  author: ActorStamp;
  target_kind: "outline_node" | "outline_document" | "lesson_section" | "quiz_item";
  node_key: string | null;
  section_key?: string | null;
  item_key?: string | null;
  field: OutlineField | LessonField | QuizField;
  kind: ChangeKind;
  comment: string;
};

export type TeacherDecision = {
  id: string;
  round_id: string;
  revision_id: string;
  verdict: "approve" | "changes_requested";
  reviewer: ActorStamp;
  requests: ChangeRequest[];
};

export type ReviewerState = {
  reviewer_user_id: string;
  display_name: string;
  state: ReviewerStateKind;
  decision: TeacherDecision | null;
};

export type RequestThread = {
  change_request: ChangeRequest;
  target_title: string | null;
};

export type RoundCloseRecord = {
  id: string;
  round_id: string;
  base_revision_id: string;
  action: string;
  actor: ActorStamp;
  collated_request_ids: string[];
  rationale: string | null;
};

export type OutlineNodeDelta = {
  node_key: string;
  before: OutlineNodeSnapshot | null;
  after: OutlineNodeSnapshot | null;
};

export type OutlineRevisionDiff = {
  kind: "outline";
  from_revision_id: string;
  to_revision_id: string;
  target_item_count: { before: number | null; after: number | null };
  nodes: OutlineNodeDelta[];
};

export type ContentRevisionDiff = {
  kind: "content";
  from_revision_id: string;
  to_revision_id: string;
  sections: {
    section_key: string;
    before: LessonSectionSnapshot | null;
    after: LessonSectionSnapshot | null;
  }[];
  quiz_items: {
    item_key: string;
    before: QuizItemSnapshot | null;
    after: QuizItemSnapshot | null;
  }[];
};

export type ReviewWorkspace = {
  run_id: string;
  topic_id: string;
  phase: RunPhase;
  published_locked: boolean;
  viewer_is_closer: boolean;
  active_revision: FrozenRevision;
  diff_from_parent: OutlineRevisionDiff | ContentRevisionDiff | null;
  open_round: ReviewRound | null;
  reviewer_states: ReviewerState[];
  request_threads: RequestThread[];
  history: RoundCloseRecord[];
};

export type DraftChangeRequest =
  | {
      target: { kind: "outline_node"; node_key: string } | { kind: "outline_document" };
      field: OutlineField;
      kind: ChangeKind;
      comment: string;
    }
  | {
      target: { kind: "lesson_section"; section_key: string };
      field: LessonField;
      kind: ChangeKind;
      comment: string;
    }
  | {
      target: { kind: "quiz_item"; item_key: string };
      field: QuizField;
      kind: ChangeKind;
      comment: string;
    };

export type GenerationAssistantTurn = {
  revision_id: string;
  message: string;
  target?: DraftChangeRequest["target"];
};

export type GenerationAssistantReply = {
  content: string;
  citations: { id: string; label: string; excerpt: string }[];
  draft_change_request: DraftChangeRequest | null;
};

export type TeacherDecisionCommand =
  | { revision_id: string; verdict: "approve"; snapshot_hash?: string }
  | {
      revision_id: string;
      verdict: "changes_requested";
      snapshot_hash?: string;
      requests: DraftChangeRequest[];
    };

export type CloseRoundCommand =
  | { revision_id: string; action: "rewrite"; snapshot_hash?: string }
  | { revision_id: string; action: "accept_current"; snapshot_hash?: string }
  | {
      revision_id: string;
      action: "override_and_accept";
      snapshot_hash?: string;
      rationale: string;
      replacement: OutlineSnapshot;
    }
  | { revision_id: string; action: "discard"; snapshot_hash?: string; rationale?: string };

export type CloseRoundResult = {
  kind: "rewrite_queued" | "outline_accepted" | "content_accepted" | "run_discarded";
  job_id?: string | null;
  run_id?: string | null;
  accepted_revision_id?: string | null;
};

export type AcceptedGenerationRun = {
  run_id: string;
  topic_id: string;
  phase: RunPhase;
};

export type BloomLevel = "remember" | "understand" | "apply" | "analyze";

export type GenerationQaItem = {
  question_id: string;
  question_version_id: string;
  prompt: string;
  options: Record<string, string>;
  correct_label: string;
  correct_rationale: string;
  distractor_rationales: Record<string, string>;
  subtopic_id: string;
  sequence: number;
  bloom?: BloomLevel | null;
  misconception_labels?: string[];
  key_disputed?: boolean;
  blind_answer?: string | null;
  check_attempts?: number | null;
  check_verdict?: string | null;
  difficulty?: string | null;
  subtopic_title?: string | null;
  judge_needs_review?: boolean;
};

export type GenerationStepId =
  | "indexing"
  | "outlining"
  | "outline_review"
  | "lesson"
  | "items"
  | "ready";

export type GenerationStepStatus = "complete" | "current" | "pending" | "failed";

export type GenerationStepTiming = {
  step: GenerationStepId;
  label: string;
  status: GenerationStepStatus;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  error: string | null;
  available: boolean;
  hint: string | null;
};

export type GenerationChunkPreview = {
  ordinal: number;
  page_number: number | null;
  section_heading: string | null;
  preview: string;
};

export type JudgeCheckView = {
  check_id: string;
  kind: string;
  decision: string;
  summary: string;
  probabilities: Record<string, number>;
};

export type JudgeReadout = {
  subject_kind: string;
  subject_key: string | null;
  label: string;
  decision: string;
  reasons: string[];
  checks: JudgeCheckView[];
  model: string | null;
  question_version_id?: string | null;
};

export type GenerationStepDetail = {
  timing: GenerationStepTiming;
  indexing: {
    page_count: number | null;
    chunk_count: number;
    chunks: GenerationChunkPreview[];
    truncated: boolean;
  } | null;
  outline: {
    target_item_count: number;
    nodes: Array<{
      id: string;
      parent_id: string | null;
      title: string;
      sequence: number;
      quota: number | null;
      proposed_outcomes: string[];
      chunks: GenerationChunkPreview[];
    }>;
    unmapped_chunks: GenerationChunkPreview[];
  } | null;
  outline_review: {
    rounds: Array<{
      id: string;
      stage: string;
      number: number;
      opened_at: string;
      sealed_at: string | null;
      decisions: Array<{ reviewer: string; verdict: string }>;
      comments: Array<{
        author: string;
        kind: string;
        field: string;
        comment: string;
        target_title: string | null;
      }>;
      closure_action: string | null;
      closed_by: string | null;
      rationale: string | null;
    }>;
  } | null;
  lesson: {
    markdown: string | null;
    published_lesson_version_id: string | null;
    judge?: JudgeReadout[];
    needs_review?: boolean;
  } | null;
  items: {
    count: number;
    difficulty_mix: { easy: number; medium: number; hard: number; unlabeled: number };
    bloom_mix: {
      remember: number;
      understand: number;
      apply: number;
      analyze: number;
      unlabeled: number;
    };
    subtopic_counts: Array<{ subtopic_id: string; title: string; count: number }>;
    passed: number | null;
    fixed_on_retry: number | null;
    dropped: number | null;
    key_disputed: number;
    items: GenerationQaItem[];
    judge?: JudgeReadout[];
  } | null;
  ready: {
    phase: string;
    published: boolean;
    published_at: string | null;
    created_at: string;
    updated_at: string;
    lesson_version_id: string | null;
    quiz_version_id: string | null;
  } | null;
};

export type ItemValidationStats = {
  passed: number;
  fixed_on_retry: number;
  dropped: number;
};

export type PublishedTopic = {
  run_id: string;
  topic_id: string;
  lesson_version_id: string;
  quiz_version_id: string;
  item_count: number;
};

export type GenerationRun = {
  id: string;
  topic_id: string;
  title: string;
  phase: RunPhase;
  target_item_count: number;
  submitted_by_user_id: string;
  intake_version_id: string | null;
  failure_reason: string | null;
  outline: GenerationOutline | null;
  draft_lesson_markdown: string | null;
  published_lesson_version_id?: string | null;
  published_quiz_version_id?: string | null;
  qa_items?: GenerationQaItem[];
  item_validation?: ItemValidationStats | null;
  jobs: GenerationJobStatus[];
  created_at: string;
};

export type ItemStatFlag =
  | "too_easy"
  | "too_hard"
  | "low_discrimination"
  | "negative_discrimination"
  | "dead_distractor"
  | "distractor_beats_key_top_group";

export type ItemOptionStat = {
  label: string;
  text: string;
  is_key: boolean;
  pick_rate: number;
  top_group_pick_rate: number;
  bottom_group_pick_rate: number;
};

export type TopicItemStat = {
  question_version_id: string;
  sequence: number;
  prompt: string;
  correct_option_label: string | null;
  n: number;
  p_correct: number | null;
  discrimination: number | null;
  options: ItemOptionStat[];
  flags: ItemStatFlag[];
};

export type TopicItemStats = {
  topic_id: string;
  quiz_version_id: string | null;
  minimum_n: number;
  items: TopicItemStat[];
};

export type MaterialIngestAccepted = {
  source_material_id: string;
  version_id: string;
  version_number: number;
  title: string;
  lifecycle_status: IngestLifecycleStatus;
  ingest_job_id: string;
};

export type MaterialVersionStatus = {
  id: string;
  source_material_id: string;
  version_number: number;
  title: string;
  lifecycle_status: IngestLifecycleStatus;
  failure_reason: string | null;
  chunk_count: number;
  blob_content_type?: string | null;
};

export type KnowledgeDocType = "policy" | "handbook" | "other" | string;

export type KnowledgeDocumentAccepted = {
  document_id: string;
  version_id: string;
  version_number: number;
  title: string;
  doc_type: string;
  lifecycle_status: IngestLifecycleStatus;
  ingest_job_id: string;
};

export type KnowledgeVersionSummary = {
  id: string;
  version_number: number;
  lifecycle_status: IngestLifecycleStatus;
  failure_reason: string | null;
  chunk_count: number;
  created_at?: string | null;
};

export type KnowledgeDocumentListItem = {
  id: string;
  title: string;
  slug: string;
  doc_type: string;
  status: string;
  required_roles: string[];
  latest_version: KnowledgeVersionSummary | null;
};

export type KnowledgeDocumentDetail = {
  id: string;
  title: string;
  slug: string;
  doc_type: string;
  status: string;
  required_roles: string[];
  versions: KnowledgeVersionSummary[];
};

export type KnowledgeDocumentVersionStatus = {
  id: string;
  document_id: string;
  version_number: number;
  lifecycle_status: IngestLifecycleStatus;
  failure_reason: string | null;
  chunk_count: number;
  blob_content_type?: string | null;
};

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}
