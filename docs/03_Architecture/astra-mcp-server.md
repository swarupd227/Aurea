# Astra as an MCP server (B-166)

The Foundation layer reaches a wealth platform through MCP: Astra is the client (`foundation.mcp`,
[foundation-mcp-profile.md](foundation-mcp-profile.md)). This is the other direction. An agent outside Astra, such as a
firm's own orchestrator, a desktop assistant or another vendor's agent, calls Astra's tools. It calls them **as one named
person**, through a key that person made for it.

## What it is

| | |
|---|---|
| Endpoint | `POST /api/mcp` on the backend |
| Protocol | MCP over Streamable HTTP, JSON-RPC 2.0, protocol `2025-06-18` (`2025-03-26` also accepted). Answers are plain JSON. No session state is kept between requests. |
| Methods | `initialize`, `notifications/*` (202), `ping`, `tools/list`, `tools/call`. `GET` and `DELETE` answer 405, because the server sends no stream and has no session. Batches answer 400. |
| Sign-in | `Authorization: Bearer astra_oa_…`, an outside agent key. A person's own sign-in opens nothing here, and the key opens nothing anywhere else. |
| Tools | The conversation's catalogue for the person's role (`core/tools.py`), less `open_view`, plus `confirmation_status`. |
| Code | `aurea_core/outside_agents.py` (rules), `api/outside_agents.py` (endpoint and keys), `models/outside_agent.py` |
| Checks | `python -m tests.verify_outside_agents` drives the real routes over HTTP, using Astra's own MCP client as the outside agent. |

## The rules

1. **Off until the administrator switches it on.** The agent control `outside_agents` belongs to the administrator and
   is changed with a reason on the audit log (`rulebook.SETTINGS`). When it is off, no key can be made and every key
   already made is refused.
2. **A key is a person's.** Staff make their own key on their Outside agents panel (the robot button beside the account
   controls). It is shown once and only its SHA-256 hash is kept. A key lasts 1 to 90 days. A client and the platform
   owner make none. The key is a secret, so it is never made in, or shown in, the conversation.
3. **The person's rights, in the person's firm.** Every call goes through `validate_tool_call` for the person's role,
   and then through the same executors as the conversation. So firm scope, decision rights and the domain rules all
   apply unchanged. A tool the role does not hold is refused and counted as a `tool_role_forbidden` violation, just as
   it is in the conversation.
4. **A change waits for the person.**
   - When a state-changing tool is called, it is checked now with the conversation's own preview. If the change would
     only fail, it is refused with its reason.
   - Otherwise a Confirm card is put to the person on the key's own conversation, titled *Outside agent: <name>*. The
     card also appears in Needs you.
   - The call returns a `confirmation_id`. Nothing has changed at that point.
   - The person presses Confirm or Not now in Astra. The gateway then carries the change out at most once, exactly as
     it does for the conversation's own cards (`Gateway.confirm_pending_action`).
   - The agent reads the outcome with `confirmation_status`: waiting, done with its result, failed, or not done.
   - The agent cannot confirm, because the confirm route takes a person's sign-in and the key is not one.
   - A confirmation lapses after 24 hours, as in the conversation.
5. **Checked on every request.** A key stops working at once when:
   - it is revoked or has expired;
   - its person is deactivated or moves to a role that makes no keys;
   - the firm is switched off, or outside agents are switched off.
6. **Revoking** is done by the key's person or the administrator, with a reason. Any confirmation the key is still
   waiting on is declined. In the conversation, `revoke_outside_agent` does this behind a Confirm card, and
   `read_outside_agents` lists the keys, never a key's value.
7. **On the audit log.** Every call records the key, the tool and whether it worked (`outside_agent.tool_call`), but
   never the call's inputs or its result. Making and revoking a key are logged too.

## What it does not do

- **No client access.** Rule 8 of the product model keeps a client to their own household with no state-changing
  tools. Opening `CLIENT_TOOLS` to a client's outside agent would be a separate decision.
- **No masking.** An outside agent reads what its person may read, unmasked, as the page shows it. The firm's PII
  redaction protects the model provider behind Astra's own conversation; it does not apply to a person's own agent.
  The administrator's switch is the control.
- **No rate limit** yet beyond the platform's own.
- **No OAuth.** A key is a bearer token that the person pastes into their agent. OAuth 2.1 authorization, as the MCP
  specification describes it for remote servers, is the natural next step if a client asks for it.
