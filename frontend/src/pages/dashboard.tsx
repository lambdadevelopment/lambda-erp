import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import { Card } from "@/components/ui/card";
import { StatusBadge } from "@/components/document/status-badge";
import { formatCurrency, formatDate } from "@/lib/utils";

interface MetricCardProps {
  title: string;
  value: number;
}

function MetricCard({ title, value }: MetricCardProps) {
  return (
    <Card>
      <p className="text-sm font-medium text-gray-500">{title}</p>
      <p className="mt-2 text-2xl font-semibold text-gray-900">
        {formatCurrency(value)}
      </p>
    </Card>
  );
}

export default function DashboardPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["dashboard-summary"],
    queryFn: () => api.dashboardSummary(),
  });

  if (isLoading) {
    return <p className="text-gray-500">Loading...</p>;
  }

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard title="Total Revenue" value={data?.total_revenue} />
        <MetricCard
          title="Outstanding Receivable"
          value={data?.outstanding_receivable}
        />
        <MetricCard
          title="Outstanding Payable"
          value={data?.outstanding_payable}
        />
        <MetricCard
          title="Total Stock Value"
          value={data?.total_stock_value}
        />
      </div>

      <Card title="Recent Documents">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead>
              <tr>
                <th className="px-4 py-2 text-left font-medium text-gray-500">
                  Type
                </th>
                <th className="px-4 py-2 text-left font-medium text-gray-500">
                  Name
                </th>
                <th className="px-4 py-2 text-left font-medium text-gray-500">
                  Status
                </th>
                <th className="px-4 py-2 text-left font-medium text-gray-500">
                  Date
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {data?.recent_documents?.map(
                (doc: any, idx: number) => (
                  <tr key={idx} className="hover:bg-gray-50">
                    <td className="px-4 py-2 text-gray-700">{doc.type}</td>
                    <td className="px-4 py-2 text-gray-900 font-medium">
                      {doc.name}
                    </td>
                    <td className="px-4 py-2">
                      <StatusBadge status={doc.status} />
                    </td>
                    <td className="px-4 py-2 text-gray-500">
                      {formatDate(doc.date)}
                    </td>
                  </tr>
                ),
              )}
              {(!data?.recent_documents ||
                data.recent_documents.length === 0) && (
                <tr>
                  <td
                    colSpan={4}
                    className="px-4 py-6 text-center text-gray-400"
                  >
                    No recent documents
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
