# Micrographs for upload panels

Use an `upload-and-annotate` panel for an annotated copy of the student's own micrograph.

The student captures one field from the slide viewer named by their course.
Capture by hand or use the viewer's own export.

Do not bulk-download tiles. The old `grab_slide_tiles.js` does not ship.

## Annotate offline

Use `python3 "$SKILL_DIR/scripts/annotate_micrograph.py"` to draw leader lines and a caption.
Save the annotated copy as JPEG or PNG.

Use `--probe` to check that a point lands on tissue.
Use `python3 "$SKILL_DIR/scripts/gridref.py"` to read coordinates from a grid.

Label style rules come from `annotate.naming_rules` in the configuration.
Print them before labelling:

```bash
python3 "$SKILL_DIR/scripts/annotate_micrograph.py" --help-rules
```

An empty naming-rule list adds no label style rules.

The configuration also supplies `annotate.casing`, `annotate.ink`,
`annotate.margin_fill` and `annotate.font_path`.

The annotation script uses an existing `annotate.font_path` first.
Otherwise it resolves the configured sans font through `ltcore.fonts`.
Pillow's default font is the final fallback and emits a warning.

Keep the marks inside the uploaded image.
Lt's separate annotation overlay has no tested write path.

An `image-annotation` panel with `allowAnnotation` accepts no upload.
Record a TODO for that panel.

## Upload to the exact panel

Probe the page and identify the target by `(page_index, panel_key)`.

Lt renders file inputs lazily. Scroll to the bottom before checking their presence.
DOM order does not follow numeric panel-key order.

An occupied target requires approval before replacement.
Keep that approval separate from the later mechanical confirmation.

Render the upload script:

```bash
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" render upload --arg panel=<key> --file <png>
```

Run the generated file through your browser tool's evaluate-file action.

`$SKILL_DIR/scripts/browser/upload.js` sets the panel's own file input from embedded bytes.
It clears `input.value` first and dispatches `change`. It never clicks.

## Accept one replacement dialog per upload

Uploading over an existing image opens Lt's "Replace existing picture?" dialog.
`upload.js` reports `dialog_open: true` and records one pending upload for that panel.

After an approved replacement, write the dialog arguments and render the confirmation script:

```bash
python3 "$SKILL_DIR/scripts/emit_fill.py" dialog-args --module <module.json> --panel <key>
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" render confirm_dialog --args-file <printed path>
```

Run the generated file through your browser tool's evaluate-file action.

The confirmation script reads the one visible dialog node and clicks in that node only.
It checks the raw text against the deny list first, then the body text against the accept list.
It refuses two or more visible dialogs, two OK buttons, and a dialog with no pending upload for
the panel. Each accepted dialog binds one recorded upload. It never accepts dialogs in a loop.

Lt dialogs share markup and button labels.
Position, CSS classes and button order cannot identify a harmless replacement.

| Trap | Required action |
|---|---|
| A second dialog is open after the first is accepted. | Stop and report it. A dialog with no pending upload is refused, so it waits for the user. |
| Setting the same file twice may produce no `change` event. | Clear `input.value` before setting the file. `upload.js` performs this step. |
| Equal-size replacements preserve `presentedRatio`. | Verify replacement with `imageKey`. The ratio cannot distinguish images with equal dimensions. |
| A dialog is unrecognised or commit-shaped. | Stop confirmation and report it. Never broaden matching from button labels alone. |

The wait after the click is 6,000 ms. The receipt names the one request after the click that
carried the uploaded file (a body part of exactly its size). Once `emit_fill.py record --dialog
<receipt>` has calibrated the origin from such a request answered 2xx, the wait is the
matching request plus a quiet window; a failed or 202 answer, or a request still open at the
cap, reports `error` or `unconfirmed`.

Read the upload result back and verify persistence from a fresh load.
Do not infer success from a closed dialog or unchanged image dimensions.

## File the annotated copy

File a copy into the notes graph only when `notes.graph_path` is set.
Use `notes.assets_dir` for the asset destination.

Choose a descriptive filename for the annotated image.
Leave notes filing disabled when `notes.graph_path` is empty.
