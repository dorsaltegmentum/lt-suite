# fill-notes.json

`fill-notes.json` holds what only the operator saw during a fill run.
`make_fill_report.py` reads it with `receipts.json` and writes `fill-report.json`.
The schema is `scripts/_shared/schemas/fill-notes.schema.json`.
`emit_fill.py payload` writes a template to `<run>/fill-notes.json` when the run has none.
Edit that file as the run goes; never move a note to another file.

## A full example

```json
{
 "approval_mode": "auto",
 "items": {
  "p4-31": {"result": "failed", "attempted": true,
            "stopped_reason": "The verify read the old text twice after two fills."},
  "p5-12": {"result": "todo", "attempted": false,
            "stopped_reason": "Left for typing by hand at the student's request.",
            "todo": {"blocker": "The user types it.", "placement": "page 5, panel 12"}},
  "p7-40": {"method": "view-model", "stopped_reason": "Written on the second call."}
 },
 "connection_events": [
  {"at": "2026-10-29T11:46:30+11:00", "kind": "screenshot-timeout",
   "detail": "The screenshot call timed out on a background tab."}
 ],
 "user_decisions": [],
 "run_notes": ["Page 5 needed a 3 s wait before the guard."]
}
```

## Keys

| Key | Rule |
|---|---|
| `approval_mode` | Required: `manual`, `auto` or `skip-all`, as the browser tool was supervised. The template leaves it null. |
| `started_at`, `ended_at` | Optional. The defaults are the earliest bound payload `generated_at` (with no bound payload, the earliest payload in the run folder; the first receipt only when the run folder holds none) and the last receipt. A value that differs needs `times_reason`. A receipt observed before its own payload's `generated_at` refuses whatever the notes say. |
| `times_reason` | Why a time differs. The report keeps it in `run_notes`. |
| `items` | `{item_id: note}`. See below. |
| `connection_events` | `{at, kind, detail}`. `kind` is one of `worker-idle`, `modal-block`, `navigation-timeout`, `screenshot-timeout`, `content-filter-block`. |
| `user_decisions` | Waivers, each with its `approval_id` from `approvals.json`. |
| `selector_events` | Selector ledger rows: `selector`, `item_type`, `outcome` (`hit` or `miss`), `recorded_at`. |
| `boundary_reached`, `pages_filled`, `never_navigated` | Optional overrides of the plan's values. |
| `run_notes` | Free text, one string per note. Every other key is refused. |

## Item notes

- An item with a receipt takes only `stopped_reason`, `method`, `selector_used` and `lt_conflict`.
  Its result comes from the receipt. A receipt without `source_payload` (recorded before
  payload binding) gives `filled-unverified` or `failed`, never `filled`, and the report item's
  receipt carries `binding: unbound-legacy`.
- An item with no receipt may set `result` (`failed`, `filled-unverified`, `skipped` or
  `todo`), `attempted` and `todo`. A note that sets `result` needs `stopped_reason`.
- No note sets `verified` or result `filled`.
- A drafted item the plan marks fillable needs a receipt or a note.
- A single-attempt draft with no `single_attempt_approval` needs no note. The plan holds it,
  the payload refuses it, and the report lists it skipped, "yours to check and submit".

## Submitted

A single-attempt item is submitted only when the capture reads `attempts` single,
`read_only` true and `answered` true. `answered: true` alone is not submitted. plan_fill.py
prints each single's state, for example "answered, not submitted (read_only false,
commit_state open)", and lists passed submitted singles under ALREADY COMMITTED.

## Checking

```bash
python3 "$SKILL_DIR/scripts/make_fill_report.py" --module <module.json> --answers <answers.json> --check-notes
```

It runs every report check, prints each problem at once, and writes nothing. Exit 0 means
the report would be built; exit 3 lists the problems. Before the fill, every planned item
shows as missing a receipt or a note.
