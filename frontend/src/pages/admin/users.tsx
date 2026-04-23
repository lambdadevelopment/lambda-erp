import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useAuth } from "@/contexts/auth-context";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";

export default function UsersPage() {
  const { user: currentUser } = useAuth();
  const queryClient = useQueryClient();

  const { data: users, isLoading } = useQuery({
    queryKey: ["auth-users"],
    queryFn: () => api.authListUsers(),
  });

  const { data: invites } = useQuery({
    queryKey: ["auth-invites"],
    queryFn: () => api.authListInvites(),
  });

  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("viewer");
  const [inviteResult, setInviteResult] = useState<{ link: string } | null>(null);

  const inviteMut = useMutation({
    mutationFn: () => api.authInvite(inviteEmail, inviteRole),
    onSuccess: (result) => {
      setInviteResult(result);
      setInviteEmail("");
      queryClient.invalidateQueries({ queryKey: ["auth-invites"] });
    },
  });

  const changeRoleMut = useMutation({
    mutationFn: ({ name, role }: { name: string; role: string }) => api.authChangeRole(name, role),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["auth-users"] }),
  });

  const disableMut = useMutation({
    mutationFn: (name: string) => api.authDisableUser(name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["auth-users"] }),
  });

  if (currentUser?.role !== "admin") {
    return <p className="py-8 text-center text-gray-400">Admin access required</p>;
  }

  return (
    <div className="space-y-6">
      {/* Role explanation */}
      <Card title="Roles">
        <div className="grid gap-3 text-sm sm:grid-cols-3">
          <div className="rounded-lg border border-blue-200 bg-blue-50 p-3">
            <div className="font-semibold text-blue-900">Admin</div>
            <p className="mt-1 text-blue-700">
              Full access. Can create and manage documents, masters, company setup, and users. Can invite team members and change roles.
            </p>
          </div>
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
            <div className="font-semibold text-amber-900">Manager</div>
            <p className="mt-1 text-amber-700">
              Can create, edit, submit, and cancel documents. Can create and edit master data. Can run reports and use the AI chat. Cannot manage users or company setup.
            </p>
          </div>
          <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
            <div className="font-semibold text-gray-900">Viewer</div>
            <p className="mt-1 text-gray-600">
              Read-only access to all documents, masters, and reports. Can use the AI chat. Cannot create or modify any data.
            </p>
          </div>
        </div>
      </Card>

      {/* Invite form */}
      <Card title="Invite User">
        <div className="flex flex-wrap items-end gap-4">
          <Input
            label="Email"
            type="email"
            value={inviteEmail}
            onChange={(e) => setInviteEmail(e.target.value)}
            placeholder="colleague@company.com"
          />
          <Select
            label="Role"
            options={["viewer", "manager", "admin"]}
            value={inviteRole}
            onChange={(e) => setInviteRole(e.target.value)}
          />
          <Button
            onClick={() => inviteMut.mutate()}
            disabled={!inviteEmail.trim() || inviteMut.isPending}
          >
            {inviteMut.isPending ? "Sending..." : "Create Invite"}
          </Button>
        </div>
        {inviteMut.error && (
          <p className="mt-2 text-sm text-red-600">{(inviteMut.error as Error).message}</p>
        )}
        {inviteResult && (
          <div className="mt-3 rounded-md bg-green-50 px-4 py-3 text-sm">
            <p className="font-medium text-green-800">Invite created!</p>
            <p className="mt-1 text-green-700">
              Share this link:{" "}
              <code className="rounded bg-green-100 px-2 py-0.5 text-xs font-mono">
                {window.location.origin}{inviteResult.link}
              </code>
            </p>
          </div>
        )}
      </Card>

      {/* Pending invites */}
      {invites && invites.filter((i: any) => !i.used).length > 0 && (
        <Card title="Pending Invites">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-2 text-left font-medium text-gray-500">Email</th>
                <th className="px-4 py-2 text-left font-medium text-gray-500">Role</th>
                <th className="px-4 py-2 text-left font-medium text-gray-500">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {invites.filter((i: any) => !i.used).map((invite: any) => (
                <tr key={invite.token}>
                  <td className="px-4 py-2">{invite.email}</td>
                  <td className="px-4 py-2">
                    <span className="rounded bg-gray-100 px-2 py-0.5 text-xs font-medium">{invite.role}</span>
                  </td>
                  <td className="px-4 py-2 text-gray-500">{invite.creation?.split("T")[0]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {/* User list */}
      <Card title="Team Members">
        {isLoading ? (
          <p className="text-gray-500">Loading...</p>
        ) : (
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-2 text-left font-medium text-gray-500">Name</th>
                <th className="px-4 py-2 text-left font-medium text-gray-500">Email</th>
                <th className="px-4 py-2 text-left font-medium text-gray-500">Role</th>
                <th className="px-4 py-2 text-left font-medium text-gray-500">Status</th>
                <th className="px-4 py-2 text-right font-medium text-gray-500">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(users || []).map((u: any) => (
                <tr key={u.name} className="hover:bg-gray-50">
                  <td className="px-4 py-2 font-medium text-gray-900">{u.full_name}</td>
                  <td className="px-4 py-2 text-gray-600">{u.email}</td>
                  <td className="px-4 py-2">
                    {u.role === "public_manager" ? (
                      <span className="rounded bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                        public_manager (system)
                      </span>
                    ) : (
                      <select
                        value={u.role}
                        onChange={(e) => changeRoleMut.mutate({ name: u.name, role: e.target.value })}
                        disabled={u.name === currentUser?.name}
                        className={`rounded border px-2 py-1 text-xs ${u.name === currentUser?.name ? "border-gray-200 bg-gray-50 text-gray-400" : "border-gray-300 bg-white text-gray-900"}`}
                      >
                        <option value="viewer">viewer</option>
                        <option value="manager">manager</option>
                        <option value="admin">admin</option>
                      </select>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    <span className={`rounded px-2 py-0.5 text-xs font-medium ${u.enabled ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}`}>
                      {u.enabled ? "Active" : "Disabled"}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right">
                    {u.name !== currentUser?.name && u.enabled ? (
                      <button
                        onClick={() => disableMut.mutate(u.name)}
                        className="text-xs text-red-500 hover:text-red-700"
                      >
                        Disable
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

    </div>
  );
}
