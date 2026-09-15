import { ConsumptionScope } from "./types";

export function ConsumptionScopeLabel({ data, scope }: { data: ConsumptionScope; scope: string }) {
  if (!data.included_wings) return null;
  return <div data-testid={`${scope}-consumption-scope`} className="text-xs text-gray-400 break-words">
    Consumption scope: {data.included_wings.length ? `Wings ${data.included_wings.join(", ")}` : "No enabled wings"}
    {data.excluded_wings?.length ? ` · Disabled / excluded: ${data.excluded_wings.join(", ")}` : ""} · Current logical configuration
  </div>;
}