/** Answer style. Same documentation & retrieval — only the explanation style changes. */
export type AnswerMode = "technical" | "non-technical";

export interface SourceReference {
  /** Manual / document name, exactly as returned by the backend. */
  manual: string;
  /** Page number — null when the backend did not provide one. Never invented. */
  page: number | null;
  /** Relevant excerpt in the manual's original language, when available. */
  excerpt: string | null;
  /** Link to the document, when available. */
  url: string | null;
  /** Original language of the manual, when provided by the backend. */
  language: string | null;
}

/** "answered" = backend returned an answer; "not_found" = not enough supporting info in the manual. */
export type AnswerOutcome = "answered" | "not_found";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  /** Mode in effect when this assistant message was produced. */
  mode?: AnswerMode;
  robotName?: string | null;
  language?: string;
  sources?: SourceReference[];
  outcome?: AnswerOutcome;
  /** True when produced by the local mock service — never verified manual information. */
  isDemo?: boolean;
}

export interface ChatThread {
  id: string;
  title: string;
  mode: AnswerMode;
  robotId: string | null;
  language: string;
  createdAt: number;
  updatedAt: number;
  messages: ChatMessage[];
}
