# -------------------------------------------------------------------------
#
#  Part of the CodeChecker project, under the Apache License v2.0 with
#  LLVM Exceptions. See LICENSE for license information.
#  SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# -------------------------------------------------------------------------

"""
The names a What-If Constraint can be written on, at every Path Step of the
constraint trace fixtures, as the server resolves them.

The report page offers names from its own reading of the trace, and its jest
tests compare that reading with the same file, so the two cannot drift
apart without a test failing on one side. After a change to name
resolution or to the fixtures, run this test with UPDATE_WHAT_IF_NAMES=1 to
write the file again, and review the difference.
"""


import json
import os
import unittest

from codechecker_report_converter.report import report_file

from codechecker_server.api import what_if

from .test_what_if import CALLERS, MEMORY

FIXTURES = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'tests',
                        'projects', 'constraint_trace')
EXPECTED = os.path.join(FIXTURES, 'what_if_names.json')


def _names():
    """ For each fixture with a trace the check can read, the trace as the
    server stores and serves it, without its control flow graphs, which no
    name depends on, and the names at each step by `order`, anchored and
    throughout. The hand-written traces of the What-If unit tests come
    too, for what no fixture has, such as memory a call replaced at a step
    that still names its value before. """
    result = {name: _entry(trace) for name, trace in
              [('unit_callers', CALLERS), ('unit_memory', MEMORY)]}
    for run in sorted(os.listdir(FIXTURES)):
        directory = os.path.join(FIXTURES, run)
        if not os.path.isdir(directory):
            continue
        for plist in sorted(os.listdir(directory)):
            if not plist.endswith('.plist'):
                continue
            reports = [report for report in report_file.get_reports(
                os.path.join(directory, plist), None)
                if report.constraint_trace and
                report.constraint_trace.get('steps')]
            if not reports:
                continue
            trace = {key: value
                     for key, value in reports[0].constraint_trace.items()
                     if key != 'cfgs'}
            result[run] = _entry(json.dumps(trace, sort_keys=True))
    return result


def _entry(stored: str) -> dict:
    path = what_if.PreparedPath(stored)
    orders = sorted(step['order'] for step in json.loads(stored)['steps'])
    return {'trace': stored,
            'anchored': {str(order): _lines(what_if.constrainable_names(
                path, order)) for order in orders},
            'throughout': {str(order): _lines(what_if.constrainable_names(
                path, order, True)) for order in orders}}


def _lines(names):
    """ One line per name, `name = kind:value | kind:value`, which keeps the
    file short and its differences readable. """
    return [f"{name} = {' | '.join(values)}" for name, values in names]


class WhatIfNamesTest(unittest.TestCase):

    def test_fixture_names_are_the_ones_the_page_is_tested_with(self):
        names = _names()
        if os.environ.get('UPDATE_WHAT_IF_NAMES'):
            with open(EXPECTED, 'w', encoding='utf-8') as expected:
                json.dump(names, expected, indent=1, sort_keys=True)
                expected.write('\n')
        with open(EXPECTED, encoding='utf-8') as expected:
            self.assertEqual(json.load(expected), names)

    def test_fixtures_cover_the_kinds_of_names(self):
        """ The comparison is only as good as what the fixtures name. """
        names = {name for run in _names().values()
                 for step in run['anchored'].values()
                 for name in (line.split(' = ')[0] for line in step)}
        self.assertIn('top::n', names)
        self.assertIn('line->next->cells[1].flags', names)
        self.assertIn('flag', names)
