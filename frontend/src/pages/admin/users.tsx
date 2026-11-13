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
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";

type ScopeKind = "customer" | "warehouse";
const SCOPE_KINDS: { key: ScopeKind }[] = [{ key: "customer" }, { key: "warehouse" }];

interface ScopeEdit {
  name: string;
  fullName: string;
  restricted: Record<ScopeKind, boolean>;
  values: Record<ScopeKind, string>;
}

function scopeSummary(scope: Record<string, any> | null | undefined, t: TFunction): string {
  if (!scope) return t("users.scope.all");
  if (scope.error) return t("users.scope.unreadable");
  const parts = SCOPE_KINDS.filter(({ key }) => key in scope)
    .map(({ key }) => t(`users.scope.count.${key}`, { count: (scope[key] as string[]).length }));
  return parts.length ? parts.join(", ") : t("users.scope.all");
}

export default function UsersPage() {
  const { t } = useTranslation();
  usePageTitle(t("users.title"));
  const { user: currentUser } = useAuth();
  const roleLabel = (role: string) => t(`users.role.${role}`, { defaultValue: role });
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
      if (!scopeEdit) throw new Error(t("users.nothingToSave"));
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
          throw new Error(t("users.profiles.invalidJson"));
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
      void prompt({ title: t("users.invite.linkTitle"), body: t("users.invite.linkBody"), defaultValue: inviteLink(token) });
    }
  };

  if (currentUser?.role !== "admin") {
    return <p className="py-8 text-center text-gray-400">{t("users.adminRequired")}</p>;
  }

  return (
    <div className="space-y-6">
      {/* Role explanation */}
      <Card title={t("users.roles.title")}>
        <div className="grid gap-3 text-sm sm:grid-cols-3">
          <div className="rounded-lg border border-blue-200 bg-blue-50 p-3">
            <div className="font-semibold text-blue-900">{roleLabel("admin")}</div>
            <p className="mt-1 text-blue-700">
              {t("users.roles.adminText")}
            </p>
          </div>
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
            <div className="font-semibold text-amber-900">{roleLabel("manager")}</div>
            <p className="mt-1 text-amber-700">
              {t("users.roles.managerText")}
            </p>
          </div>
          <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
            <div className="font-semibold text-gray-900">{roleLabel("viewer")}</div>
            <p className="mt-1 text-gray-600">
              {t("users.roles.viewerText")}
            </p>
          </div>
        </div>
      </Card>

      {/* Invite form */}
      <Card title={t("users.invite.title")}>
        <div className="flex flex-wrap items-end gap-4">
          <Input
            label={t("users.email")}
            type="email"
            value={inviteEmail}
            onChange={(e) => setInviteEmail(e.target.value)}
            placeholder={t("users.invite.placeholder")}
          />
          <Select
            label={t("users.roleColumn")}
            options={["viewer", "manager", "admin"].map((role) => ({ value: role, label: roleLabel(role) }))}
            value={inviteRole}
            onChange={(e) => setInviteRole(e.target.value)}
          />
          <Button
            onClick={() => inviteMut.mutate()}
            disabled={!inviteEmail.trim() || inviteMut.isPending}
          >
            {inviteMut.isPending ? t("users.invite.sending") : t("users.invite.create")}
          </Button>
        </div>
        {inviteMut.error && (
          <p className="mt-2 text-sm text-red-600">{(inviteMut.error as Error).message}</p>
        )}
        {inviteResult && (
          <div className="mt-3 rounded-md bg-green-50 px-4 py-3 text-sm">
            <p className="font-medium text-green-800">{t("users.invite.created")}</p>
            <p className="mt-1 text-green-700">
              {t("users.invite.share")}{" "}
              <code className="rounded bg-green-100 px-2 py-0.5 text-xs font-mono">
                {window.location.origin}{inviteResult.link}
              </code>
            </p>
          </div>
        )}
      </Card>

      {/* Pending invites */}
      {invites && invites.filter((i: any) => !i.used).length > 0 && (
        <Card title={t("users.invite.pending")}>
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-2 text-left font-medium text-gray-500">{t("users.email")}</th>
                <th className="px-4 py-2 text-left font-medium text-gray-500">{t("users.roleColumn")}</th>
                <th className="px-4 py-2 text-left font-medium text-gray-500">{t("users.created")}</th>
                <th className="px-4 py-2 text-right font-medium text-gray-500">{t("users.actions")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {invites.filter((i: any) => !i.used).map((invite: any) => (
                <tr key={invite.token}>
                  <td className="px-4 py-2">{invite.email}</td>
                  <td className="px-4 py-2">
                    <span className="rounded bg-gray-100 px-2 py-0.5 text-xs font-medium">{roleLabel(invite.role)}</span>
                  </td>
                  <td className="px-4 py-2 text-gray-500">{invite.creation?.split("T")[0]}</td>
                  <td className="px-4 py-2 text-right">
                    <div className="flex items-center justify-end gap-2">
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => copyInviteLink(invite.token)}
                      >
                        {copiedToken === invite.token ? t("users.invite.copied") : t("users.invite.copy")}
                      </Button>
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={() => revokeMut.mutate(invite.token)}
                        disabled={revokeMut.isPending}
                      >
                        {t("users.invite.revoke")}
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
      <Card title={t("users.team")}>
        {isLoading ? (
          <p className="text-gray-500">{t("common.loading")}</p>
        ) : (
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-2 text-left font-medium text-gray-500">{t("users.name")}</th>
                <th className="px-4 py-2 text-left font-medium text-gray-500">{t("users.email")}</th>
                <th className="px-4 py-2 text-left font-medium text-gray-500">{t("users.roleColumn")}</th>
                <th className="px-4 py-2 text-left font-medium text-gray-500">{t("users.profileColumn")}</th>
                <th className="px-4 py-2 text-left font-medium text-gray-500">{t("users.scope.column")}</th>
                <th className="px-4 py-2 text-left font-medium text-gray-500">{t("users.status")}</th>
                <th className="px-4 py-2 text-right font-medium text-gray-500">{t("users.actions")}</th>
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
                        {t("users.role.public_manager")}
                      </span>
                    ) : (
                      <select
                        value={u.role}
                        onChange={(e) => changeRoleMut.mutate({ name: u.name, role: e.target.value })}
                        disabled={u.name === currentUser?.name}
                        className={`rounded border px-2 py-1 text-xs ${u.name === currentUser?.name ? "border-gray-200 bg-gray-50 text-gray-400" : "border-gray-300 bg-white text-gray-900"}`}
                      >
                        <option value="viewer">{roleLabel("viewer")}</option>
                        <option value="manager">{roleLabel("manager")}</option>
                        <option value="admin">{roleLabel("admin")}</option>
                      </select>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    {u.role === "public_manager" || u.name === currentUser?.name ? (
                      <span className="text-xs text-gray-400">{u.access_profile || t("users.profiles.none")}</span>
                    ) : (
                      <select
                        value={u.access_profile || ""}
                        onChange={(e) => assignProfileMut.mutate({ name: u.name, profile: e.target.value || null })}
                        className="rounded border border-gray-300 bg-white px-2 py-1 text-xs text-gray-900"
                      >
                        <option value="">{t("users.profiles.noneRoleOnly")}</option>
                        {profiles.map((p) => (
                          <option key={p.name} value={p.name}>{p.name}{p.disabled ? ` (${t("users.profiles.disabledTag")})` : ""}</option>
                        ))}
                        {u.access_profile && !profiles.some((p) => p.name === u.access_profile) ? (
                          <option value={u.access_profile}>{u.access_profile} ({t("users.profiles.missing")})</option>
                        ) : null}
                      </select>
                    )}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-600">
                    <span>{scopeSummary(u.record_scope, t)}</span>
                    {u.role !== "public_manager" && u.name !== currentUser?.name ? (
                      <button type="button" className="ml-2 text-blue-600 hover:underline" onClick={() => editScope(u)}>
                        {t("common.edit")}
                      </button>
                    ) : null}
                  </td>
                  <td className="px-4 py-2">
                    <span className={`rounded px-2 py-0.5 text-xs font-medium ${u.enabled ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}`}>
                      {u.enabled ? t("common.active") : t("common.disabled")}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right">
                    {u.name !== currentUser?.name && u.enabled ? (
                      <button
                        onClick={() => disableMut.mutate(u.name)}
                        className="text-xs text-red-500 hover:text-red-700"
                      >
                        {t("common.disable")}
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
        <Card title={t("users.scope.editTitle", { name: scopeEdit.fullName })}>
          <p className="mb-3 text-sm text-gray-500">
            {t("users.scope.intro")}
          </p>
          <div className="space-y-3">
            {SCOPE_KINDS.map(({ key }) => (
              <div key={key}>
                <label className="flex items-center gap-2 text-sm font-medium text-gray-900">
                  <input
                    type="checkbox"
                    checked={scopeEdit.restricted[key]}
                    onChange={(e) => setScopeEdit({ ...scopeEdit, restricted: { ...scopeEdit.restricted, [key]: e.target.checked } })}
                  />
                  {t(`users.scope.restrict.${key}`)}
                </label>
                {scopeEdit.restricted[key] && (
                  <input
                    className="mt-1 w-full rounded border border-gray-300 px-2 py-1 text-sm"
                    placeholder={t(`users.scope.placeholder.${key}`)}
                    value={scopeEdit.values[key]}
                    onChange={(e) => setScopeEdit({ ...scopeEdit, values: { ...scopeEdit.values, [key]: e.target.value } })}
                  />
                )}
              </div>
            ))}
            {scopeError && <p className="text-sm text-red-600">{scopeError}</p>}
            <div className="flex gap-2">
              <Button onClick={() => saveScopeMut.mutate()} disabled={saveScopeMut.isPending}>{t("common.save")}</Button>
              <Button variant="secondary" onClick={() => setScopeEdit(null)}>{t("common.cancel")}</Button>
            </div>
          </div>
        </Card>
      )}

      {/* Access profiles */}
      <Card title={t("users.profiles.title")}>
        <p className="mb-3 text-sm text-gray-600">
          {t("users.profiles.intro")}
        </p>
        {profiles.length > 0 && (
          <table className="mb-4 min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-2 text-left font-medium text-gray-500">{t("users.profiles.profile")}</th>
                <th className="px-4 py-2 text-left font-medium text-gray-500">{t("users.profiles.hides")}</th>
                <th className="px-4 py-2 text-left font-medium text-gray-500">{t("users.profiles.rules")}</th>
                <th className="px-4 py-2 text-right font-medium text-gray-500">{t("users.actions")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {profiles.map((p) => (
                <tr key={p.name}>
                  <td className="px-4 py-2">
                    <div className="font-medium text-gray-900">{p.name}{p.disabled ? ` (${t("users.profiles.disabledTag")})` : ""}</div>
                    <div className="text-xs text-gray-500">{p.description}</div>
                  </td>
                  <td className="px-4 py-2 text-gray-700">{p.hidden_classes.map((cls) => t(`users.classes.${cls}.name`, { defaultValue: cls })).join(", ") || t("users.profiles.nothing")}</td>
                  <td className="px-4 py-2 text-gray-700">
                    {Object.keys(p.permissions?.doctypes ?? {}).length + (p.permissions?.default != null ? 1 : 0) || t("users.profiles.none")}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <div className="flex items-center justify-end gap-2">
                      <Button variant="secondary" size="sm" onClick={() => editProfile(p, false)}>{t("common.edit")}</Button>
                      <Button variant="danger" size="sm" onClick={() => deleteProfileMut.mutate(p.name)}>{t("common.delete")}</Button>
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
                label={t("users.name")}
                value={editing.profile.name}
                disabled={!editing.isNew}
                onChange={(e) => setEditing({ ...editing, profile: { ...editing.profile, name: e.target.value } })}
              />
              <Input
                label={t("users.profiles.description")}
                value={editing.profile.description ?? ""}
                onChange={(e) => setEditing({ ...editing, profile: { ...editing.profile, description: e.target.value } })}
              />
            </div>
            <div>
              <div className="mb-1 text-sm font-medium text-gray-900">{t("users.profiles.hide")}</div>
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
                  <span>
                    <span className="font-medium">{t(`users.classes.${cls}.name`, { defaultValue: cls })}</span>{" "}
                    <span className="text-gray-600">{t(`users.classes.${cls}.text`, { defaultValue: description })}</span>
                  </span>
                </label>
              ))}
              <p className="mt-1 text-xs text-gray-500">
                {t("users.profiles.implies")}
              </p>
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-900" htmlFor="profile-permissions">
                {t("users.profiles.rulesLabel")}
              </label>
              <textarea
                id="profile-permissions"
                className="h-32 w-full rounded border border-gray-300 p-2 font-mono text-xs"
                placeholder={'{"default": ["read"], "doctypes": {"Sales Order": ["read", "create", "update"], "Supplier": []}}'}
                value={editing.permissionsText}
                onChange={(e) => setEditing({ ...editing, permissionsText: e.target.value })}
              />
              <p className="mt-1 text-xs text-gray-500">
                {t("users.profiles.rulesHelp", { actions: (profileData?.actions ?? []).join(", ") })}
              </p>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={!!editing.profile.disabled}
                onChange={(e) => setEditing({ ...editing, profile: { ...editing.profile, disabled: e.target.checked } })}
              />
              {t("users.profiles.disabledLabel")}
            </label>
            {profileError && <p className="text-sm text-red-600">{profileError}</p>}
            <div className="flex gap-2">
              <Button onClick={() => saveProfileMut.mutate()} disabled={!editing.profile.name.trim() || saveProfileMut.isPending}>
                {t("users.profiles.save")}
              </Button>
              <Button variant="secondary" onClick={() => setEditing(null)}>{t("common.cancel")}</Button>
            </div>
          </div>
        ) : (
          <>
            {profileError && <p className="mb-2 text-sm text-red-600">{profileError}</p>}
            <Button variant="secondary" onClick={() => editProfile(emptyProfile, true)}>{t("users.profiles.new")}</Button>
          </>
        )}
      </Card>

    </div>
  );
}
