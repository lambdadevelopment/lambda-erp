# Agent notes

Short, high-value context for any LLM (Claude Code, Codex, etc.) working on
this repo. Each file here captures information that **cannot be derived by
reading the code** — invariants, gotchas, and judgment calls that bit us
once and we don't want to rediscover.

## When to read what

| If you are… | Start with |
|---|---|
| Making any non-trivial change | `invariants.md` + `gotchas.md` |
| Touching GL/stock/returns/payments | `accounting.md` + `stock.md` |
| Touching the chat / WebSocket path | (add `chat.md` when you bite the first trap) |
| Verifying a change is safe to ship | `testing.md` |
| Wondering "why does the code do X and not Y?" | `decisions.md` |

## When to write a note

Add an entry when one of the following is true:

- You hit a bug whose root cause a reader wouldn't see from the code alone
  (e.g., the SQLite quoted-identifier quirk that silently returns string
  literals — documented in `gotchas.md`).
- You made a deliberate design choice that trades off against an obvious
  alternative, and the next person might undo it without knowing why
  (→ `decisions.md`).
- You discovered a loose invariant the code relies on but doesn't enforce
  (→ `invariants.md`).

## What NOT to put here

- Anything that duplicates `README.md` or `CLAUDE.md`.
- Code layout / file structure (readable from the tree).
- Full architectural essays. Keep each note under ~30 lines.
- Per-commit notes — those belong in commit messages.
- "Here's how the sales flow works" — that's derivable from the code and
  the CLAUDE.md overview.

## Convention

Each note: one concrete problem, the rule that fixes it, and (if useful)
the file:line that would have caught it. No prose paragraphs.
