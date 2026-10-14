"use client";

import { Fragment, useRef, useState } from "react";
import { ask, sendFeedback, type Citation } from "@/lib/api";

const EXAMPLES = [
  "How much notice does my landlord need to raise the rent?",
  "Can my landlord enter my unit without telling me?",
  "My landlord gave me an N12. Am I owed compensation?",
  "Is a no pets clause in my lease enforceable?",
];

type Status = "idle" | "streaming" | "done" | "error";

// Turn "[2]" markers into superscript links to the matching citation below.
function renderAnswer(text: string) {
  const parts = text.split(/(\[\d{1,2}\])/g);
  return parts.map((part, i) => {
    const m = part.match(/^\[(\d{1,2})\]$/);
    if (!m) return <Fragment key={i}>{part}</Fragment>;
    return (
      <a key={i} href={`#source-${m[1]}`} className="cite-mark" aria-label={`Source ${m[1]}`}>
        {m[1]}
      </a>
    );
  });
}

export default function Home() {
  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState("");
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<Citation[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState("");
  const [logId, setLogId] = useState<number | null>(null);
  const [rated, setRated] = useState<1 | -1 | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  async function submit(q: string) {
    const trimmed = q.trim();
    if (!trimmed || status === "streaming") return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setAsked(trimmed);
    setAnswer("");
    setCitations([]);
    setError("");
    setLogId(null);
    setRated(null);
    setStatus("streaming");

    await ask(
      trimmed,
      {
        onMeta: () => undefined,
        onToken: (t) => setAnswer((prev) => prev + t),
        onDone: (p) => {
          setCitations(p.citations);
          setLogId(p.log_id);
          setStatus("done");
        },
        onError: (m) => {
          setError(m);
          setStatus("error");
        },
      },
      controller.signal,
    );
  }

  function rate(value: 1 | -1) {
    if (logId === null || rated !== null) return;
    setRated(value);
    void sendFeedback(logId, value);
  }

  return (
    <>
      <section className="intro">
        <h1>Renting in Ontario? Ask what the law says.</h1>
        <p className="lede">
          Answers come only from the Residential Tenancies Act and official Ontario guides, and every
          sentence links to the section it came from.
        </p>
      </section>

      <form
        className="ask"
        onSubmit={(e) => {
          e.preventDefault();
          void submit(question);
        }}
      >
        <label htmlFor="question" className="visually-hidden">Your question</label>
        <textarea
          id="question"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void submit(question);
            }
          }}
          placeholder="For example: my landlord wants to raise the rent in 30 days. Is that allowed?"
          rows={3}
          maxLength={1200}
        />
        <div className="ask-row">
          <span className="hint">Leave out names and addresses. They are removed automatically.</span>
          <button type="submit" disabled={status === "streaming" || !question.trim()}>
            {status === "streaming" ? "Answering" : "Ask"}
          </button>
        </div>
      </form>

      {status === "idle" && (
        <section className="examples" aria-label="Example questions">
          <p>Try one of these:</p>
          <ul>
            {EXAMPLES.map((ex) => (
              <li key={ex}>
                <button type="button" className="link-button" onClick={() => { setQuestion(ex); void submit(ex); }}>
                  {ex}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {asked && (
        <article className="result" aria-live="polite">
          <h2 className="asked">{asked}</h2>

          {error ? (
            <p className="error">{error}</p>
          ) : (
            <div className="answer">
              {answer ? renderAnswer(answer) : <span className="thinking">Reading the law</span>}
            </div>
          )}

          {citations.length > 0 && (
            <ol className="sources">
              {citations.map((c) => (
                <li key={c.n} id={`source-${c.n}`}>
                  <span className="source-n">{c.n}</span>
                  <div>
                    <a href={c.url} target="_blank" rel="noreferrer" className="source-ref">{c.section_ref}</a>
                    <span className="source-path">{c.heading_path}</span>
                  </div>
                </li>
              ))}
            </ol>
          )}

          {status === "done" && logId !== null && (
            <div className="feedback">
              {rated === null ? (
                <>
                  <span>Was this helpful?</span>
                  <button type="button" onClick={() => rate(1)}>Yes</button>
                  <button type="button" onClick={() => rate(-1)}>No</button>
                </>
              ) : (
                <span>Thanks. Your rating helps improve answers.</span>
              )}
            </div>
          )}
        </article>
      )}
    </>
  );
}
