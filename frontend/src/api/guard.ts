import { apiRequest } from "./client";

export type GuardDecision = "block" | "flag" | "abuse";

export type GuardVerdict = {
  id: string;
  user_id: string;
  role: string;
  surface: string;
  message_sha256: string;
  message_preview: string;
  injection_probability: number | null;
  exfiltration_probability: number | null;
  topic: string | null;
  decision: GuardDecision;
  source: string;
  latency_ms: number;
  created_at: string;
};

export function fetchGuardVerdicts(): Promise<GuardVerdict[]> {
  return apiRequest<GuardVerdict[]>("/admin/guard-verdicts", { auth: true });
}
