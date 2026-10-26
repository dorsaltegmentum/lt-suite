// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* The renderer creates lexical library instances before dispatching this call with frozen arguments. */
/*__LT_FILL_LIB__*/
LtFillLib.loadProbe = function () {
/*__LT_PROBE__*/
  return probeLtPanels;
};
/*__LT_CHECK__*/
switch (__LT_ARGS__.method) {
  case 'fillPage': return await LtFillLib.fillPage(__LT_ARGS__.cfg, __LT_ARGS__.jobs);
  case 'fillPages': return await LtFillLib.fillPages(__LT_ARGS__.cfg, __LT_ARGS__.jobs);
  case 'verify': return await LtFillLib.verify(__LT_ARGS__.cfg, __LT_ARGS__.jobs);
  case 'check': return await LtCheck.run(__LT_ARGS__.cfg, __LT_ARGS__.jobs);
  case 'checkVerify': return await LtCheck.verify(__LT_ARGS__.cfg, __LT_ARGS__.jobs);
  case 'probe': return await LtFillLib.loadProbe()(__LT_ARGS__.jobs);
  default: return {ok:false, fatal:'runner-method'};
}
