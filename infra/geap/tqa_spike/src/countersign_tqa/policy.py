"""Technical Quality Agreement: tenant + lifecycle for department MCP tools.

Wrong-department deny is native MCP tool-name policy, not this module.
"""

from __future__ import annotations

from dataclasses import dataclass

DEPARTMENT_TOOLS = frozenset({"lims_query", "em_query", "coa_fetch"})
SPONSORS_WITH_TQA = frozenset({"aurex", "novara"})
DEPARTMENT_READ_STATES = frozenset({"classified", "under_investigation"})

REASON_ALLOW = "allow"
REASON_NOT_TOOLS_CALL = "not_tools_call"
REASON_MISSING_TUPLE = "missing_tuple"
REASON_TENANT_MISMATCH = "tenant_mismatch"
REASON_UNKNOWN_SPONSOR = "unknown_sponsor"
REASON_LIFECYCLE_DENIED = "lifecycle_denied"
REASON_TOOL_NOT_COVERED = "tool_not_covered"


@dataclass(frozen=True)
class McpToolCall:
    name: str
    tenant_id: str | None
    case_tenant_id: str | None
    lifecycle_state: str | None


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str

    @property
    def details(self) -> str:
        return f"tqa_{self.reason}"


def authorize_department_read(call: McpToolCall) -> Decision:
    """Fail closed on department tools/call. Other MCP methods are not this check."""
    if call.name not in DEPARTMENT_TOOLS:
        if not (call.name or "").strip():
            return Decision(False, REASON_MISSING_TUPLE)
        return Decision(True, REASON_TOOL_NOT_COVERED)
    tenant = (call.tenant_id or "").strip()
    case_tenant = (call.case_tenant_id or "").strip()
    state = (call.lifecycle_state or "").strip()
    if not tenant or not case_tenant or not state:
        return Decision(False, REASON_MISSING_TUPLE)
    if tenant != case_tenant:
        return Decision(False, REASON_TENANT_MISMATCH)
    if tenant not in SPONSORS_WITH_TQA:
        return Decision(False, REASON_UNKNOWN_SPONSOR)
    if state not in DEPARTMENT_READ_STATES:
        return Decision(False, REASON_LIFECYCLE_DENIED)
    return Decision(True, REASON_ALLOW)
    tenant = (call.tenant_id or "").strip()
    case_tenant = (call.case_tenant_id or "").strip()
    state = (call.lifecycle_state or "").strip()
    if not tenant or not case_tenant or not state:
        return Decision(False, REASON_MISSING_TUPLE)
    if tenant != case_tenant:
        return Decision(False, REASON_TENANT_MISMATCH)
    if tenant not in SPONSORS_WITH_TQA:
        return Decision(False, REASON_UNKNOWN_SPONSOR)
    if state not in DEPARTMENT_READ_STATES:
        return Decision(False, REASON_LIFECYCLE_DENIED)
    return Decision(True, REASON_ALLOW)
