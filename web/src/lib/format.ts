export function money(value: number | string | null | undefined, digits = 2) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: digits, maximumFractionDigits: digits }).format(number);
}
export function number(value: number | string | null | undefined, digits = 3) {
  if (value === null || value === undefined || value === "") return "—";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString("en-US", { maximumFractionDigits: digits }) : "—";
}
export function percent(value: number | string | null | undefined, digits = 2) {
  if (value === null || value === undefined || value === "") return "—";
  return `${(Number(value) * 100).toFixed(digits)}%`;
}
export function label(value: string | null | undefined) { return value ? value.replaceAll("_", " ") : "MISSING"; }
