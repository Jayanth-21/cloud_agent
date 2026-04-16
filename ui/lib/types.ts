import type { InferUITool, UIMessage } from "ai";
import { z } from "zod";
import type { CostChartSpec } from "@/lib/agentcore/chart-spec";
import type { ArtifactKind } from "@/components/chat/artifact";
import type { createDocument } from "./ai/tools/create-document";
import type { getWeather } from "./ai/tools/get-weather";
import type { requestSuggestions } from "./ai/tools/request-suggestions";
import type { updateDocument } from "./ai/tools/update-document";
import type { Suggestion } from "./db/schema";

export const messageMetadataSchema = z.object({
  createdAt: z.string(),
});

export type MessageMetadata = z.infer<typeof messageMetadataSchema>;

type weatherTool = InferUITool<typeof getWeather>;
type createDocumentTool = InferUITool<ReturnType<typeof createDocument>>;
type updateDocumentTool = InferUITool<ReturnType<typeof updateDocument>>;
type requestSuggestionsTool = InferUITool<
  ReturnType<typeof requestSuggestions>
>;

export type ChatTools = {
  getWeather: weatherTool;
  createDocument: createDocumentTool;
  updateDocument: updateDocumentTool;
  requestSuggestions: requestSuggestionsTool;
};

/** Live Cloud Intelligence progress (transient stream parts; not persisted). */
export type AgentActivityStep = {
  id: string;
  label: string;
  status: "pending" | "active" | "done";
};

export type AgentActivityPayload = {
  steps: AgentActivityStep[];
  skills?: string[];
  fullToolsFallback?: boolean;
  hidden?: boolean;
};

export type CustomUIDataTypes = {
  textDelta: string;
  imageDelta: string;
  sheetDelta: string;
  codeDelta: string;
  suggestion: Suggestion;
  appendMessage: string;
  id: string;
  title: string;
  kind: ArtifactKind;
  clear: null;
  finish: null;
  "chat-title": string;
  /** Cost / forecast charts from AgentCore (`cost_chart_spec` JSON). */
  "cost-charts": CostChartSpec[];
  /** Agent routing + LangGraph progress (streaming only). */
  "agent-activity": AgentActivityPayload;
};

export type ChatMessage = UIMessage<
  MessageMetadata,
  CustomUIDataTypes,
  ChatTools
>;

export type Attachment = {
  name: string;
  url: string;
  contentType: string;
};
