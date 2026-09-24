/**
 * Handing the browser a file built in the page: what a chart is holding, as
 * CSV or JSON, without asking the server for it again.
 *
 * The shape follows the runner's export routes so a file saved from a chart
 * and one downloaded from the store line up in the same spreadsheet: two
 * leading time columns, `time_s` (seconds from the first point) and `time`
 * (ISO 8601 UTC, milliseconds), then a column per trace named
 * `label (unit)`.
 */

/** Named columns and the rows under them: what `toCsv`/`toJson` write. */
export interface Table {
  columns: string[];
  rows: Array<Array<string | number | null>>;
}

/** One trace as the tables want it: a label, a unit, and points in seconds since the epoch. */
export interface TraceLike {
  label: string;
  unit?: string;
  t: number[];
  v: Array<number | null | undefined>;
}

const cell = (value: string | number | null): string => {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
};

export function toCsv({ columns, rows }: Table): string {
  return [columns, ...rows].map((row) => row.map(cell).join(",")).join("\n") + "\n";
}

/** A list of objects keyed by column, as the runner's `format=json` sends. */
export function toJson({ columns, rows }: Table): string {
  return JSON.stringify(rows.map((row) => Object.fromEntries(columns.map((c, i) => [c, row[i] ?? null]))));
}

/** ISO 8601 UTC to the millisecond, as the runner writes it. */
export const isoTime = (seconds: number): string => new Date(seconds * 1000).toISOString().replace(/\.(\d{3})\d*Z$/, ".$1Z");

/**
 * Traces onto one table: a row per instant any trace has a point at, each
 * column holding that trace's last value (the runner's `wide` layout). All
 * the points given are written, whatever the chart draws.
 */
export function seriesTable(series: TraceLike[]): Table {
  const columns = ["time_s", "time", ...series.map((s) => (s.unit ? `${s.label} (${s.unit})` : s.label))];
  const instants = [...new Set(series.flatMap((s) => s.t))].sort((a, b) => a - b);
  if (instants.length === 0) return { columns, rows: [] };
  const first = instants[0]!;
  const next = series.map(() => 0);
  const held: Array<number | null> = series.map(() => null);
  const rows = instants.map((at) => {
    series.forEach((s, i) => {
      while (next[i]! < s.t.length && s.t[next[i]!]! <= at) {
        const value = s.v[next[i]!];
        // A reading with no value (null, or `NaN` from the store) empties the cell; `undefined` is no reading.
        if (value !== undefined) held[i] = value === null || Number.isNaN(value) ? null : value;
        next[i]!++;
      }
    });
    return [Math.round((at - first) * 1e6) / 1e6, isoTime(at), ...held];
  });
  return { columns, rows };
}

/** A name a filesystem takes. */
export const fileName = (stem: string, extension: string): string =>
  `${stem.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "chart"}.${extension}`;

/**
 * Save `text` as a file. A blob URL and a click on an anchor that is never in
 * the document: the only way a page can hand the browser bytes it made
 * itself. The URL is revoked once the click has been taken.
 */
export function download(name: string, text: string, type: string): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.rel = "noopener";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** A table as a file, in either format the runner speaks. */
export function saveTable(stem: string, table: Table, format: "csv" | "json"): void {
  if (format === "json") download(fileName(stem, "json"), toJson(table), "application/json");
  else download(fileName(stem, "csv"), toCsv(table), "text/csv;charset=utf-8");
}
