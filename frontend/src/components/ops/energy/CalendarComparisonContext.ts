import { createContext } from "react";
import { CalendarComparison } from "./types";

export const CalendarComparisonContext = createContext<{ month: string; data: CalendarComparison | null; loading: boolean; error: string; operatingDate?: string } | null>(null);