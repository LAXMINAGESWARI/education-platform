import { useEffect, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";

import { fetchLearningDirectory, getTopicMaterial } from "../api/materials";
import type { MaterialProgress, SubjectNode, TopicNode } from "../api/types";
import { ApiError } from "../api/types";
import { AttemptHistoryTrigger } from "../components/AttemptHistory";
import { Crumbs } from "../components/Crumbs";
import { TopicEntries } from "../components/TopicEntries";
import { findSubjectTopic, topicHistoryPath } from "../lib/subjectMaterial";

/** Topic URLs offer the lesson and quiz, or the subtopic list when that topic has no lesson. */
export function TopicPageRedirect() {
  const { subjectId = "", topicId = "", subtopicId } = useParams();
  const [subject, setSubject] = useState<SubjectNode | null>(null);
  const [topic, setTopic] = useState<TopicNode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [lessonProgress, setLessonProgress] = useState<MaterialProgress | null>(null);

  useEffect(() => {
    if (subtopicId) return;
    let cancelled = false;
    void (async () => {
      try {
        const directory = await fetchLearningDirectory();
        if (cancelled) return;
        const found = findSubjectTopic(directory, subjectId, topicId);
        setSubject(found?.subject ?? null);
        setTopic(found?.topic ?? null);
        setError(found ? null : "This topic is not in your subjects.");
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load this topic.");
        }
      } finally {
        if (!cancelled) setReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [subjectId, subtopicId, topicId]);

  useEffect(() => {
    if (!topic?.has_topic_lesson) return;
    let cancelled = false;
    void getTopicMaterial(topic.id)
      .then((material) => {
        if (!cancelled) setLessonProgress(material.progress);
      })
      .catch(() => {
        if (!cancelled) setLessonProgress(null);
      });
    return () => {
      cancelled = true;
    };
  }, [topic?.has_topic_lesson, topic?.id]);

  if (subtopicId) {
    return <Navigate to={`/subjects/${subjectId}/subtopics/${subtopicId}/lesson`} replace />;
  }

  if (!ready) {
    return (
      <div className="center-state" role="status">
        Loading topic…
      </div>
    );
  }

  if (error || !topic || !subject) {
    return (
      <div className="center-state">
        <p className="form__error" role="alert">
          {error ?? "This topic is not in your subjects."}
        </p>
        <Link to={`/subjects/${subjectId}`} className="btn btn--soft btn--sm">
          Back to subject
        </Link>
      </div>
    );
  }

  const historyLink = topic.overall_quiz ? (
    <AttemptHistoryTrigger
      title="Show full history"
      attempts={topic.overall_quiz.recent_attempts}
      to={topicHistoryPath(subjectId, topic.id)}
      actionLabel="Open"
    />
  ) : null;

  return (
    <>
      <Crumbs
        parts={[
          { label: "Subjects", to: "/" },
          { label: subject.name, to: `/subjects/${subjectId}` },
          { label: topic.title },
        ]}
      />
      {topic.has_topic_lesson ? (
        <>
          <header className="page-head">
            <h1>{topic.title}</h1>
            <p>Open the lesson, or take the quiz once that lesson is complete.</p>
          </header>
          <TopicEntries subjectId={subjectId} topic={topic} progress={lessonProgress} />
          {historyLink}
        </>
      ) : (
        <>
          <header className="page-head">
            <h1>{topic.title}</h1>
            <p>This topic is organized as subtopics. Open one to read its lesson.</p>
          </header>
          {topic.subtopics.length === 0 ? (
            <p className="muted">No subtopics published for this topic yet.</p>
          ) : (
            <section aria-labelledby="subtopics-heading">
              <h2 id="subtopics-heading">Subtopics</h2>
              <ul className="list">
                {topic.subtopics.map((subtopic) => (
                  <li key={subtopic.id} className="subtopic-card">
                    <Link
                      to={`/subjects/${subjectId}/subtopics/${subtopic.id}/lesson`}
                      className="subtopic-card__head"
                    >
                      <p className="list-item__title">{subtopic.title}</p>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {historyLink}
        </>
      )}
    </>
  );
}
