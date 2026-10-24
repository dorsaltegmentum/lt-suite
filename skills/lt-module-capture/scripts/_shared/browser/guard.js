// Generated from shared/browser/guard.js by tools/sync_shared.py. Edit the source.
// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* guard.js: the never-submit guard alone. Run it right after every navigation:
 *
 *     python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" render guard --form <form> [--plan plan.json]
 *
 * ltjs.py prepends lt_guard.js, so this body only reports what the guard installed: the four
 * commit mutators (locked, wrapped, rewrapped, absent or no-root), the form wrappers, the
 * event and confirm() guards, the navigate listener and the navigation policy. Pass the fill
 * plan with --plan so the navigate listener knows the stop and the checkpoint pages; without
 * it every page change is cancelled. A forbidden route returns
 * {ok:false, fatal:'forbidden-route'} before this body runs.
 */
return LtGuard.status();
