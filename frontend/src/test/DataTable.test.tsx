import { fireEvent, render, screen } from "@testing-library/react";
import { DataTable } from "../design/DataTable";
import type { Column } from "../design/DataTable";

interface Row {
  sku: string;
  name: string;
  qty: number;
}

const rows: Row[] = [
  { sku: "B2", name: "Beta", qty: 5 },
  { sku: "A1", name: "Alpha", qty: 10 },
  { sku: "C3", name: "Gamma", qty: 1 },
];

const columns: Column<Row>[] = [
  { key: "sku", header: "SKU", sortable: true },
  { key: "name", header: "Name", sortable: true },
  { key: "qty", header: "Qty", sortable: true, align: "right" },
];

function bodyText() {
  return screen.getAllByRole("row").slice(1).map((r) => r.textContent);
}

test("renders rows and sorts on header click", () => {
  render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.sku} />);
  expect(screen.getByText("Beta")).toBeInTheDocument();

  fireEvent.click(screen.getByText("SKU"));
  expect(bodyText()[0]).toContain("A1");

  fireEvent.click(screen.getByText("SKU"));
  expect(bodyText()[0]).toContain("C3");

  // numeric sort, not lexicographic
  fireEvent.click(screen.getByText("Qty"));
  expect(bodyText().map((t) => t?.slice(0, 2))).toEqual(["C3", "B2", "A1"]);
});

test("filters rows by text across columns", () => {
  render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.sku} filterText="alp" />);
  expect(screen.getByText("Alpha")).toBeInTheDocument();
  expect(screen.queryByText("Beta")).not.toBeInTheDocument();
});

test("shows empty state when nothing matches", () => {
  render(
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={(r) => r.sku}
      filterText="zzz"
      empty={<div>Nothing found</div>}
    />,
  );
  expect(screen.getByText("Nothing found")).toBeInTheDocument();
});
