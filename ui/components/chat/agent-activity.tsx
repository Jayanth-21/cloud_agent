"use client";

import { Check, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { AgentActivityPayload } from "@/lib/types";

export function AgentActivityPanel({ data }: { data: AgentActivityPayload }) {
  if (data.hidden) {
    return null;
  }
  const { steps, skills, fullToolsFallback } = data;
  const hasSteps = steps.length > 0;
  const hasSkills = Boolean(skills?.length);

  if (!hasSteps && !hasSkills) {
    return null;
  }

  return (
    <div
      className="mb-3 w-full min-w-0 max-w-full rounded-xl border border-border/40 bg-muted/30 px-3 py-2.5 text-[13px] shadow-[var(--shadow-card)]"
      data-testid="agent-activity"
    >
      {hasSkills && (
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <span className="text-muted-foreground">Skills</span>
          {skills?.map((id) => (
            <span
              className="rounded-md bg-background/80 px-2 py-0.5 font-medium text-foreground text-xs ring-1 ring-border/50"
              key={id}
            >
              {id}
            </span>
          ))}
          {fullToolsFallback && (
            <span className="rounded-md bg-amber-500/15 px-2 py-0.5 text-amber-800 text-xs dark:text-amber-200">
              Full toolset
            </span>
          )}
        </div>
      )}
      {hasSteps && (
        <ul className="space-y-2">
          {steps.map((s) => (
            <li className="flex gap-2.5" key={s.id}>
              <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center">
                {s.status === "done" && (
                  <Check className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                )}
                {s.status === "active" && (
                  <Loader2 className="h-4 w-4 animate-spin text-primary" />
                )}
                {s.status === "pending" && (
                  <span className="block h-2 w-2 rounded-full bg-muted-foreground/35" />
                )}
              </span>
              <span
                className={cn(
                  "leading-snug",
                  s.status === "active" && "font-medium text-foreground",
                  s.status === "done" && "text-muted-foreground",
                  s.status === "pending" && "text-muted-foreground/80"
                )}
              >
                {s.label}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
