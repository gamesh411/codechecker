# -------------------------------------------------------------------------
#
#  Part of the CodeChecker project, under the Apache License v2.0 with
#  LLVM Exceptions. See LICENSE for license information.
#  SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# -------------------------------------------------------------------------

""" Reading version 2 constraint traces, and keeping them through a plist. """

import unittest
from copy import deepcopy

from codechecker_report_converter.report import constraint_trace


def _range(symbol, value):
    return {'symbol': symbol, 'range': value}


def _variable(name, value, kind='symbol', smt=None, signed=True):
    variable = {'name': name, 'value': value, 'kind': kind}
    if smt:
        variable.update(smt=smt, smt_signed=signed)
    return variable


def make_snapshot(index):
    """ A long path where one more symbol is narrowed at every node, and a
    variable changes value now and then. """
    symbols = [f'reg_${i}<int a{i}>' for i in range(index + 1)]
    return {
        'symbol_ranges': [_range(symbols[-1], f'{{ [{index}, {index}] }}')],
        'variables': [_variable('i', str(index % 3), 'integer'),
                      _variable(f'a{index}', symbols[-1],
                                smt=f'reg_${index}')],
        'path_condition': [_range(s, f'{{ [{i}, {i}] }}')
                           for i, s in enumerate(symbols)],
        'smt_declarations': [f'(declare-fun reg_${i} () (_ BitVec 32))'
                             for i in (index - 1, index) if i >= 0],
        'smt_assertions': [f'(assert (= reg_${i} #x{i:08x}))'
                           for i in range(index + 1)],
        'cfg_position': {'cfg': 0, 'block': index, 'element': 0}}


class ConstraintTraceTest(unittest.TestCase):

    def setUp(self):
        self.snapshots = [make_snapshot(i) for i in range(150)]
        self.trace = constraint_trace.encode(
            deepcopy(self.snapshots),
            [{'path_index': i, 'exploded_node': i} for i in range(150)])

    def test_every_node_reads_back_whole(self):
        """ Also out of order and across checkpoints. """
        trace = constraint_trace.ConstraintTrace(self.trace)
        trace.check()
        for node in [149, 3, 64, 63, 128, 0, 100]:
            self.assertEqual(trace.snapshot(node), self.snapshots[node])

    def test_path_condition_is_only_added_to(self):
        for node in self.trace['exploded_nodes'][1:]:
            self.assertEqual(set(node['path_condition']), {'insert'})
            self.assertEqual(len(node['path_condition']['insert']), 1)

    def test_each_string_once(self):
        strings = self.trace['strings']
        self.assertEqual(len(strings), len(set(strings)))

    def test_lifetimes_cover_every_node(self):
        trace = constraint_trace.ConstraintTrace(self.trace)
        for field in ('variables', 'path_condition', 'smt_declarations'):
            lifetimes = trace.lifetimes(field)
            for node, snapshot in enumerate(self.snapshots):
                live = sorted(
                    (trace.entry(field, life.entry) for life in lifetimes
                     if life.start <= node < life.end), key=str)
                self.assertEqual(live, sorted(snapshot[field], key=str),
                                 (field, node))

    def test_unordered_lists_are_written_whole(self):
        """ Entries that change order between nodes still read back. """
        snapshots = [
            {'variables': [_variable('a', '1', 'integer'),
                           _variable('b', '2', 'integer')]},
            {'variables': [_variable('b', '2', 'integer'),
                           _variable('a', '1', 'integer')]}]
        trace = constraint_trace.ConstraintTrace(constraint_trace.encode(
            snapshots, []))
        self.assertEqual(trace.snapshot(1)['variables'],
                         snapshots[1]['variables'])

    def test_bad_traces_are_rejected(self):
        with self.assertRaises(constraint_trace.MalformedTrace):
            constraint_trace.ConstraintTrace({'version': 1, 'steps': []})
        bad_string = deepcopy(self.trace)
        bad_string['exploded_nodes'][0]['variables']['insert'][0][1] = 10 ** 6
        bad_node = deepcopy(self.trace)
        bad_node['steps'][0]['exploded_node'] = 150
        bad_position = deepcopy(self.trace)
        bad_position['exploded_nodes'][5]['path_condition']['insert'][0][0] \
            = 99
        for trace in (bad_string, bad_node, bad_position):
            with self.assertRaises(constraint_trace.MalformedTrace):
                constraint_trace.ConstraintTrace(trace).check()

    def test_cfgs_read_back_as_dicts(self):
        trace = constraint_trace.ConstraintTrace({
            **self.trace,
            'cfgs': [{'function': 'f', 'entry': 2, 'exit': 0,
                      'blocks': [[2, '', [1], []],
                                 [1, 'if a', [0, -1],
                                  [['a', 3, -1], ['a (cast)', 3, 0]]],
                                 [0, '', [], []]],
                      'path_blocks': [1, 2], 'path_edges': [2, 1, 1, 0]}]})
        self.assertEqual(trace.cfgs(), [{
            'function': 'f', 'entry': 2, 'exit': 0,
            'blocks': [
                {'id': 2, 'elements': [], 'successors': [1]},
                {'id': 1, 'successors': [0, -1], 'terminator': 'if a',
                 'elements': [{'text': 'a', 'line': 3},
                              {'text': 'a (cast)', 'line': 3, 'parent': 0}]},
                {'id': 0, 'elements': [], 'successors': []}],
            'path_blocks': [1, 2],
            'path_edges': [{'from': 2, 'to': 1}, {'from': 1, 'to': 0}]}])

    def test_graph_bodies_are_kept_once(self):
        """ Graphs with one body, whether one object or equal ones, name one
        entry of the file's table, and read back whole. """
        def graph(function):
            return {'function': function, 'entry': 1, 'exit': 0,
                    'blocks': [[1, '', [0], [['a', 3, -1]]], [0, '', [], []]]}

        table = constraint_trace.GraphTable()
        shared = graph('f')
        cfgs = [
            {**shared, 'path_blocks': [1], 'path_edges': []},
            {**shared, 'path_blocks': [0, 1], 'path_edges': [1, 0]},
            {**graph('f'), 'path_blocks': [], 'path_edges': []},
            {**graph('g'), 'path_blocks': [1], 'path_edges': []}]
        refs = [table.share(cfg) for cfg in cfgs]
        self.assertEqual([ref['graph'] for ref in refs], [0, 0, 0, 1])
        self.assertEqual(refs[1], {'graph': 0, 'path_blocks': [0, 1],
                                   'path_edges': [1, 0]})
        # The blocks are JSON text in the file.
        self.assertEqual(
            constraint_trace.read_graphs(table.graphs),
            [graph('f'), graph('g')])
        self.assertIsInstance(table.graphs[0]['blocks'], str)

        resolved = constraint_trace.resolve_cfgs(
            {'version': 2, 'cfgs': refs},
            constraint_trace.read_graphs(table.graphs))
        self.assertEqual(resolved['cfgs'], cfgs)
        self.assertIs(resolved['cfgs'][0]['blocks'],
                      resolved['cfgs'][1]['blocks'])

    def test_whole_graphs_are_kept_and_bad_names_rejected(self):
        whole = {'function': 'f', 'entry': 0, 'exit': 0, 'blocks': [],
                 'path_blocks': [], 'path_edges': []}
        trace = {'version': 2, 'cfgs': [whole]}
        self.assertEqual(constraint_trace.resolve_cfgs(trace, []), trace)
        body = {'function': 'f', 'entry': 0, 'exit': 0}
        for graphs in ([], [{'function': 'f'}], [{**body, 'blocks': '[[0,'}],
                       [{**body, 'blocks': '{}'}]):
            with self.assertRaises(constraint_trace.MalformedTrace):
                constraint_trace.resolve_cfgs(
                    {'version': 2, 'cfgs': [{'graph': 0}]},
                    constraint_trace.read_graphs(graphs))


CALL_RESULT = 'conj_$2{int, LC1, S1234, #1}'
SYMBOLS = {
    'reg_$0<int a0>': {'kind': 'region_value', 'smt': 'reg_$0',
                       'smt_signed': True},
    CALL_RESULT: {
        'kind': 'conjured', 'smt': 'conj_$2', 'smt_signed': True,
        'origin': {'reason': 'call_result', 'statement': 'get()',
                   'location': {'line': 4, 'col': 7, 'file': 0},
                   'callee': 'get', 'function': 'f', 'frame': 0}},
    'conj_$3{int, LC2, S99, #1}': {
        'kind': 'conjured',
        'origin': {'reason': 'other', 'function': 'wrap'}}}


class SymbolTableTest(unittest.TestCase):

    def setUp(self):
        self.snapshot = {
            'symbol_ranges': [],
            'variables': [_variable('a0', 'reg_$0<int a0>')],
            'path_condition': [
                _range(f'({CALL_RESULT}) + 1', '{ [5, 5] }'),
                _range('conj_$3{int, LC2, S99, #1}', '{ [7, 7] }')]}
        self.trace = {
            **constraint_trace.encode([self.snapshot], [], deepcopy(SYMBOLS)),
            'stack_frames': [{'function': 'f'}]}

    def test_table_reads_back_by_dump(self):
        trace = constraint_trace.ConstraintTrace(self.trace)
        trace.check()
        self.assertEqual(trace.symbols(), SYMBOLS)
        self.assertIs(trace.symbols(), trace.symbols())
        # Symbol dumps in the table are the strings the lists use.
        strings = self.trace['strings']
        self.assertEqual(len(strings), len(set(strings)))

    def test_trace_without_a_table_has_no_symbols(self):
        trace = constraint_trace.ConstraintTrace(
            constraint_trace.encode([self.snapshot], []))
        trace.check()
        self.assertNotIn('symbols', trace.trace)
        self.assertEqual(trace.symbols(), {})

    def test_bad_tables_are_rejected(self):
        def broken(change):
            trace = deepcopy(self.trace)
            change(trace['symbols'])
            return trace

        def origin(symbols):
            return next(s['origin'] for s in symbols if 'origin' in s)

        for trace in (
                broken(lambda s: s[0].update(symbol=10 ** 6)),
                broken(lambda s: s[0].pop('kind')),
                broken(lambda s: s[0].update(smt=-1)),
                broken(lambda s: origin(s).update(function='f')),
                broken(lambda s: origin(s).update(frame=3)),
                broken(lambda s: origin(s).update(location={'line': 1})),
                broken(lambda s: s.append(7)),
                {**self.trace, 'symbols': {}}):
            with self.assertRaises(constraint_trace.MalformedTrace):
                constraint_trace.ConstraintTrace(trace).check()
