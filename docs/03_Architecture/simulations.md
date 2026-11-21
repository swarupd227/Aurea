# Simulations, built to be thrown away

Some of what a client asked for depends on systems we do not have yet: their wealth platform, an aggregation feed for
money held elsewhere, a licensed news service, an RM's LinkedIn connections. To show and test those features now, a
**simulation** stands in for the missing source. The feature it feeds is real and stays; only the source is simulated.
When the real source arrives, the simulation is discarded and nothing else changes.

Asked for on 21 Nov 2026: "develop in a way that we can discard the simulations very easily later". Backlog rows
B-220 to B-225.

## The rules (enforced by `tests.verify_simulations`)

1. **One place.** Everything a simulation is - its provider, its fixture, its seed and its discard - lives in its own
   package, `backend/app/simulations/<key>/`. Nothing outside `app/simulations` imports from it.
2. **A real seam.** It plugs into a registry the real feature exposes and the real source will use too: a Foundation
   adapter (`foundation.adapters.register`), a held-away feed, a news source, a network import. The real feature is
   written against the seam, never against the simulation.
3. **One switch.** `install_all()` connects every simulation to its seam. It is called from `app.main` and the worker,
   and only when `settings.simulations` is on (`AUREA_SIMULATIONS`, on by default while we demonstrate).
4. **Opt-in per firm.** A simulation acts only for a firm whose `settings["simulations"]` lists it. Seeding adds the
   key; discarding takes it away. A firm that never opted in never sees one.
5. **Tagged rows.** Every row a simulation writes carries `simulated:<key>` - in `source_system` on the Foundation
   tables, or a `source` in its JSON attributes elsewhere - so the discard finds it. A seed writes nothing untagged:
   no audit or ledger entries, which could not be removed.
6. **It says so.** A simulated provider has `simulated = True`, the data it returns carries `simulated: true`, and
   every card and page shows a "simulated" chip from that flag. Nothing simulated reads as real.

## Using them

    python -m app.simulations status             # what is installed and seeded, firm by firm
    python -m app.simulations seed [key ...]     # seed onto the demonstration firms; safe to repeat
    python -m app.simulations discard [key ...]  # remove every row they wrote, and the opt-in; safe to repeat

In a deployed container: `az containerapp exec --name aurea-backend --resource-group aurea-rg --command
"python -m app.simulations seed"`.

## Throwing them away

1. `python -m app.simulations discard` - every simulated row on every firm is removed, and every opt-in.
2. Set `AUREA_SIMULATIONS=off` on the backend and worker - nothing is installed at a seam any more.
3. Later, when the code should go too: delete `backend/app/simulations/` and the two `install_all` call sites in
   `app/main.py` and `app/atlas/worker.py`. `tests.verify_simulations` and every other suite still pass.

`verify_simulations` proves, for every simulation it finds: no import from outside, nothing installed when off,
seeding twice adds nothing, and a discard leaves every table exactly as it was before the seed.

## The simulations

| Key | Stands for | Seam (the real feature) | Row |
|---|---|---|---|
| `wealth_platform` | The client's wealth platform (WealthSmart), reached through MCP | `foundation.adapters`: a real MCP client adapter that reads the Foundation contract from any MCP server speaking it; the simulation is the server and its data | B-221 |
| `dynamics_demo` | A Dynamics 365 environment for a demonstration firm | The Dynamics 365 adapter's mock mode, switched on for a simulated demonstration firm only | B-222 |
| `held_away_feed` | An aggregation feed or statements for money held elsewhere | The held-away holdings feature (B-198): a feed registry | B-223 |
| `news_source` | A licensed news and company-data service | The persona card (B-202): a news-source registry | B-224 |
| `linkedin_export` | An RM's own LinkedIn connections export | The network map and the connections import (B-201, internal part) | B-225 |

The real features each simulation feeds are B-164 and B-165 (the wealth platform), B-198, B-202 and B-201; the
simulation rows say what is simulated, the feature rows say what is real.

### `wealth_platform` and `dynamics_demo` (B-221, B-222)

- `python -m app.simulations seed wealth_platform dynamics_demo` makes the demonstration firm `platform-sim` (US,
  USD, marked `simulated_firm`), its staff, the instruments and models the platform's book names, and a live
  market-data connector; connects it to the in-process MCP server (a mock-mode `foundation.mcp` connector tagged
  `simulated`), hands it parties, accounts, positions and activity, runs a first sync and records each household's
  adviser (tagged); then puts the CRM on the Dynamics 365 mock with its own tagged connector and a first sync.
- Its people sign in from the login page and the role switcher: core reads any firm's `settings["personas"]`
  (`core/personas.firm_personas`), a seam that imports nothing from here. Password `aurea`, as for every demo persona.
- Discarding `wealth_platform` removes the firm and every row in it; on another firm it removes what the platform
  synced, its connector and its tagged rows, and hands the domains back to Astra. Discarding `dynamics_demo` hands the
  CRM back and removes what it synced.
- When WealthSmart exposes its own server: switch the `foundation.mcp` connector live with its URL and auth header on a
  real firm, then discard these. Delete `tests/verify_sim_wealth_platform.py` with the packages; the real adapter's
  suite, `tests.verify_foundation_mcp`, runs against its own test server and stays.
