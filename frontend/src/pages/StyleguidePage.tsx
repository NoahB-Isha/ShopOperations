import { useState } from "react";
import {
  Badge,
  Button,
  Card,
  DataTable,
  Dialog,
  Drawer,
  EmptyState,
  Field,
  Input,
  PageHeader,
  Select,
  Spinner,
  Stat,
  StatusDot,
  Toggle,
  useToast,
} from "../design";
import type { Column } from "../design";

const swatches = [
  ["canvas", "bg-canvas border border-line"],
  ["surface", "bg-surface border border-line"],
  ["raised", "bg-raised border border-line"],
  ["ink", "bg-ink"],
  ["ink-soft", "bg-ink-soft"],
  ["ink-faint", "bg-ink-faint"],
  ["line", "bg-line"],
  ["copper", "bg-copper"],
  ["copper-deep", "bg-copper-deep"],
  ["copper-tint", "bg-copper-tint border border-line"],
  ["forest", "bg-forest"],
  ["forest-tint", "bg-forest-tint border border-line"],
  ["gold", "bg-gold"],
  ["gold-tint", "bg-gold-tint border border-line"],
  ["danger", "bg-danger"],
  ["danger-tint", "bg-danger-tint border border-line"],
] as const;

interface DemoRow {
  sku: string;
  name: string;
  qty: number;
  price: number;
}

const demoRows: DemoRow[] = [
  { sku: "CA0023000009", name: "Copper Water Bottle — 950ml", qty: 120, price: 34 },
  { sku: "RU0000000005", name: "Rudraksha Mala — 5mm", qty: 2, price: 24 },
  { sku: "IN0000000777", name: "Sandalwood Incense", qty: 0, price: 9 },
  { sku: "BL0000000021", name: "Bloom Ghee", qty: 46, price: 18 },
];

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-10">
      <h2 className="display mb-4 border-b border-line pb-2 text-xl">{title}</h2>
      {children}
    </section>
  );
}

export function StyleguidePage() {
  const toast = useToast();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [toggled, setToggled] = useState(true);

  const columns: Column<DemoRow>[] = [
    { key: "sku", header: "SKU", sortable: true,
      render: (r) => <span className="font-mono text-[12.5px]">{r.sku}</span> },
    { key: "name", header: "Product", sortable: true },
    { key: "qty", header: "On hand", align: "right", sortable: true },
    { key: "price", header: "Price", align: "right", sortable: true,
      render: (r) => `$${r.price.toFixed(2)}` },
  ];

  return (
    <>
      <PageHeader
        title="Styleguide"
        subtitle="The design system, rendered live — tokens, type, and every core component."
      />

      <Section title="Palette">
        <div className="grid grid-cols-4 gap-3 sm:grid-cols-8">
          {swatches.map(([name, cls]) => (
            <div key={name}>
              <div className={`h-14 rounded-(--radius-sm) shadow-soft ${cls}`} />
              <div className="mt-1.5 font-mono text-[11px] text-ink-faint">{name}</div>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Type">
        <div className="flex flex-col gap-3">
          <div className="display text-3xl">Fraunces carries the voice — warm, settled, sure.</div>
          <div className="display text-xl text-ink-soft">Section headings sit a size down.</div>
          <p className="max-w-xl text-sm text-ink">
            Inter handles everything operational: tables, forms, and long labels. Body text stays
            at 15px with relaxed leading so dense pages read calmly.
          </p>
          <p className="text-[13px] text-ink-faint">
            Faint 13px text carries hints, timestamps, and secondary detail.
          </p>
          <span className="label-caps">Small-caps micro labels structure tables & fields</span>
          <span className="font-mono text-[13px]">CA0023000009 · monospace for SKUs and references</span>
        </div>
      </Section>

      <Section title="Buttons">
        <div className="flex flex-wrap items-center gap-2.5">
          <Button>Primary</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="danger">Danger</Button>
          <Button loading>Saving…</Button>
          <Button disabled>Disabled</Button>
          <Button size="sm">Small</Button>
          <Button variant="secondary" size="sm" icon={<span>+</span>}>With icon</Button>
        </div>
      </Section>

      <Section title="Badges & status">
        <div className="flex flex-wrap items-center gap-2.5">
          <Badge>neutral</Badge>
          <Badge tone="copper">Air only</Badge>
          <Badge tone="forest">fresh</Badge>
          <Badge tone="gold">stale</Badge>
          <Badge tone="danger">Expires 2026-09-01</Badge>
          <Badge tone="outline">untracked</Badge>
          <StatusDot ok label="Synced · live Odoo" />
          <StatusDot ok={false} warn label="Sync stale" />
          <StatusDot ok={false} label="Odoo auth failing!" />
          <Spinner />
        </div>
      </Section>

      <Section title="Forms">
        <Card className="max-w-md">
          <div className="flex flex-col gap-4">
            <Field label="Email or phone" help="We'll send a one-time code.">
              <Input placeholder="you@example.org" />
            </Field>
            <Field label="Zone" error="Pick a zone for this coordinator.">
              <Select defaultValue="">
                <option value="" disabled>Choose…</option>
                <option>Zone 1 (Lili)</option>
                <option>Canada</option>
              </Select>
            </Field>
            <Toggle checked={toggled} onChange={setToggled} label="Dept-orderable" />
          </div>
        </Card>
      </Section>

      <Section title="Stats">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="Odoo connection" value="Fixture" hint="no credentials set" />
          <Stat label="Writes" value="Kill switch OFF" hint="all writes dry-run" tone="good" />
          <Stat label="Sync age" value="12m" tone="warn" />
          <Stat label="Auth failures" value="1" tone="bad" />
        </div>
      </Section>

      <Section title="Data table">
        <DataTable
          columns={columns}
          rows={demoRows}
          rowKey={(r) => r.sku}
          onRowClick={() => toast.info("Row clicked.")}
          footer={<span>{demoRows.length} rows · click headers to sort</span>}
        />
      </Section>

      <Section title="Feedback">
        <div className="flex flex-wrap gap-2.5">
          <Button variant="secondary" onClick={() => toast.success("Draft transfer created — review it in Odoo.")}>
            Success toast
          </Button>
          <Button variant="secondary" onClick={() => toast.error("Odoo rejected the transfer: missing picking type.")}>
            Error toast
          </Button>
          <Button variant="secondary" onClick={() => toast.info("Sync started.")}>
            Info toast
          </Button>
          <Button variant="secondary" onClick={() => setDialogOpen(true)}>Dialog</Button>
          <Button variant="secondary" onClick={() => setDrawerOpen(true)}>Drawer</Button>
        </div>
      </Section>

      <Section title="Empty state">
        <EmptyState
          title="No orders yet"
          hint="When city centers start ordering in Phase 3, their requests land here for approval."
          action={<Button variant="secondary" size="sm">Learn what's coming</Button>}
        />
      </Section>

      <Dialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        title="Run the live canary?"
        footer={
          <>
            <Button variant="ghost" onClick={() => setDialogOpen(false)}>Cancel</Button>
            <Button onClick={() => setDialogOpen(false)}>Confirm</Button>
          </>
        }
      >
        <p className="text-sm leading-6 text-ink-soft">
          Dialogs confirm consequential actions. They close on Escape or backdrop click, and the
          primary action sits bottom-right.
        </p>
      </Dialog>

      <Drawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title="Copper Water Bottle — 950ml"
        footer={<Button onClick={() => setDrawerOpen(false)}>Close</Button>}
      >
        <p className="text-sm leading-6 text-ink-soft">
          Drawers hold detail views — product records, order lines — sliding in from the right
          without losing the table behind.
        </p>
      </Drawer>
    </>
  );
}
