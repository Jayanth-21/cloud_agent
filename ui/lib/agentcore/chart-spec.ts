export type LinePoint = { x: string; y: number };

export type LineSeries = {
  name: string;
  points: LinePoint[];
};

export type BarCategory = {
  label: string;
  value: number;
};

export type CostLineChartSpec = {
  kind: "line";
  title: string;
  xLabel: string;
  yLabel: string;
  xTickMode: "all" | "sparse";
  maxXTicks: number;
  series: LineSeries[];
};

export type CostBarChartSpec = {
  kind: "bar";
  title: string;
  xLabel: string;
  yLabel: string;
  categories: BarCategory[];
  subtitle?: string | null;
};

export type CostChartSpec = CostLineChartSpec | CostBarChartSpec;

export function isCostChartSpec(x: unknown): x is CostChartSpec {
  if (!x || typeof x !== "object") return false;
  const k = (x as { kind?: string }).kind;
  return k === "line" || k === "bar";
}
