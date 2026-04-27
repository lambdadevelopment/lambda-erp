# Relational traversal: the doctype model and where it stops

A design note on why Lambda ERP keeps the parent-with-child-tables
("doctype") schema, where that pattern hits its limits in the real
world, and which extensions matter once chat — not forms — is the
primary way users navigate the data.

This doc exists because the question keeps coming up, both internally
and from people reviewing the code: *"won't this fall apart on graph-
shaped data?"* The honest answer takes some untangling.

---

## The pattern we have

Every transactional document and every master record is a parent
record plus zero or more child tables, where each child row carries a
`parent` foreign key back to the header.

```
Sales Invoice "INV-2026-04-001"                       ← parent
├── customer:        CUST-042
├── posting_date:    2026-04-15
├── grand_total:     325.00
├── items[]:                                          ← child table
│   ├── { item: "Bolt Pack M8", qty: 100, rate: 2.50 }
│   ├── { item: "Wing Nut M8",  qty: 200, rate: 0.30 }
│   └── { item: "Locking Washer", qty: 150, rate: 0.10 }
└── taxes[]:                                          ← child table
    └── { type: "VAT 20%", rate: 20, amount: 65.00 }
```

The schema is uniform across every doctype:

```sql
CREATE TABLE "<Doctype>"      ( name TEXT PRIMARY KEY, ...header columns... );
CREATE TABLE "<Doctype> Item" ( name TEXT PRIMARY KEY, parent TEXT, ...row columns... );
```

This is the same shape ERPNext / Frappe, SAP Business One, and most
"document-as-unit-of-work" ERPs use. It's the right shape for
**transactional documents** because the unit of work *is* the document,
and the lifecycle (Draft → Submitted → Cancelled) maps cleanly to how
accounting and operations actually think.

For the LLM, it gives a single pattern to learn:
`create_document(doctype, {...header, items: [...]})` works the same
for every transaction type. One verb set, one lifecycle, one schema
shape.

## Where the pattern hits its limits

Three-level master-data hierarchies don't fit cleanly. The canonical
example, surfaced by a commenter:

> *Delivery Zone has many Zip Codes. Each Zip Code has many Delivery
> Windows (Monday 8–12, Tuesday 8–12, etc.). I want to see all three
> levels on one screen.*

In a strict parent-with-flat-children model, you have to pick:

- **Option A — Zone is standalone, Zip Code links to Zone via a Link
  field, Window is Zip Code's child.** Schema is correct. The Zip Code
  list view shows the link's primary key but can't display Zone's
  other columns inline without a custom report.
- **Option B — Zone is parent, Zip Code is child, Windows are stuffed
  into a JSON blob on each Zip Code row.** Everything is together but
  the Window data is unindexed, unfilterable, and a dead-end for any
  UX that wants per-window editing.

Both compromises are real. The pattern keeps recurring on the ERPNext
forums for the same reason. One implication critics sometimes draw is
that the data model itself is wrong and that the system needs the kind
of schema flexibility found in tools like Dynamics AX (separate tables,
rich relationships, forms with multiple data sources). We've taken a
different path, for reasons that follow.

## The diagnosis is partly wrong

Both Option A and Option B above have the schema *right* — Zone, Zip
Code, and Window are all separate tables, with foreign keys connecting
them. The thing that's broken in Option A isn't the data, it's the
**rendering and traversal layer**:

1. The Zip Code list view can't display columns from `Zone` inline.
2. Editing Zone + its Zip Codes + each Zip Code's Windows requires
   navigating between three pages.
3. There's no first-class way to express "give me the whole Zone
   subtree."

A relational JOIN solves (1). A nested grid solves (2). Depth-aware
traversal in the API solves (3). None of these requires the underlying
schema to morph into AX shape.

Highly configurable form/schema designers — *"design forms with as
many data sources as you want, combine any tables you like"* — are
powerful, but every flexibility point becomes a configuration decision
that has to be made, paid for, and maintained over time. That's a
known trade-off, and a meaningful share of implementation cost on
systems built around it goes to managing it. The thesis behind an
LLM-native ERP is that **opinionated structure plus targeted escape
hatches can cover the same surface area with less configuration
overhead**, because both LLMs and humans reason better when common
cases are pre-shaped and degrees of freedom are bounded. The doctype
model is *that* opinionated structure. The JS-sandbox custom analytics
layer is *that* escape hatch. What's underdeveloped today is the
middle layer — link rendering, depth-aware traversal, and composite
views.

## Chat-first changes which gaps actually matter

The critic's frustration boils down to: *"I want to see Zone +
Zip Codes + Windows on one screen so I can browse, filter, and edit."*
That's a real need only **if forms are the primary navigation
surface**. In a chat-first system, the same outcome is:

> "Show me all zip codes in NorthWest with their delivery windows"
> "Add a Saturday 9am–1pm window to every NorthWest zip code"
> "Which zones have zip codes with no Friday window?"

The user doesn't navigate — they ask. The LLM walks the relationships
via tool calls and composes the answer as a markdown table or, when
the user wants something visual, a custom analytics report.

So a large fraction of the *visual* gap that motivates more
configurable form/schema systems evaporates. **What does not evaporate
is the underlying need for the tool-call layer to traverse graphs
cheaply.** What used to be a forms problem is now a tools problem.

Specifically, when the LLM answers "show me everything about
NorthWest", it has to either:

- Issue 30+ sequential tool calls (Zone → ZipCode list → for each, a
  Windows list), each of which is a full LLM round-trip and counts
  against the per-turn budget. Slow and expensive.
- Or have a small set of traversal-aware primitives that return
  multi-level data in one call.

The second is what we want.

## What needs to be excellent

These three primitives carry most of the weight:

### 1. `fetch_from` on Link fields

When a doctype has a Link field to another doctype, allow the schema
to declare which of the linked record's columns should be projected
inline.

```python
# zip_code doctype declaration (sketch)
delivery_zone: LinkField(
    target="Delivery Zone",
    fetch_from=["cutoff_time", "priority"],  # ← read-only projections
)
```

Effect on tool calls:

```jsonc
// list_documents("Zip Code", filters={"delivery_zone": "NorthWest"})
[
  {
    "name": "ZIP-90210",
    "city": "Beverly Hills",
    "delivery_zone": "NorthWest",
    // ↓ materialised, not stored
    "delivery_zone__cutoff_time": "14:00",
    "delivery_zone__priority": 1
  },
  ...
]
```

Effect on list views: same row inlines the projected columns. No
custom report needed.

This is the **single biggest UX win for any link-heavy schema**, and
it benefits chat and forms equally. ERPNext has a similar mechanism
(`fetch_from`) and it's underused there because the surrounding
ergonomics are weak. We can make it the default for Link fields.

### 2. Depth-aware `get_document` / a `query_graph` primitive

Today `get_document(doctype, name)` returns the parent + its direct
child rows. Extend it (or add a parallel tool) to traverse linked
records and child tables to a specified depth:

```jsonc
// get_document("Delivery Zone", "ZONE-NW", expand=2)
{
  "name": "ZONE-NW",
  "cutoff_time": "14:00",
  "priority": 1,
  "_linked": {
    "zip_codes": [           // doctypes that link back via FK
      {
        "name": "ZIP-90210",
        "city": "Beverly Hills",
        "windows": [          // direct children
          {"day": "Monday",  "from": "08:00", "to": "12:00"},
          {"day": "Tuesday", "from": "08:00", "to": "12:00"}
        ]
      },
      ...
    ]
  }
}
```

Bounded depth keeps the response size sane. The LLM gets the whole
neighbourhood in one round-trip, which is what makes chat-driven graph
queries actually feel responsive.

### 3. Structured-data rendering inside the chat thread

The LLM doesn't have to *narrate* a 50-row result in markdown — the
backend already returns structured JSON. The chat UI can render that
JSON as a sortable, filterable table component inline, the same way it
already renders custom analytics reports. This closes the *trust* gap
that's otherwise the weakest point of chat-first interaction: a
deterministic grid is auditable in a way an LLM-narrated table isn't.

Already partly done for some tools; needs to be consistent across all
list/aggregation tool returns.

## What only needs to be acceptable

### Composite-view forms (multi-doctype on one page)

Genuinely useful for power users who prefer clicking through to
asking. Not high priority — chat handles ~80% of this use case better
once primitives 1 and 2 above land. A simple "Zone Detail" page that
shows Zone fields + a Zip Code grid + each Zip Code's Window grid
expandable on demand would close most of the rest, and can be a
declarative view spec (not a generic AX-style form designer).

### Multi-level master doctypes (parent + child + grandchild forms)

Desirable for the same reason — power users editing a graph in one
screen. Schema already supports it (Zip Code can be both child of Zone
and parent of Window); the form renderer is what's missing. Medium
priority.

## Boundaries we're holding

### Generic schema flexibility

We're not pursuing fully unconstrained schema configuration —
*"any doctype can link to any number of others, no constraints on
shape, design forms freely"* — as a direction we plan to take. The
trade-off there is well-trodden by other ERPs and adds real
configuration cost. Our bet is that *opinionated structure + LLM + JS
escape hatch* covers the long tail with materially less of that
overhead. If chat-driven traversal turns out not to carry the weight
we expect, this is the most plausible direction to revisit.

### True many-to-many relationships

If one Delivery Window genuinely covers multiple Zip Codes (rather
than each Zip Code having its own copy), the right model is a
separate linking doctype:

```
Window Coverage
├── window:  WINDOW-MON-8AM
└── zip:     ZIP-90210
```

Same compromise every doctype-style ERP makes. The LLM is fine with
it because creating a coverage row is just another `create_document`
call.

## Honest limits of chat-first traversal

We should not over-claim. Chat as the primary surface has real costs
that the form-based approach doesn't:

- **Performance compounding.** Even with `expand=N` and `fetch_from`,
  some questions ("rebuild last quarter's commissions across 4 levels
  of org hierarchy") are inherently many-step. They will be slower
  than a hand-tuned SQL query against a relational database.
- **Discoverability.** Forms expose schema by their existence. Chat
  assumes the user knows what to ask. Mitigations: empty-state
  suggestions, system-prompt hints, and the LLM proactively listing
  available fields when uncertain. Real cost.
- **Trust.** A 50-row LLM-narrated table is harder to audit than a
  paginated, sortable grid. Mitigated by primitive (3) above —
  structured returns rendered as deterministic UI components — but
  not eliminated for free-form aggregations.
- **High-frequency structured entry.** A warehouse picker doing 200
  stock-out actions per day will be faster on a form than chatting.
  Forms still need to exist for the operational hot path; they just
  don't need to be expressive enough to model arbitrary graphs.

These are bounded and known. None of them argues for moving toward
AX-style flexibility. They argue for the three primitives above plus
acceptable form rendering.

## Priority summary

| Enhancement                                     | Priority | Why                                              |
|-------------------------------------------------|----------|--------------------------------------------------|
| `fetch_from` on Link fields                     | High     | Biggest UX/tooling win per line of code          |
| Depth-aware `get_document` / `query_graph`      | High     | Linchpin for chat-driven graph traversal         |
| Structured-data rendering in chat thread        | High     | Closes the trust gap for chat as primary UI      |
| Multi-level master doctype forms (grandchildren editable in one screen) | Medium   | Useful for power users; chat covers most cases   |
| Composite-view forms (multi-doctype views)      | Low      | Forms-paradigm need that chat largely dissolves  |
| Generic schema flexibility (multi-data-source forms) | Deferred | Different cost shape; revisit if chat-first traversal underdelivers |

## TL;DR

The doctype model is right for the work it's doing and wrong only
because of a missing rendering/traversal layer that *both* doctype
ERPs (ERPNext, SAP B1) and AX-style ERPs (Dynamics, NetSuite custom
forms) get from different directions. We can close the gap without
touching the schema, by investing in tool-call primitives that
traverse cheaply and a chat UI that renders structured data
deterministically. Form-level composite views are nice to have but
no longer the load-bearing element they would be if Lambda ERP were
forms-first.

The bet: **chat substantially shrinks what would otherwise be a
forms-flexibility problem. An opinionated schema + strong traversal
primitives + an LLM is a meaningfully different operating-cost shape
than a fully configurable schema designed for human configuration —
and we think it's the right shape for this system.**
