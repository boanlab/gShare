import { useEffect, useMemo, useRef } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';

export interface Series {
  labels: Record<string, string | undefined>;
  uuid?: string | null;
  points: [number, number | null][];   // [unix seconds, value]
}

/** Distinct hues that stay legible on both themes. Colour is assigned by a STABLE key (a card's
 *  UUID, a node's name) rather than by array position, so one card keeps one colour across every
 *  panel — that is what lets the page carry a single shared legend instead of six. */
export const CHART_COLORS = [
  '#38bdf8', '#f59e0b', '#34d399', '#f472b6', '#a78bfa', '#fb7185', '#22d3ee', '#facc15',
];

export function colorForIndex(i: number): string {
  return CHART_COLORS[i % CHART_COLORS.length];
}

export function formatValue(v: number | null | undefined, unit: string): string {
  if (v == null || Number.isNaN(v)) return '-';
  switch (unit) {
    case 'percent': return `${v.toFixed(v < 10 ? 1 : 0)}%`;
    case 'cores': return `${v.toFixed(v < 10 ? 2 : 1)}`;
    case 'mib': return v >= 1024 ? `${(v / 1024).toFixed(1)} GiB` : `${v.toFixed(0)} MiB`;
    case 'celsius': return `${v.toFixed(0)}°C`;
    case 'watt': return `${v.toFixed(0)} W`;
    case 'mhz': return `${v.toFixed(0)} MHz`;
    case 'bytes_per_sec': {
      const u = ['B/s', 'KiB/s', 'MiB/s', 'GiB/s'];
      let n = v, i = 0;
      while (n >= 1024 && i < u.length - 1) { n /= 1024; i += 1; }
      return `${n.toFixed(n < 10 ? 1 : 0)} ${u[i]}`;
    }
    default: return v.toFixed(0);
  }
}

const timeLabel = (ts: number) =>
  new Date(ts * 1000).toLocaleString(undefined, {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false,   // 00:00-23:59 everywhere; the AM/PM prefix read as noise on charts
  });

/** Hover readout: the exact values at the cursor's timestamp, every series at once, so a spike can
 *  be read without a legend under each panel. Built with DOM nodes (never innerHTML) because the
 *  series labels come from metric labels. */
function tooltipPlugin(
  unit: string,
  labels: string[],
  colors: string[],
): uPlot.Plugin {
  let tip: HTMLDivElement | null = null;
  return {
    hooks: {
      init: (u: uPlot) => {
        tip = document.createElement('div');
        tip.className = 'gs-chart-tip';
        tip.style.display = 'none';
        u.over.appendChild(tip);
      },
      setCursor: (u: uPlot) => {
        if (!tip) return;
        const { idx, left, top } = u.cursor;
        if (idx == null || left == null || left < 0) { tip.style.display = 'none'; return; }
        const ts = u.data[0][idx] as number;
        tip.replaceChildren();

        const head = document.createElement('div');
        head.className = 'gs-chart-tip-head';
        head.textContent = timeLabel(ts);
        tip.appendChild(head);

        let any = false;
        for (let i = 1; i < u.data.length; i += 1) {
          const v = u.data[i][idx] as number | null | undefined;
          if (v == null) continue;
          any = true;
          const row = document.createElement('div');
          row.className = 'gs-chart-tip-row';
          const sw = document.createElement('span');
          sw.className = 'gs-chart-tip-swatch';
          sw.style.background = colors[i - 1] ?? '#888';
          const name = document.createElement('span');
          name.className = 'gs-chart-tip-name';
          name.textContent = labels[i - 1] ?? '';
          const val = document.createElement('b');
          val.className = 'gs-num';
          val.textContent = formatValue(v, unit);
          row.append(sw, name, val);
          tip.appendChild(row);
        }
        if (!any) { tip.style.display = 'none'; return; }

        tip.style.display = 'block';
        // Flip to the cursor's left near the right edge so the readout never leaves the panel.
        const w = tip.offsetWidth;
        const flip = left + w + 16 > u.over.clientWidth;
        tip.style.left = `${Math.max(0, flip ? left - w - 12 : left + 12)}px`;
        tip.style.top = `${Math.max(0, Math.min((top ?? 0) - 8, u.over.clientHeight - tip.offsetHeight - 4))}px`;
      },
      destroy: () => { tip?.remove(); tip = null; },
    },
  };
}

/** A time-series panel. uPlot draws on canvas, so a 7-day range stays smooth; this component owns
 *  sizing, theming and the hover readout. The legend lives on the page, once, not per panel. */
export function TimeSeriesChart({ series, unit, height = 180, seriesLabel, colorOf, timeOnly = false, hideXAxis = false }: {
  series: Series[];
  unit: string;
  height?: number;
  seriesLabel: (s: Series, i: number) => string;
  /** Stable colour per series; falls back to position when not supplied. */
  colorOf?: (s: Series, i: number) => string;
  /** Short ranges (a few hours at most): x-axis ticks as HH:MM only, no date line. */
  timeOnly?: boolean;
  /** Sparkline mode: no x-axis at all — the hover readout still carries the exact time. */
  hideXAxis?: boolean;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);

  // uPlot wants column-major data: [timestamps, ...one array per series] on a shared x axis.
  const data = useMemo(() => {
    const xs = Array.from(new Set(series.flatMap((s) => s.points.map((p) => p[0])))).sort((a, b) => a - b);
    const index = new Map(xs.map((t, i) => [t, i]));
    const cols = series.map((s) => {
      const col = new Array<number | null>(xs.length).fill(null);
      for (const [t, v] of s.points) {
        const i = index.get(t);
        if (i !== undefined) col[i] = v;
      }
      return col;
    });
    return [xs, ...cols] as uPlot.AlignedData;
  }, [series]);

  const labels = useMemo(() => series.map((s, i) => seriesLabel(s, i)), [series, seriesLabel]);
  const colors = useMemo(
    () => series.map((s, i) => (colorOf ? colorOf(s, i) : colorForIndex(i))),
    [series, colorOf],
  );

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const css = getComputedStyle(document.documentElement);
    const axisColor = css.getPropertyValue('--muted').trim() || '#94a3b8';
    const gridColor = css.getPropertyValue('--border').trim() || '#334155';

    const opts: uPlot.Options = {
      width: host.clientWidth || 600,
      height,
      padding: [8, 8, 6, 0],   // bottom>0: the lowest gridline/label was shaved off
      legend: { show: false },
      cursor: { y: false, points: { size: 5 } },
      scales: { x: { time: true } },
      plugins: [tooltipPlugin(unit, labels, colors)],
      axes: [
        {
          stroke: axisColor, grid: { stroke: gridColor, width: 1 }, ticks: { stroke: gridColor },
          ...(hideXAxis ? { show: false } : {}),
          ...(timeOnly ? {
            values: (_u: uPlot, splits: number[]) => splits.map((ts) =>
              new Date(ts * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false })),
          } : {}),
        },
        {
          stroke: axisColor,
          grid: { stroke: gridColor, width: 1 },
          ticks: { stroke: gridColor },
          // Wide enough for the longest label a unit produces ("589 KiB/s", "11.7 GiB").
          size: unit === 'bytes_per_sec' || unit === 'mib' ? 78 : unit === 'mhz' ? 74 : 62,
          values: (_u, vals) => vals.map((v) => formatValue(v, unit)),
        },
      ],
      series: [
        {},
        ...series.map((_s, i) => ({
          label: labels[i],
          stroke: colors[i],
          width: 1.6,
          points: { show: false },
        })),
      ],
    };
    const plot = new uPlot(opts, data, host);
    plotRef.current = plot;
    const ro = new ResizeObserver(() => plot.setSize({ width: host.clientWidth, height }));
    ro.observe(host);
    return () => { ro.disconnect(); plot.destroy(); plotRef.current = null; };
    // Rebuild when the series set changes (labels/colours are baked into the plugin); plain data
    // updates go through setData below so the cursor is not dropped.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [labels.join('|'), colors.join('|'), unit, height, timeOnly, hideXAxis]);

  useEffect(() => {
    plotRef.current?.setData(data);
  }, [data]);

  return <div ref={hostRef} className="w-full" />;
}

/** The page-level legend: one row for the whole tab, since every panel uses the same series and
 *  the same colour per series. */
export function ChartLegend({ items }: { items: { key: string; label: string; color: string }[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1.5">
      {items.map((it) => (
        <span key={it.key} className="inline-flex items-center gap-1.5 text-xs text-muted">
          <span className="w-3 h-[3px] rounded-tag" style={{ background: it.color }} aria-hidden="true" />
          {it.label}
        </span>
      ))}
    </div>
  );
}
