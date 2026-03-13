"use client";

/**
 * Renders structured JSON from the agent (e.g. tool results, final answer payload).
 * Optional: when content includes a visualization schema, render a simple table or chart.
 */

import { useMemo } from "react";

export type AgentJsonResultProps = {
  /** Raw message content (string or parsed object). */
  content: string | Record<string, unknown>;
  /** Optional visualization schema: "table" | "chart" | custom. */
  visualizationSchema?: string;
  className?: string;
};

export function AgentJsonResult({
  content,
  visualizationSchema,
  className = "",
}: AgentJsonResultProps) {
  const parsed = useMemo(() => {
    if (typeof content === "object") return content;
    try {
      return JSON.parse(content as string) as Record<string, unknown>;
    } catch {
      return null;
    }
  }, [content]);

  const isJson = parsed !== null && typeof parsed === "object";

  if (!isJson) {
    return (
      <pre className={`whitespace-pre-wrap break-words text-sm ${className}`}>
        {typeof content === "string" ? content : JSON.stringify(content)}
      </pre>
    );
  }

  if (visualizationSchema === "table" && Array.isArray(parsed)) {
    return (
      <div className={`overflow-x-auto ${className}`}>
        <table className="min-w-full border border-gray-200 text-sm">
          <thead>
            <tr>
              {Object.keys(parsed[0] as object).map((key) => (
                <th key={key} className="border-b px-3 py-2 text-left font-medium">
                  {key}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(parsed as object[]).map((row, i) => (
              <tr key={i}>
                {Object.values(row).map((val, j) => (
                  <td key={j} className="border-b px-3 py-2">
                    {typeof val === "object" ? JSON.stringify(val) : String(val)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (visualizationSchema === "chart" && typeof parsed === "object" && parsed !== null) {
    return (
      <div className={className}>
        <p className="text-xs text-gray-500 mb-1">Chart data (use your chart library with this payload):</p>
        <pre className="bg-gray-50 p-3 rounded text-xs overflow-auto max-h-48">
          {JSON.stringify(parsed, null, 2)}
        </pre>
      </div>
    );
  }

  return (
    <pre
      className={`bg-gray-50 p-3 rounded text-xs overflow-auto max-h-96 ${className}`}
      data-testid="agent-json-result"
    >
      {JSON.stringify(parsed, null, 2)}
    </pre>
  );
}
