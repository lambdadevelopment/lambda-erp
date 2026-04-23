import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useAuth } from "@/contexts/auth-context";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/select";

export default function SettingsPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const isAdmin = user?.role === "admin";

  const { data: settings, isLoading } = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.getSettings(),
  });

  const settingsMut = useMutation({
    mutationFn: (data: Record<string, string>) => api.updateSettings(data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings"] }),
  });

  const { data: pubStatus } = useQuery({
    queryKey: ["public-manager"],
    queryFn: () => api.getPublicManagerStatus(),
  });

  const [showConfirm, setShowConfirm] = useState(false);

  const createPubMut = useMutation({
    mutationFn: () => api.createPublicManager(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["public-manager"] });
      setShowConfirm(false);
    },
  });

  const removePubMut = useMutation({
    mutationFn: () => api.removePublicManager(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["public-manager"] }),
  });

  if (isLoading) return <p className="text-gray-500">Loading...</p>;

  return (
    <div className="space-y-6">
      <Card title="PDF & Print">
        <div className="flex flex-wrap items-end gap-4">
          {isAdmin ? (
            <Select
              label="Page Size"
              options={["A4", "letter"]}
              value={settings?.pdf_page_size || "A4"}
              onChange={(e) => settingsMut.mutate({ pdf_page_size: e.target.value })}
            />
          ) : (
            <div>
              <div className="text-xs font-medium text-gray-500 mb-1">Page Size</div>
              <div className="text-sm text-gray-900">{settings?.pdf_page_size || "A4"}</div>
            </div>
          )}
        </div>
        <p className="mt-2 text-xs text-gray-400">
          A4 is standard internationally (210 x 297 mm). Letter is standard in the US (8.5 x 11 in).
        </p>
      </Card>

      <Card title="Opening Balances">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-gray-700">
              The Opening Balances page allows importing account balances, stock, and outstanding invoices from a previous system.
            </p>
            <p className="mt-1 text-xs text-gray-400">
              {settings?.opening_balances_enabled === "1"
                ? "Currently enabled — accessible under Introduction > Opening Balances."
                : "Currently disabled — the page is hidden from the sidebar."}
            </p>
          </div>
          {isAdmin && (
            <button
              onClick={() => settingsMut.mutate({
                opening_balances_enabled: settings?.opening_balances_enabled === "1" ? "0" : "1",
              })}
              className={`ml-4 shrink-0 rounded-full px-4 py-1.5 text-sm font-medium ${
                settings?.opening_balances_enabled === "1"
                  ? "bg-red-50 text-red-700 hover:bg-red-100"
                  : "bg-green-50 text-green-700 hover:bg-green-100"
              }`}
            >
              {settings?.opening_balances_enabled === "1" ? "Disable" : "Enable"}
            </button>
          )}
        </div>
      </Card>
      {/* Public Manager / Demo Mode */}
      {isAdmin && (
        <Card title="Public Access (Demo Mode)">
          {pubStatus?.active ? (
            <div>
              <div className="flex items-center gap-2">
                <span className="inline-block h-2 w-2 rounded-full bg-green-500" />
                <span className="text-sm font-medium text-green-800">Active</span>
              </div>
              <p className="mt-2 text-sm text-gray-600">
                Public access is enabled. Anyone can use the application without logging in.
                All visitors get manager-level permissions.
              </p>
              <button
                onClick={() => removePubMut.mutate()}
                disabled={removePubMut.isPending}
                className="mt-3 rounded-lg bg-gray-100 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-200"
              >
                {removePubMut.isPending ? "Disabling..." : "Disable Public Access"}
              </button>
            </div>
          ) : (
            <div>
              <p className="text-sm text-gray-600">
                Enable public access to let anyone use the application without an account.
                Useful for demos and showcases. All visitors get manager-level permissions and share the same identity.
              </p>
              {!showConfirm ? (
                <button
                  onClick={() => setShowConfirm(true)}
                  className="mt-3 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
                >
                  Enable Public Access
                </button>
              ) : (
                <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-4">
                  <p className="text-sm font-medium text-red-800">Are you sure?</p>
                  <p className="mt-1 text-xs text-red-600">
                    This will allow anyone to access the application without logging in.
                    They will be able to create, edit, and submit documents. Your admin account
                    still requires login.
                  </p>
                  <div className="mt-3 flex gap-2">
                    <button
                      onClick={() => createPubMut.mutate()}
                      disabled={createPubMut.isPending}
                      className="rounded-lg bg-red-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-red-700"
                    >
                      {createPubMut.isPending ? "Enabling..." : "Yes, Enable"}
                    </button>
                    <button
                      onClick={() => setShowConfirm(false)}
                      className="rounded-lg bg-white px-4 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 border border-gray-300"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
