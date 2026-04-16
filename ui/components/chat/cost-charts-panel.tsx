"use client";

import dynamic from "next/dynamic";
import type { ComponentType, CSSProperties } from "react";
import { useMemo } from "react";
import type {
  CostBarChartSpec,
  CostChartSpec,
  CostLineChartSpec,
} from "@/lib/agentcore/chart-spec";
import type { Data, Layout } from "plotly.js";

const Plot = dynamic(() => import("react-plotly.js"), {
  ssr: false,
  loading: () => (
    <div className="flex h-[360px] items-center justify-center text-muted-foreground text-sm">
      Loading chart…
    </div>
  ),
}) as ComponentType<{
  data: Data[];
  layout: Partial<Layout>;
  config?: Record<string, unknown>;
  style?: CSSProperties;
  useResizeHandler?: boolean;
}>;

function sparseTickVals(dates: string[], maxTicks: number): string[] {
  if (dates.length <= maxTicks) return dates;
  const n = Math.min(maxTicks, 10);
  const step = Math.max(1, Math.floor((dates.length - 1) / (n - 1)));
  const out: string[] = [];
  for (let i = 0; i < dates.length; i += step) out.push(dates[i]);
  const last = dates[dates.length - 1];
  if (out[out.length - 1] !== last) out.push(last);
  return out.slice(0, n + 1);
}

function LineChart({ spec }: { spec: CostLineChartSpec }) {
  const traces: Data[] = useMemo(
    () =>
      spec.series.map((s) => ({
        type: "scatter",
        mode: "lines",
        name: s.name,
        x: s.points.map((p) => p.x),
        y: s.points.map((p) => p.y),
        hovertemplate:
          "<b>%{fullData.name}</b><br>%{x}<br>$%{y:,.2f} USD<extra></extra>",
      })),
    [spec.series]
  );

  const allDates = useMemo(() => {
    const set = new Set<string>();
    spec.series.forEach((s) => s.points.forEach((p) => set.add(p.x)));
    return Array.from(set).sort();
  }, [spec.series]);

  const layout: Partial<Layout> = useMemo(() => {
    const ticks =
      spec.xTickMode === "sparse"
        ? sparseTickVals(allDates, spec.maxXTicks)
        : undefined;
    return {
      title: { text: spec.title, font: { size: 14 } },
      margin: { t: 44, r: 20, b: 64, l: 56 },
      paper_bgcolor: "transparent",
      plot_bgcolor: "transparent",
      font: { size: 11 },
      xaxis: {
        title: { text: spec.xLabel },
        type: "category",
        tickangle: allDates.length > 14 ? -35 : 0,
        ...(ticks ? { tickmode: "array" as const, tickvals: ticks } : {}),
      },
      yaxis: {
        title: { text: spec.yLabel },
        tickformat: ",.2f",
      },
      showlegend: spec.series.length > 1,
      legend: {
        orientation: "h",
        yanchor: "top",
        y: -0.2,
        x: 0,
      },
      autosize: true,
      height: 380,
    };
  }, [spec, allDates]);

  return (
    <div className="w-full rounded-xl border border-border/60 bg-muted/20 p-2">
      <Plot
        config={{
          responsive: true,
          displayModeBar: true,
          displaylogo: false,
          modeBarButtonsToRemove: ["lasso2d", "select2d"],
        }}
        data={traces}
        layout={layout}
        style={{ width: "100%", minHeight: 380 }}
        useResizeHandler
      />
    </div>
  );
}

function BarChart({ spec }: { spec: CostBarChartSpec }) {
  const traces: Data[] = useMemo(
    () => [
      {
        type: "bar",
        x: spec.categories.map((c) => c.label),
        y: spec.categories.map((c) => c.value),
        marker: { color: "#38bdf8" },
        hovertemplate: "%{x}<br>$%{y:,.2f} USD<extra></extra>",
      },
    ],
    [spec.categories]
  );

  const layout: Partial<Layout> = useMemo(
    () => ({
      title: { text: spec.title, font: { size: 14 } },
      margin: { t: 44, r: 20, b: 112, l: 56 },
      paper_bgcolor: "transparent",
      plot_bgcolor: "transparent",
      font: { size: 11 },
      xaxis: {
        title: { text: spec.xLabel },
        type: "category",
        tickangle: -40,
      },
      yaxis: {
        title: { text: spec.yLabel },
        tickformat: ",.2f",
      },
      height: 400,
    }),
    [spec]
  );

  return (
    <div className="w-full rounded-xl border border-border/60 bg-muted/20 p-2">
      {spec.subtitle ? (
        <p className="text-muted-foreground text-xs px-1 pb-1">{spec.subtitle}</p>
      ) : null}
      <Plot
        config={{
          responsive: true,
          displayModeBar: true,
          displaylogo: false,
          modeBarButtonsToRemove: ["lasso2d", "select2d"],
        }}
        data={traces}
        layout={layout}
        style={{ width: "100%", minHeight: 400 }}
        useResizeHandler
      />
    </div>
  );
}

export function CostChartsPanel({ charts }: { charts: CostChartSpec[] }) {
  if (!charts.length) return null;
  return (
    <div className="mt-3 flex flex-col gap-5">
      {charts.map((c, i) =>
        c.kind === "line" ? (
          <LineChart key={`line-${i}`} spec={c} />
        ) : (
          <BarChart key={`bar-${i}`} spec={c} />
        )
      )}
    </div>
  );
}
