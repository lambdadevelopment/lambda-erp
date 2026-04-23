import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { LinkField } from "@/components/document/link-field";

interface AccountRow { account: string; debit: string; credit: string; party_type: string; party: string }
interface StockRow { item_code: string; qty: string; rate: string }
interface InvoiceRow { type: string; party: string; amount: string; due_date: string; remarks: string }

function emptyAccountRow(): AccountRow { return { account: "", debit: "", credit: "", party_type: "", party: "" }; }
function emptyStockRow(): StockRow { return { item_code: "", qty: "", rate: "" }; }
function emptyInvoiceRow(): InvoiceRow { return { type: "sales", party: "", amount: "", due_date: "", remarks: "" }; }

export default function OpeningBalancesPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [company, setCompany] = useState("");
  const [postingDate, setPostingDate] = useState(new Date().toISOString().split("T")[0]);
  const [warehouse, setWarehouse] = useState("");

  const [accountRows, setAccountRows] = useState<AccountRow[]>([emptyAccountRow()]);
  const [stockRows, setStockRows] = useState<StockRow[]>([emptyStockRow()]);
  const [invoiceRows, setInvoiceRows] = useState<InvoiceRow[]>([emptyInvoiceRow()]);

  const [results, setResults] = useState<Record<string, any>>({});

  const accountMut = useMutation({
    mutationFn: () => api.importAccountBalances({
      company,
      posting_date: postingDate,
      entries: accountRows.filter(r => r.account && (r.debit || r.credit)).map(r => ({
        account: r.account, debit: parseFloat(r.debit) || 0, credit: parseFloat(r.credit) || 0,
        party_type: r.party_type || undefined, party: r.party || undefined,
      })),
    }),
    onSuccess: (d) => setResults(prev => ({ ...prev, accounts: d })),
  });

  const stockMut = useMutation({
    mutationFn: () => api.importStockBalances({
      company,
      posting_date: postingDate,
      warehouse,
      items: stockRows.filter(r => r.item_code && r.qty).map(r => ({
        item_code: r.item_code, qty: parseFloat(r.qty) || 0, rate: parseFloat(r.rate) || 0,
      })),
    }),
    onSuccess: (d) => setResults(prev => ({ ...prev, stock: d })),
  });

  const invoiceMut = useMutation({
    mutationFn: () => api.importOutstandingInvoices({
      company,
      invoices: invoiceRows.filter(r => r.party && r.amount).map(r => ({
        type: r.type, party: r.party, amount: parseFloat(r.amount) || 0,
        due_date: r.due_date || undefined, remarks: r.remarks || undefined, posting_date: postingDate,
      })),
    }),
    onSuccess: (d) => setResults(prev => ({ ...prev, invoices: d })),
  });

  function updateRow<T>(rows: T[], setRows: (r: T[]) => void, idx: number, field: string, value: string) {
    const updated = [...rows];
    (updated[idx] as any)[field] = value;
    setRows(updated);
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-gray-500">
        Import balances from your previous system. Each section creates the appropriate documents
        (Journal Entry, Stock Entry, or Invoices) and submits them automatically.
      </p>

      <Card title="Common Settings">
        <div className="flex flex-wrap items-end gap-4">
          <LinkField label="Company" value={company} onChange={setCompany} linkDoctype="company" readOnly={false} />
          <Input label="As-of Date" type="date" value={postingDate} onChange={e => setPostingDate(e.target.value)} />
        </div>
      </Card>

      {/* Account Balances */}
      <Card title="1. Account Balances">
        <p className="mb-3 text-xs text-gray-500">
          Enter your trial balance from the old system. The difference will be automatically balanced against Opening Balance Equity.
          For receivable/payable accounts, also set the party type and party.
        </p>
        <div>
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-2 py-2 text-left font-medium text-gray-500">Account</th>
                <th className="px-2 py-2 text-left font-medium text-gray-500">Party Type</th>
                <th className="px-2 py-2 text-left font-medium text-gray-500">Party</th>
                <th className="px-2 py-2 text-right font-medium text-gray-500">Debit</th>
                <th className="px-2 py-2 text-right font-medium text-gray-500">Credit</th>
              </tr>
            </thead>
            <tbody>
              {accountRows.map((row, i) => (
                <tr key={i}>
                  <td className="px-2 py-1">
                    <LinkField value={row.account} onChange={v => updateRow(accountRows, setAccountRows, i, "account", v)} linkDoctype="account" readOnly={false} label="" />
                  </td>
                  <td className="px-2 py-1">
                    <select value={row.party_type} onChange={e => updateRow(accountRows, setAccountRows, i, "party_type", e.target.value)} className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm">
                      <option value="">—</option>
                      <option value="Customer">Customer</option>
                      <option value="Supplier">Supplier</option>
                    </select>
                  </td>
                  <td className="px-2 py-1">
                    {row.party_type ? (
                      <LinkField label="" value={row.party} onChange={v => updateRow(accountRows, setAccountRows, i, "party", v)} linkDoctype={row.party_type.toLowerCase()} readOnly={false} />
                    ) : <span className="text-gray-300 text-xs">set party type first</span>}
                  </td>
                  <td className="px-2 py-1"><input type="number" value={row.debit} onChange={e => updateRow(accountRows, setAccountRows, i, "debit", e.target.value)} className="w-24 rounded border border-gray-300 px-2 py-1.5 text-right text-sm" placeholder="0.00" /></td>
                  <td className="px-2 py-1"><input type="number" value={row.credit} onChange={e => updateRow(accountRows, setAccountRows, i, "credit", e.target.value)} className="w-24 rounded border border-gray-300 px-2 py-1.5 text-right text-sm" placeholder="0.00" /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-2 flex gap-2">
          <button onClick={() => setAccountRows([...accountRows, emptyAccountRow()])} className="text-xs text-blue-600 hover:text-blue-800">+ Add Row</button>
        </div>
        <div className="mt-3 flex items-center gap-3">
          <Button onClick={() => accountMut.mutate()} disabled={!company || accountMut.isPending}>
            {accountMut.isPending ? "Importing..." : "Import Account Balances"}
          </Button>
          {accountMut.error && <span className="text-xs text-red-600">{(accountMut.error as Error).message}</span>}
          {results.accounts && <span className="text-xs text-green-600">Created {results.accounts.journal_entry}</span>}
        </div>
      </Card>

      {/* Stock Balances */}
      <Card title="2. Stock Balances">
        <p className="mb-3 text-xs text-gray-500">
          Enter your current inventory. Creates a Stock Entry (Opening Stock) — posts Dr Stock In Hand / Cr Opening Balance Equity so the P&L isn't distorted by day-one inventory.
        </p>
        <div className="mb-3">
          <LinkField label="Warehouse" value={warehouse} onChange={setWarehouse} linkDoctype="warehouse" readOnly={false} />
        </div>
        <div>
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-2 py-2 text-left font-medium text-gray-500">Item</th>
                <th className="px-2 py-2 text-right font-medium text-gray-500">Qty</th>
                <th className="px-2 py-2 text-right font-medium text-gray-500">Rate</th>
              </tr>
            </thead>
            <tbody>
              {stockRows.map((row, i) => (
                <tr key={i}>
                  <td className="px-2 py-1"><LinkField label="" value={row.item_code} onChange={v => updateRow(stockRows, setStockRows, i, "item_code", v)} linkDoctype="item" readOnly={false} /></td>
                  <td className="px-2 py-1"><input type="number" value={row.qty} onChange={e => updateRow(stockRows, setStockRows, i, "qty", e.target.value)} className="w-24 rounded border border-gray-300 px-2 py-1.5 text-right text-sm" placeholder="0" /></td>
                  <td className="px-2 py-1"><input type="number" value={row.rate} onChange={e => updateRow(stockRows, setStockRows, i, "rate", e.target.value)} className="w-24 rounded border border-gray-300 px-2 py-1.5 text-right text-sm" placeholder="0.00" /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-2">
          <button onClick={() => setStockRows([...stockRows, emptyStockRow()])} className="text-xs text-blue-600 hover:text-blue-800">+ Add Row</button>
        </div>
        <div className="mt-3 flex items-center gap-3">
          <Button onClick={() => stockMut.mutate()} disabled={!company || !warehouse || stockMut.isPending}>
            {stockMut.isPending ? "Importing..." : "Import Stock Balances"}
          </Button>
          {stockMut.error && <span className="text-xs text-red-600">{(stockMut.error as Error).message}</span>}
          {results.stock && <span className="text-xs text-green-600">Created {results.stock.stock_entry} ({results.stock.items_count} items)</span>}
        </div>
      </Card>

      {/* Outstanding Invoices */}
      <Card title="3. Outstanding Invoices">
        <p className="mb-3 text-xs text-gray-500">
          Enter unpaid invoices from your old system. Creates submitted Sales or Purchase Invoices with the correct outstanding amounts for AR/AP aging.
        </p>
        <div>
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-2 py-2 text-left font-medium text-gray-500">Type</th>
                <th className="px-2 py-2 text-left font-medium text-gray-500">Customer / Supplier</th>
                <th className="px-2 py-2 text-right font-medium text-gray-500">Amount</th>
                <th className="px-2 py-2 text-left font-medium text-gray-500">Due Date</th>
                <th className="px-2 py-2 text-left font-medium text-gray-500">Ref / Remarks</th>
              </tr>
            </thead>
            <tbody>
              {invoiceRows.map((row, i) => (
                <tr key={i}>
                  <td className="px-2 py-1">
                    <select value={row.type} onChange={e => updateRow(invoiceRows, setInvoiceRows, i, "type", e.target.value)} className="rounded border border-gray-300 px-2 py-1.5 text-sm">
                      <option value="sales">Sales (AR)</option>
                      <option value="purchase">Purchase (AP)</option>
                    </select>
                  </td>
                  <td className="px-2 py-1">
                    <LinkField
                      label=""
                      value={row.party}
                      onChange={v => updateRow(invoiceRows, setInvoiceRows, i, "party", v)}
                      linkDoctype={row.type === "sales" ? "customer" : "supplier"}
                      readOnly={false}
                    />
                  </td>
                  <td className="px-2 py-1"><input type="number" value={row.amount} onChange={e => updateRow(invoiceRows, setInvoiceRows, i, "amount", e.target.value)} className="w-28 rounded border border-gray-300 px-2 py-1.5 text-right text-sm" placeholder="0.00" /></td>
                  <td className="px-2 py-1"><input type="date" value={row.due_date} onChange={e => updateRow(invoiceRows, setInvoiceRows, i, "due_date", e.target.value)} className="rounded border border-gray-300 px-2 py-1.5 text-sm" /></td>
                  <td className="px-2 py-1"><input type="text" value={row.remarks} onChange={e => updateRow(invoiceRows, setInvoiceRows, i, "remarks", e.target.value)} className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm" placeholder="Old invoice ref" /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-2">
          <button onClick={() => setInvoiceRows([...invoiceRows, emptyInvoiceRow()])} className="text-xs text-blue-600 hover:text-blue-800">+ Add Row</button>
        </div>
        <div className="mt-3 flex items-center gap-3">
          <Button onClick={() => invoiceMut.mutate()} disabled={!company || invoiceMut.isPending}>
            {invoiceMut.isPending ? "Importing..." : "Import Outstanding Invoices"}
          </Button>
          {invoiceMut.error && <span className="text-xs text-red-600">{(invoiceMut.error as Error).message}</span>}
          {results.invoices && <span className="text-xs text-green-600">Created {results.invoices.invoices_created} invoices</span>}
        </div>
      </Card>

      {/* Finish */}
      <div className="rounded-lg border border-gray-200 bg-gray-50 p-6 text-center">
        <p className="text-sm text-gray-600">
          Done importing? You can disable this page and re-enable it later under Settings &gt; General.
        </p>
        <button
          onClick={async () => {
            await api.updateSettings({ opening_balances_enabled: "0" });
            queryClient.invalidateQueries({ queryKey: ["settings"] });
            navigate("/");
          }}
          className="mt-3 rounded-lg bg-gray-900 px-5 py-2 text-sm font-medium text-white hover:bg-gray-800"
        >
          Finish and Disable Opening Balances
        </button>
      </div>
    </div>
  );
}
