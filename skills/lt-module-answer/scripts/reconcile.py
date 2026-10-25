#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prepare reconciliation work, validate authored reconciliation, or rebind after RAT fills."""
from __future__ import annotations
import argparse
import copy
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent/'_shared'))
import ltcore
ltcore.bootstrap([])
from ltcore import config, reconcile, store, validate


class InputParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(3, self.prog + ': error: ' + message + '\n')


def main(argv=None):
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap=InputParser(description=__doc__)
    subs=ap.add_subparsers(dest='command',required=True)
    for name in ('prepare','check','rebind'):
        p=subs.add_parser(name)
        for arg in ('module','answers','fill-report'):
            p.add_argument('--'+arg,required=True,type=Path)
        for arg in ('config','data-dir'):
            p.add_argument('--'+arg)
        if name=='prepare':p.add_argument('--out',type=Path)
        else:p.add_argument('--reconciled',required=True,type=Path)
        if name=='rebind':p.add_argument('--previous-fill-report',required=True,type=Path)
    args=ap.parse_args(argv)
    try:
        cfg=config.load(args.config)
        # Guard every source path before opening it. Reconciliation never enters a RAT root.
        from ltcore import rat
        roots=[r for _,r in rat.effective_roots(rat.policy_from_config(cfg))]
        inputs=[args.module,args.answers,args.fill_report]
        if args.command!='prepare':inputs.append(args.reconciled)
        if args.command=='rebind':inputs.append(args.previous_fill_report)
        if any(rat.within(p,r) for p in inputs for r in roots):
            raise ValueError('reconcile input enters a RAT root')
        module=json.loads(args.module.read_text(encoding='utf-8'));answers=json.loads(args.answers.read_text(encoding='utf-8'));report=json.loads(args.fill_report.read_text(encoding='utf-8'))
        if any(d.get('module_id')!=module['module_id'] for d in (answers,report)) or report.get('source_answers',{}).get('sha256')!=reconcile.sha(args.answers):
            raise ValueError('source_answers pin or module differs')
        if args.command=='prepare':
            validity=validate.validate_file(args.fill_report,'fill-report',module=args.module,config_path=args.config)
            if validity.exit_code:
                print('reconcile: invalid fill report',file=sys.stderr);return 3
            work=reconcile.worklist(module,answers,report)
            if not work['items']:return 2
            dest=args.out or store.Store(cfg,data_dir=args.data_dir).run_dir(module['module_id'])/'reconcile-worklist.json'
            reconcile.atomic_json(dest,work);print(dest);return 0
        doc=json.loads(args.reconciled.read_text(encoding='utf-8'))
        # Explicit inputs must match the pins. The validator also checks the referenced files.
        old=args.previous_fill_report if args.command=='rebind' else args.fill_report
        if doc.get('source_answers',{}).get('sha256')!=reconcile.sha(args.answers) or doc.get('source_fill_report',{}).get('sha256')!=reconcile.sha(old):
            print('reconciled-binding: stale source pin; re-run reconcile',file=sys.stderr);return 1
        result=validate.validate_doc(doc,'reconciled',module=module,base_dir=args.reconciled.parent,config_path=args.config)
        if result.exit_code:
            print('\n'.join(result.schema_errors+result.rail_errors+[result.fatal or '']),file=sys.stderr);return result.exit_code
        if args.command=='rebind':
            final_validation=validate.validate_file(args.fill_report,'fill-report',module=args.module,config_path=args.config)
            if final_validation.exit_code:
                print('rebind: invalid final fill report',file=sys.stderr);return final_validation.exit_code
            previous=json.loads(old.read_text(encoding='utf-8'))
            updated=reconcile.rebind_document(doc,previous,report)
            updated['source_fill_report']={'path':str(args.fill_report.resolve()),'sha256':reconcile.sha(args.fill_report)}
            result=validate.validate_doc(updated,'reconciled',module=module,base_dir=args.reconciled.parent,config_path=args.config)
            if result.exit_code:
                print('\n'.join(result.schema_errors+result.rail_errors),file=sys.stderr);return result.exit_code
            reconcile.atomic_json(args.reconciled,updated)
        print('reconciled: valid');return 0
    except (OSError,ValueError,KeyError,TypeError,config.ConfigError) as exc:
        print('reconcile: input or forbidden-change error: '+str(exc),file=sys.stderr);return 3


if __name__=='__main__':raise SystemExit(main())
