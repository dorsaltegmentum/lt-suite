<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

# Failure modes

| Symptom | Cause and fix |
|---|---|
| `answers.json answers different questions than this capture asks` | A prompt, type, option or authored constraint changed after the draft. Re-draft; do not re-point the draft. `python3 -m ltcore.digest <old> --diff <new>` names the items. |
| `answers.json is bound to a different capture` | The draft has no question digest, so only the file hash binds, and the capture moved. Re-draft against the current capture. |
| `ltcore.validate is not available` | The skill folder lacks its `_shared` copy. Reinstall the skill or run the sync tool. Exit 3, nothing exported. |
| `cannot read a week number out of lesson.title` | The title has no match for `curriculum.week_number_pattern`. Ask the user for the week and pass `--week`. |
| The ILO map says no section matched | The outcomes file has no unique section for this week and title. A person picks; the script never guesses. Check `curriculum.patterns.ilo_heading`. |
| `--install` exits 3 | `export.outputs.notes_graph` is off or `notes.graph_path` is empty. |
| `--install` exits 5 | `LT_SUITE_NO_INSTALL=1` is set. Unset it, or do not install. |
| Exit 4, `carries this module's stamp` | The user edited the installed page. Read the `.diff`, ask, and replace only on an explicit yes. |
| Exit 4, `cannot prove it is its own output` | A different file sits at the path. Choose a different page title. `--replace` never overwrites it. |
| Every re-export blocks on the page | Something edits the page between runs. Read the kept diff first. |
| A page shows `%2F` or `___` in its name | The file-name format differs from the graph's. The page still carries `title::`, which Logseq uses. Set `notes.file_name_format`. |
| Image keys reported as not found | The media step has not run on the pages that hold them. Run it, then export again. |
| `no usable sans font for PDF export` | `fonts.sans` does not resolve to a TrueType file. Run the setup skill, or set `fonts.sans_path`. |
| `no installed font has these characters` | The page uses characters no font covers. Set `fonts.sans` to a font that has them. |
| The final pass fails | `pdf.strip_metadata` is on and no strip tool is installed, a cleaner failed, or cleaning changed a page. Read `.final.json`. Rebuild before handing any PDF over. |
| QC lists defects | Read each `DEFECT` line. Render the page with `--render` and look at it. |
