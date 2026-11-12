import { useState } from "react";
import { usePageTitle } from "@/lib/use-page-title";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type AccessProfile } from "@/api/client";
import { useAuth } from "@/contexts/auth-context";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { usePrompt } from "@/components/ui/dialog";

type ScopeKind = "customer" | "warehouse";
const SCOPE_KINDS: { key: ScopeKind; label: string }[] = [
  { key: "customer", label: "customers" },
  { key: "warehouse", label: "warehouses" },
];

interface ScopeEdit {
  name: string;
  fullName: string;
  restricted: Record<ScopeKind, boolean>;
  values: Record<ScopeKind, string>;
}

function scopeSummary(scope: Record<string, any> | null | undefined): string {
  if (!scope) return "all";
  if (scope.error) return "unreadable: locked out";
  const parts = SCOPE_KINDS.filter(({ key }) => key in scope)
    .map(({ key, label }) => `${(scope[key] as string[]).length} ${label}`);
  return parts.length ? parts.join(", ") : "all";
}

export default function UsersPage() {
  usePageTitle("Users");
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

  const revokeMut = useMutation({
    mutationFn: (token: string) => api.authRevokeInvite(token),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["auth-invites"] }),
  });

  const { data: profileData } = useQuery({
    queryKey: ["auth-access-profiles"],
    queryFn: () => api.authListAccessProfiles(),
    enabled: currentUser?.role === "admin",
  });
  const profiles = profileData?.profiles ?? [];
  const refreshProfiles = () => {
    queryClient.invalidateQueries({ queryKey: ["auth-access-profiles"] });
    queryClient.invalidateQueries({ queryKey: ["auth-users"] });
  };
  const assignProfileMut = useMutation({
    mutationFn: ({ name, profile }: { name: string; profile: string | null }) =>
      api.authAssignAccessProfile(name, profile),
    onSuccess: refreshProfiles,
  });

  const [scopeEdit, setScopeEdit] = useState<ScopeEdit | null>(null);
  const [scopeError, setScopeError] = useState<string | null>(null);
  const editScope = (u: any) => {
    setScopeError(null);
    const scope: Record<string, string[]> = u.record_scope && !u.record_scope.error ? u.record_scope : {};
    setScopeEdit({
      name: u.name,
      fullName: u.full_name || u.email,
      restricted: { customer: "customer" in scope, warehouse: "warehouse" in scope },
      values: { customer: (scope.customer ?? []).join(", "), warehouse: (scope.warehouse ?? []).join(", ") },
    });
  };
  const saveScopeMut = useMutation({
    mutationFn: () => {
      if (!scopeEdit) throw new Error("Nothing to save");
      const scope: Record<string, string[]> = {};
      for (const { key } of SCOPE_KINDS) {
        if (scopeEdit.restricted[key]) {
          scope[key] = scopeEdit.values[key].split(",").map((v) => v.trim()).filter(Boolean);
        }
      }
      return api.authAssignRecordScope(scopeEdit.name, Object.keys(scope).length ? scope : null);
    },
    onSuccess: () => {
      setScopeEdit(null);
      queryClient.invalidateQueries({ queryKey: ["auth-users"] });
    },
    onError: (error) => setScopeError((error as Error).message),
  });

  const emptyProfile: AccessProfile = { name: "", description: "", hidden_classes: [], permissions: {} };
  const [editing, setEditing] = useState<{ profile: AccessProfile; isNew: boolean; permissionsText: string } | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);
  const editProfile = (profile: AccessProfile, isNew: boolean) => {
    setProfileError(null);
    const doctypes = profile.permissions?.doctypes ?? {};
    const hasRules = Object.keys(doctypes).length > 0 || profile.permissions?.default != null;
    setEditing({ profile: { ...profile }, isNew, permissionsText: hasRules ? JSON.stringify(profile.permissions, null, 2) : "" });
  };
  const saveProfileMut = useMutation({
    mutationFn: async () => {
      if (!editing) return;
      let permissions = {};
      if (editing.permissionsText.trim()) {
        try {
          permissions = JSON.parse(editing.permissionsText);
        } catch {
          throw new Error("Action rules must be valid JSON");
        }
      }
      return api.authSaveAccessProfile({ ...editing.profile, permissions }, editing.isNew);
    },
    onSuccess: () => {
      setEditing(null);
      refreshProfiles();
    },
    onError: (error) => setProfileError((error as Error).message),
  });
  const deleteProfileMut = useMutation({
    mutationFn: (name: string) => api.authDeleteAccessProfile(name),
    onSuccess: refreshProfiles,
    onError: (error) => setProfileError((error as Error).message),
  });

  const [copiedToken, setCopiedToken] = useState<string | null>(null);
  const prompt = usePrompt();
  const inviteLink = (token: string) => `${window.location.origin}/login?invite=${token}`;
  const copyInviteLink = async (token: string) => {
    try {
      await navigator.clipboard.writeText(inviteLink(token));
      setCopiedToken(token);
      setTimeout(() => setCopiedToken((t) => (t === token ? null : t)), 2000);
    } catch {
      // Clipboard API unavailable (e.g. non-HTTPS): show the link in a dialog
      // (a selectable field) so the admin can still copy it manually.
      void prompt({ title: "Invite link", body: "Copy this link:", defaultValue: inviteLink(token) });
    }
  };

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
                <th className="px-4 py-2 text-right font-medium text-gray-500">Actions</th>
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
                  <td className="px-4 py-2 text-right">
                    <div className="flex items-center justify-end gap-2">
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => copyInviteLink(invite.token)}
                      >
                        {copiedToken === invite.token ? "Copied!" : "Copy link"}
                      </Button>
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={() => revokeMut.mutate(invite.token)}
                        disabled={revokeMut.isPending}
                      >
                        Revoke
                      </Button>
                    </div>
                  </td>
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
                <th className="px-4 py-2 text-left font-medium text-gray-500">Access profile</th>
                <th className="px-4 py-2 text-left font-medium text-gray-500">Assigned records</th>
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
                    {u.role === "public_manager" || u.name === currentUser?.name ? (
                      <span className="text-xs text-gray-400">{u.access_profile || "none"}</span>
                    ) : (
                      <select
                        value={u.access_profile || ""}
                        onChange={(e) => assignProfileMut.mutate({ name: u.name, profile: e.target.value || null })}
                        className="rounded border border-gray-300 bg-white px-2 py-1 text-xs text-gray-900"
                      >
                        <option value="">none (role only)</option>
                        {profiles.map((p) => (
                          <option key={p.name} value={p.name}>{p.name}{p.disabled ? " (disabled)" : ""}</option>
                        ))}
                        {u.access_profile && !profiles.some((p) => p.name === u.access_profile) ? (
                          <option value={u.access_profile}>{u.access_profile} (missing: locked out)</option>
                        ) : null}
                      </select>
                    )}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-600">
                    <span>{scopeSummary(u.record_scope)}</span>
                    {u.role !== "public_manager" && u.name !== currentUser?.name ? (
                      <button type="button" className="ml-2 text-blue-600 hover:underline" onClick={() => editScope(u)}>
                        Edit
                      </button>
                    ) : null}
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

      {scopeEdit && (
        <Card title={`Assigned records: ${scopeEdit.fullName}`}>
          <p className="mb-3 text-sm text-gray-500">
            Restrict this user to assigned customers or warehouses. They then see and write only documents, stock and
            reports of those; anything that cannot be limited to them (ledger reports, the dashboard) is refused.
            A kind that is not restricted is left as it is. Restricted with nothing listed means none at all.
          </p>
          <div className="space-y-3">
            {SCOPE_KINDS.map(({ key, label }) => (
              <div key={key}>
                <label className="flex items-center gap-2 text-sm font-medium text-gray-900">
                  <input
                    type="checkbox"
                    checked={scopeEdit.restricted[key]}
                    onChange={(e) => setScopeEdit({ ...scopeEdit, restricted: { ...scopeEdit.restricted, [key]: e.target.checked } })}
                  />
                  Restrict to assigned {label}
                </label>
                {scopeEdit.restricted[key] && (
                  <input
                    className="mt-1 w-full rounded border border-gray-300 px-2 py-1 text-sm"
                    placeholder={`IDs of the ${label}, separated by commas`}
                    value={scopeEdit.values[key]}
                    onChange={(e) => setScopeEdit({ ...scopeEdit, values: { ...scopeEdit.values, [key]: e.target.value } })}
                  />
                )}
              </div>
            ))}
            {scopeError && <p className="text-sm text-red-600">{scopeError}</p>}
            <div className="flex gap-2">
              <Button onClick={() => saveScopeMut.mutate()} disabled={saveScopeMut.isPending}>Save</Button>
              <Button variant="secondary" onClick={() => setScopeEdit(null)}>Cancel</Button>
            </div>
          </div>
        </Card>
      )}

      {/* Access profiles */}
      <Card title="Access Profiles">
        <p className="mb-3 text-sm text-gray-600">
          A profile narrows what a user&apos;s role allows: it hides classes of sensitive data everywhere
          (documents, lists, reports, PDFs, chat) and can take away actions per document type.
          Users without a profile are not restricted. A disabled or deleted profile locks its users out.
        </p>
        {profiles.length > 0 && (
          <table className="mb-4 min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-2 text-left font-medium text-gray-500">Profile</th>
                <th className="px-4 py-2 text-left font-medium text-gray-500">Hides</th>
                <th className="px-4 py-2 text-left font-medium text-gray-500">Action rules</th>
                <th className="px-4 py-2 text-right font-medium text-gray-500">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {profiles.map((p) => (
                <tr key={p.name}>
                  <td className="px-4 py-2">
                    <div className="font-medium text-gray-900">{p.name}{p.disabled ? " (disabled)" : ""}</div>
                    <div className="text-xs text-gray-500">{p.description}</div>
                  </td>
                  <td className="px-4 py-2 text-gray-700">{p.hidden_classes.join(", ") || "nothing"}</td>
                  <td className="px-4 py-2 text-gray-700">
                    {Object.keys(p.permissions?.doctypes ?? {}).length + (p.permissions?.default != null ? 1 : 0) || "none"}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <div className="flex items-center justify-end gap-2">
                      <Button variant="secondary" size="sm" onClick={() => editProfile(p, false)}>Edit</Button>
                      <Button variant="danger" size="sm" onClick={() => deleteProfileMut.mutate(p.name)}>Delete</Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {editing ? (
          <div className="space-y-3 rounded-lg border border-gray-200 p-4">
            <div className="flex flex-wrap gap-4">
              <Input
                label="Name"
                value={editing.profile.name}
                disabled={!editing.isNew}
                onChange={(e) => setEditing({ ...editing, profile: { ...editing.profile, name: e.target.value } })}
              />
              <Input
                label="Description"
                value={editing.profile.description ?? ""}
                onChange={(e) => setEditing({ ...editing, profile: { ...editing.profile, description: e.target.value } })}
              />
            </div>
            <div>
              <div className="mb-1 text-sm font-medium text-gray-900">Hide</div>
              {Object.entries(profileData?.sensitivity_classes ?? {}).map(([cls, description]) => (
                <label key={cls} className="flex items-start gap-2 py-1 text-sm">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={editing.profile.hidden_classes.includes(cls)}
                    onChange={(e) => setEditing({
                      ...editing,
                      profile: {
                        ...editing.profile,
                        hidden_classes: e.target.checked
                          ? [...editing.profile.hidden_classes, cls]
                          : editing.profile.hidden_classes.filter((c) => c !== cls),
                      },
                    })}
                  />
                  <span><span className="font-mono text-xs">{cls}</span> <span className="text-gray-600">{description}</span></span>
                </label>
              ))}
              <p className="mt-1 text-xs text-gray-500">
                Hiding purchase prices also hides cost, supplier balances and the financial statements;
                hiding cost also hides the financial statements.
              </p>
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-900" htmlFor="profile-permissions">
                Action rules (optional, JSON)
              </label>
              <textarea
                id="profile-permissions"
                className="h-32 w-full rounded border border-gray-300 p-2 font-mono text-xs"
                placeholder={'{"default": ["read"], "doctypes": {"Sales Order": ["read", "create", "update"], "Supplier": []}}'}
                value={editing.permissionsText}
                onChange={(e) => setEditing({ ...editing, permissionsText: e.target.value })}
              />
              <p className="mt-1 text-xs text-gray-500">
                Per document or master type (by its name, e.g. &quot;Sales Order&quot;, &quot;Supplier&quot;), the actions that
                remain: {(profileData?.actions ?? []).join(", ")}. An empty list hides the type. &quot;default&quot; applies to
                every type not listed; leave it out to allow whatever the role allows.
              </p>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={!!editing.profile.disabled}
                onChange={(e) => setEditing({ ...editing, profile: { ...editing.profile, disabled: e.target.checked } })}
              />
              Disabled (locks its users out)
            </label>
            {profileError && <p className="text-sm text-red-600">{profileError}</p>}
            <div className="flex gap-2">
              <Button onClick={() => saveProfileMut.mutate()} disabled={!editing.profile.name.trim() || saveProfileMut.isPending}>
                Save profile
              </Button>
              <Button variant="secondary" onClick={() => setEditing(null)}>Cancel</Button>
            </div>
          </div>
        ) : (
          <>
            {profileError && <p className="mb-2 text-sm text-red-600">{profileError}</p>}
            <Button variant="secondary" onClick={() => editProfile(emptyProfile, true)}>New profile</Button>
          </>
        )}
      </Card>

    </div>
  );
}
