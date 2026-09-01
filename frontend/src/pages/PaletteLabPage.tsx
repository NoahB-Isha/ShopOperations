/* Palette lab — the shipped themes side by side. The same example page is
   rendered once per palette, each inside a theme scope that overrides the
   color custom properties (see styles/palette-lab.css — kept in lockstep
   with tokens.css). Primary #f36f21 is LOCKED and identical in all. */
import "../styles/palette-lab.css";

import { useState } from "react";
import {
  Badge,
  Button,
  Card,
  DataTable,
  Fab,
  Field,
  Input,
  PageHeader,
  Stat,
  StatusDot,
  Toggle,
  toneForLabel,
} from "../design";
import type { Column } from "../design";
import { useToast } from "../design/Toast";
import { ILMark } from "../shell/AppShell";
import { setPalette } from "../theme";

interface DemoRow {
  sku: string;
  name: string;
  category: string;
  qty: number;
  price: number;
}

const ROWS: DemoRow[] = [
  { sku: "CA0023000009", name: "Copper Water Bottle — 950ml", category: "Copper", qty: 150, price: 34 },
  { sku: "RU0000000005", name: "Rudraksha Mala — 5mm", category: "Rudraksha", qty: 2, price: 24 },
  { sku: "IN0000000777", name: "Sandalwood Incense", category: "Incense & Dhoop", qty: 0, price: 9 },
  { sku: "BL0000000021", name: "Bloom Ghee", category: "Bloom", qty: 46, price: 18 },
];

const COLUMNS: Column<DemoRow>[] = [
  { key: "sku", header: "SKU", width: "150px",
    render: (r) => <span className="font-mono text-[12.5px] text-on-surface-variant">{r.sku}</span> },
  { key: "name", header: "Product", render: (r) => <span className="font-medium">{r.name}</span> },
  { key: "category", header: "Category",
    render: (r) => <Badge tone={toneForLabel(r.category)}>{r.category}</Badge> },
  { key: "qty", header: "On hand", align: "right",
    render: (r) => (
      <span className={`tabular-nums ${r.qty === 0 ? "text-outline" : r.qty <= 3 ? "text-warn" : ""}`}>
        {r.qty}{r.qty > 0 && r.qty <= 3 ? " ⚠" : ""}
      </span>
    ) },
  { key: "price", header: "Price", align: "right",
    render: (r) => <span className="tabular-nums">${r.price.toFixed(2)}</span> },
];

interface Variant {
  id: string;
  className: string;
  name: string;
  story: string;
  tradeoff: string;
  swatches: { hex: string; label: string }[];
}

const VARIANTS: Variant[] = [
  {
    id: "pop",
    className: "pl-pop",
    name: "A — Charcoal Pop (default)",
    story:
      "Maximum energy: hot magenta secondary, electric cyan tertiary, crisp lilac-white paper. Triadic neon against near-black ink — loud, confident, unmistakable.",
    tradeoff: "The loudest; an all-day ops tool this saturated can fatigue, and it drops the warm-cream Isha feel.",
    swatches: [
      { hex: "#f36f21", label: "primary (locked)" },
      { hex: "#b90d6e", label: "secondary" },
      { hex: "#b5eaff", label: "tertiary container" },
      { hex: "#fbfafd", label: "surface" },
    ],
  },
  {
    id: "neem",
    className: "pl-neem",
    name: "B — Neem Tree",
    story:
      "Botanical warmth: olive-bark secondary, neem-leaf tertiary, parchment surfaces deepening toward desert sand. The earthiest of the set — orange reads as sunlight on bark.",
    tradeoff: "The gentlest contrast between accents; green + olive sit close together in dim rooms.",
    swatches: [
      { hex: "#f36f21", label: "primary (locked)" },
      { hex: "#5c4f26", label: "secondary" },
      { hex: "#c3ecb6", label: "tertiary container" },
      { hex: "#faf5ee", label: "surface" },
    ],
  },
  {
    id: "turmeric",
    className: "pl-turmeric",
    name: "C — Turmeric Root",
    story:
      "Fresh-cut gold on cool lavender-slate: sunflower secondary wearing dark text, slate-violet tertiary, carbon ink. The cool ground makes both the orange and the gold glow.",
    tradeoff: "Gold chips sit near the amber 'stale' warnings — the warn container stays paler on purpose.",
    swatches: [
      { hex: "#f36f21", label: "primary (locked)" },
      { hex: "#f5bd45", label: "secondary" },
      { hex: "#dfe1ff", label: "tertiary container" },
      { hex: "#f9f9fe", label: "surface" },
    ],
  },
  {
    id: "devi",
    className: "pl-devi",
    name: "D — Devi",
    story:
      "Full immersion, after the Linga Bhairavi imagery — and the ONE palette allowed to override the brand orange: kumkum-crimson primary (every button goes red), temple-gold secondary wearing dark text, the brand flame demoted to tertiary with one flourish — Devi alone paints the restock 'bring out' numbers in it. Lamp-lit ivory ground, snackbars on the sanctum's dark maroon.",
    tradeoff:
      "Crimson primary sits near the error red — destructive actions lean on wording and the error container, not hue alone. Gold chips flirt with the amber 'stale' warnings, like Turmeric.",
    swatches: [
      { hex: "#a91226", label: "primary (Devi's exception)" },
      { hex: "#d29a26", label: "secondary" },
      { hex: "#ffdcc2", label: "tertiary container" },
      { hex: "#fdf6f1", label: "surface" },
    ],
  },
];

/** The same miniature "Status meets Catalog" page, re-rendered per theme. */
function ExamplePage() {
  const [toggled, setToggled] = useState(true);
  return (
    <div className="rounded-(--radius-lg) bg-surface p-6 md:p-8">
      {/* faux nav strip so the secondary-container active pill is visible */}
      <div className="mb-6 flex flex-wrap items-center gap-2">
        <ILMark size={30} />
        <span className="state-layer flex h-9 items-center gap-2 rounded-full bg-secondary-container px-4 text-sm font-semibold text-on-secondary-container">
          Status
        </span>
        <span className="state-layer flex h-9 items-center rounded-full px-4 text-sm font-medium text-on-surface-variant">
          Catalog
        </span>
        <span className="state-layer flex h-9 items-center rounded-full px-4 text-sm font-medium text-on-surface-variant">
          Centers
        </span>
        <span className="ml-auto inline-flex items-center rounded-(--radius-sm) border border-outline-variant bg-surface-container-low px-2.5 py-1">
          <StatusDot ok label="Synced · live Odoo" />
        </span>
      </div>

      <h3 className="display-l text-on-surface">Status</h3>
      <p className="mt-1.5 mb-6 text-[15px] text-on-surface-variant">
        Sync freshness, write safety, and the paper trail — the honest view.
      </p>

      {/* two cards ride the VARIANT roles (secondary/tertiary) so the stat row
          actually changes between palettes; two keep semantic tones for context */}
      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Odoo connection" value="Live" hint="fndn-us.sadhguru.org" />
        <Card tone="secondary" className="min-w-40">
          <div className="text-xs font-bold tracking-wide uppercase opacity-75">Pending approvals</div>
          <div className="display mt-1.5 text-[clamp(1.9rem,3vw,2.5rem)] leading-none">12</div>
          <div className="mt-1.5 text-[13px] opacity-75">Zone 3 (Ravi)</div>
        </Card>
        <Card tone="tertiary" className="min-w-40">
          <div className="text-xs font-bold tracking-wide uppercase opacity-75">Dept orders</div>
          <div className="display mt-1.5 text-[clamp(1.9rem,3vw,2.5rem)] leading-none">5</div>
          <div className="mt-1.5 text-[13px] opacity-75">III Departments</div>
        </Card>
        <Stat label="Stock" value="42s ago" hint="2,016 rows" tone="good" />
      </div>

      <div className="mb-6 flex flex-wrap items-center gap-2.5">
        <Button>Run live canary</Button>
        <Button variant="secondary">Dry run</Button>
        <Button variant="outlined">Contract check</Button>
        <Button variant="ghost">View audit</Button>
        <Fab label="New item" />
      </div>

      <div className="mb-6 flex flex-wrap items-center gap-2">
        <Badge tone="copper">Air only</Badge>
        <Badge tone="secondary">Zone 3 (Ravi)</Badge>
        <Badge tone="tertiary">dept-orderable</Badge>
        <Badge tone="forest">fresh</Badge>
        <Badge tone="gold">stale</Badge>
        <Badge tone="danger">Expires 2026-09-01</Badge>
        <Badge tone="outline">untracked</Badge>
      </div>

      <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-[1fr_240px]">
        <div className="flex flex-col gap-3">
          <Input
            aria-label="Example search"
            placeholder="Search products…"
            className="max-w-xs [--control-radius:9999px]"
          />
          <DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.sku} />
        </div>
        <div className="flex flex-col gap-4">
          <Field label="Order Reviewer" help="Floating label, filled field.">
            <Input defaultValue="Lili" />
          </Field>
          <Toggle checked={toggled} onChange={setToggled} label="Dept-orderable" />
        </div>
      </div>
    </div>
  );
}

function Swatch({ hex, label }: { hex: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-surface-container-low py-1 pr-3 pl-1.5 text-[12px] text-on-surface-variant">
      <span
        className="h-5 w-5 rounded-full shadow-[inset_0_0_0_1px_rgb(0_0_0/0.15)]"
        style={{ backgroundColor: hex }}
        aria-hidden
      />
      <span className="font-mono">{hex}</span> {label}
    </span>
  );
}

export function PaletteLabPage() {
  const toast = useToast();
  const apply = (id: string, name: string) => {
    setPalette(id);
    toast.success(`${name} is now your theme.`);
  };
  return (
    <>
      <PageHeader
        title="Themes"
        subtitle={
          <>
            Three palettes around the locked primary <span className="font-mono font-semibold text-primary">#f36f21</span> —
            pick one here or on the Settings page. Dark mode is one global
            scheme and follows your system.
          </>
        }
      />
      <div className="flex flex-col gap-12">
        {VARIANTS.map((v) => (
          <section key={v.id} id={v.id} aria-label={v.name}>
            <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2">
              <h2 className="headline">{v.name}</h2>
              <div className="flex flex-wrap gap-1.5">
                {v.swatches.map((s) => (
                  <Swatch key={s.label} {...s} />
                ))}
              </div>
              <Button
                variant="secondary"
                size="sm"
                className="ml-auto"
                onClick={() => apply(v.id, v.name)}
              >
                Use this theme
              </Button>
            </div>
            <p className="mb-1 max-w-3xl text-sm text-on-surface">{v.story}</p>
            <p className="mb-4 max-w-3xl text-[13px] text-on-surface-variant">
              Trade-off: {v.tradeoff}
            </p>
            <div className={`${v.className} overflow-hidden rounded-(--radius-xl) shadow-(--shadow-e2)`}>
              <ExamplePage />
            </div>
          </section>
        ))}
      </div>
    </>
  );
}
