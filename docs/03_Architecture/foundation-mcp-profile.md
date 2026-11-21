# Foundation MCP profile, version 1

What we would ask a wealth platform (WealthSmart) to expose so that Astra's agents can use it as the Foundation: the
system that owns a firm's parties, accounts, positions and activity, with Astra holding a synced copy. Backlog rows
B-164 (read side) and B-165 (orders out); the client is `backend/app/foundation/adapters/mcp.py`.

The profile is three tools on an ordinary MCP server. Nothing else in the server is read, so a platform can expose
this beside whatever other tools it offers.

## Transport

- MCP over **Streamable HTTP**, protocol version **2025-06-18** (2025-03-26 is accepted). One endpoint, `POST` for
  every JSON-RPC message.
- Astra sends `initialize` (capabilities `{}`, `clientInfo` `Astra for Wealth`), then `notifications/initialized`,
  then `tools/list` and `tools/call`. A server may answer each request with `application/json` or a
  `text/event-stream`; both are read. Notifications are answered `202 Accepted`.
- If the server returns `Mcp-Session-Id` on `initialize`, Astra sends it on every later request with
  `MCP-Protocol-Version`, and opens a new session once if a request is answered `404`. It sends `DELETE` at the end.
- Authentication is one header the firm's administrator configures on the connector (`auth_header`, default
  `Authorization`, and a secret `auth_value`, for example `Bearer …`). The server URL (`base_url`) must be `https://`
  and on the firm's allowed domains. A refusal (401/403) is reported without the credential.

## Records

Every record is a Foundation **contract v1** record (`backend/app/foundation/contract/`) with one extra field,
`entity`, naming which record it is. The platform's own id is `external_id`; a reference to another record
(`household_ref`, `person_ref`, `entity_ref`, `mandate_ref`, `account_ref`) is that record's `external_id` on the
same platform. An instrument is named by its listed `symbol`. `as_of` is when the platform last changed the record.

| Domain | Entities, in the order they are read | Notes |
|---|---|---|
| `parties` | `household`, `person`, `legal_entity` | A trust or company is a `legal_entity` with `entity_type` |
| `accounts` | `mandate`, `account` | A mandate names its model portfolio by name; cash is the account's `cash_balance` |
| `positions` | `holding`, `tax_lot` | `tax_lot` only if the platform keeps lots (see capabilities) |
| `activity` | `txn` | A trade that fills an order Astra sent carries `lineage.order_ref` = that order's id at the platform |

A record that does not fit the contract, or belongs to another domain, is skipped and counted by Astra, never guessed.
Prices are not in the profile: Astra values the book from its own licensed feed.

## Tools

### `foundation_capabilities`

No arguments. Returns what the platform supplies; Astra records it on the firm and reads it wherever a capability
matters (no tax lots: tax and rebalancing read "not assessed").

```json
{
  "profile": "foundation-mcp/1",
  "contract_versions": ["1"],
  "domains": ["parties", "accounts", "positions", "activity"],
  "capabilities": {"tax_lots": false, "transactions_days": 365, "events": false},
  "commands": ["orders.place"],
  "platform": {"name": "WealthSmart", "version": "…"}
}
```

`transactions_days` is how many days of trades the platform returns (`null` for all of them). A capability not
declared is read as not supplied.

### `foundation_read`

Arguments: `domain` (required), `cursor` (from the previous page), `contract_version` (`"1"`). Returns one page:

```json
{"domain": "positions", "contract_version": "1", "records": [{"entity": "holding", "external_id": "H-77", "...": "..."}],
 "next_cursor": "50"}
```

`next_cursor` is `null` on the last page. Every read returns the platform's whole current state of the domain; a record
missing from it is reported by Astra as "absent at the source", never deleted.

### `foundation_command`

Arguments: `kind`, `payload`, `idempotency_key` (Astra's command id; the same key twice must give the same answer).
Returns the platform's id for the command:

```json
{"status": "accepted", "external_id": "ORD-123", "received_at": "2026-11-21T10:00:00Z"}
```

A refusal is a tool error (`isError: true`) whose text says why; Astra shows those words and moves nothing.

The one command today is **`orders.place`**:

| Payload field | Meaning |
|---|---|
| `order_id` | Astra's order id |
| `account_external_id` | The account's id at the platform |
| `symbol`, `side` (`buy`/`sell`), `quantity` | The order |
| `order_type`, `limit_price` | `market` or `limit` |
| `est_price`, `reason` | What Astra expected, and the person's reason |

The platform fills the order its own way. Astra books nothing when it is sent: the holding, the cash and the trade
arrive on the next `foundation_read`, and the trade's `lineage.order_ref` (the command's `external_id`) confirms the
command and records the fill on Astra's order.

## What stays Astra's

Owners are set per domain by a firm's administrator (`set_foundation_owner`, behind a Confirm card). Before the card
and again at Confirm, an order still answers to Astra's own `order_guard`, the wash-sale guard, the IPS and the shelf,
read on the synced copy. A difference between the copy and the platform is a break with its evidence, never
overwritten. A firm whose positions are Astra's own trades exactly as before.

## Simulated today

Until WealthSmart exposes this, `backend/app/simulations/wealth_platform/` serves the profile in process for a
demonstration firm (B-221), and is discarded when the real server is connected: the connector is switched live with the
server's URL and nothing else changes.
