#!/usr/bin/env python3
"""Registered actions are one permissioned surface across REST, chat and MCP."""

from fastapi import HTTPException


def check_registered_actions():
    from api import chat, services
    from api.routers import actions, mcp

    old = dict(services.REGISTERED_ACTIONS)
    calls = []
    try:
        services.REGISTERED_ACTIONS.clear()

        def promote(args):
            calls.append(args["name"])
            return {"lead_name": "LEAD-001", "company_index": args["name"]}

        services.register_action(
            "promote_test_company",
            promote,
            description="Promote a test company to a lead.",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        )

        tool = next(
            t for t in chat.build_tools()
            if t["function"]["name"] == "promote_test_company"
        )
        assert tool["function"]["parameters"]["required"] == ["name"], tool
        assert "promote_test_company" in {
            t["function"]["name"] for t in chat.build_tools({"role": "manager"})
        }
        assert "promote_test_company" not in {
            t["function"]["name"] for t in chat.build_tools({"role": "viewer"})
        }

        manager = {"role": "manager"}
        result = actions.run_action("promote_test_company", {"name": "FIDX-001"}, manager)
        assert result["lead_name"] == "LEAD-001" and calls == ["FIDX-001"], result

        try:
            actions.run_action("promote_test_company", {"name": "FIDX-002"}, {"role": "viewer"})
            raise AssertionError("viewer unexpectedly ran a registered action")
        except HTTPException as exc:
            assert exc.status_code == 403, exc

        assert "promote_test_company" in {t["name"] for t in mcp._tools("manager")}
        assert "promote_test_company" not in {t["name"] for t in mcp._tools("viewer")}
        assert mcp._call("promote_test_company", {"name": "FIDX-003"}, manager)["lead_name"] == "LEAD-001"
    finally:
        services.REGISTERED_ACTIONS.clear()
        services.REGISTERED_ACTIONS.update(old)


def main():
    print("Registered action checks")
    check_registered_actions()
    print("All registered action checks passed.")


if __name__ == "__main__":
    main()
