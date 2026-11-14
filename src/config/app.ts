import type { AnswerMode } from "@/types/chat";

export const APP_NAME = "RoboGuide AI";

/** UI labels per mode. "Simple" is the short sidebar label for Non-technical mode. */
export const MODE_LABELS: Record<AnswerMode, { full: string; short: string; description: string }> =
  {
    technical: {
      full: "Technical",
      short: "Technical",
      description: "Detailed and precise technical information.",
    },
    "non-technical": {
      full: "Non-technical",
      short: "Simple",
      description: "Easy-to-understand explanations.",
    },
  };

export interface RobotOption {
  id: string;
  name: string;
  type: string;
}

/**
 * SAMPLE placeholder machines. They do NOT represent the documentation actually available.
 * Replace with data from the backend (e.g. GET /robots) when it exists.
 */
export const SAMPLE_ROBOTS: RobotOption[] = [
  { id: "sample-armpro-x6", name: "ArmPro X6", type: "6-axis articulated" },
  { id: "sample-cobot-c10", name: "CoBot C10", type: "Collaborative" },
  { id: "sample-scara-s4", name: "SCARA S4", type: "SCARA" },
  { id: "sample-delta-d3", name: "Delta D3", type: "Delta / parallel" },
  { id: "sample-palletizer-p120", name: "Palletizer P120", type: "4-axis palletizing" },
];

export const findRobot = (id: string | null | undefined) =>
  SAMPLE_ROBOTS.find((r) => r.id === id) ?? null;

/** Response languages. `value` is what is sent to the backend as `response_language`. */
export const RESPONSE_LANGUAGES = [
  { value: "English", label: "English", code: "EN" },
  { value: "German", label: "Deutsch", code: "DE" },
  { value: "Italian", label: "Italiano", code: "IT" },
  { value: "French", label: "Français", code: "FR" },
  { value: "Spanish", label: "Español", code: "ES" },
  { value: "Portuguese", label: "Português", code: "PT" },
  { value: "Chinese", label: "中文", code: "ZH" },
  { value: "Japanese", label: "日本語", code: "JA" },
] as const;

export const DEFAULT_LANGUAGE = "English";

export const FAQS: string[] = [
  "What is the maximum payload of this robot?",
  "How do I perform an emergency stop?",
  "What does payload mean?",
  "How often should the robot be serviced?",
  "How do I restart the robot after a fault?",
];

export interface QuickCategory {
  id: "safety" | "maintenance" | "troubleshooting";
  label: string;
  questions: string[];
}

export const QUICK_CATEGORIES: QuickCategory[] = [
  {
    id: "safety",
    label: "Safety",
    questions: [
      "What safety precautions apply before operating the robot?",
      "Where are the emergency stop buttons located?",
      "What personal protective equipment is required?",
    ],
  },
  {
    id: "maintenance",
    label: "Maintenance",
    questions: [
      "What is the recommended maintenance schedule?",
      "How do I check and replace the lubricant?",
      "How do I replace the backup batteries?",
    ],
  },
  {
    id: "troubleshooting",
    label: "Troubleshooting",
    questions: [
      "What does this error code mean?",
      "The robot will not power on — what should I check?",
      "Why is the robot moving slower than expected?",
    ],
  },
];

export const NOT_FOUND_MESSAGE =
  "I couldn't find enough information about this in the selected robot's manual.";
