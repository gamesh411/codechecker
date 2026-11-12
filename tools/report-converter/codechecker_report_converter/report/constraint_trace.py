# -------------------------------------------------------------------------
#
#  Part of the CodeChecker project, under the Apache License v2.0 with
#  LLVM Exceptions. See LICENSE for license information.
#  SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# -------------------------------------------------------------------------
"""
Reading a constraint trace, the per-report record of what the analyzer knew
at each Path Step.

A trace of version 2 keeps one Constraint Snapshot per analyzer node, in
`exploded_nodes`, and a step names its node by `exploded_node`. Each node's
lists are written as the change from the node before it, as indices into the
trace's `strings`, so the trace grows with the length of the Bug Path rather
than with its square. This module is the one place that turns that back into
snapshots, whether whole or as the span of nodes each entry lives through.

A trace may also have a table of the atomic symbols its snapshots mention,
`symbols`, keyed by the symbol's dump in `strings`, with the symbol's kind,
its SMT-LIB term and, for a conjured symbol, the origin Clang recorded.
`ConstraintTrace.symbols` reads it, and a trace from a Clang before the table
reads as having no symbols. A trace may also have the types of the symbolic
expressions its snapshots constrain, `expressions`, which
`ConstraintTrace.expressions` reads.

A trace's control flow graphs are self-contained once read, but a plist keeps
each graph's body once per file, in `PLIST_GRAPHS`, and a trace names it by
`graph`: the reports of one translation unit run through the same functions
again and again. `resolve_cfgs` and `GraphTable` turn one into the other.
"""

import hashlib
import json
from dataclasses import dataclass

VERSION = 2

# The snapshot lists, in the order a node lists their changes.
LISTS = ('symbol_ranges', 'variables', 'path_condition', 'smt_declarations')

# The plist key of a file's control flow graph bodies.
PLIST_GRAPHS = 'constraint_trace_cfgs'

# The keys of a control flow graph that make up its body, which does not
# depend on the path.
_GRAPH_BODY = ('function', 'entry', 'exit', 'blocks')

# The keys of a symbol's origin, and of the symbol itself, that name a
# string.
_ORIGIN_STRINGS = ('statement', 'callee', 'function', 'region')
_SYMBOL_STRINGS = ('parent', 'region', 'type', 'name')

# Whole snapshots kept on the way, so that reading any node replays at most
# this many deltas.
_CHECKPOINT_INTERVAL = 64


class MalformedTrace(ValueError):
    """ The trace does not decode. """


@dataclass(frozen=True)
class Lifetime:
    """ An entry of a snapshot list, and the nodes it is in: from `start` up
    to but not including `end`. """
    entry: object
    start: int
    end: int


def _position(index, size: int) -> int:
    # Python would take a negative index from the end, and an insertion past
    # the end as an append, so a broken trace would read as another one.
    if not isinstance(index, int) or not 0 <= index < size:
        raise MalformedTrace(f"position {index} outside a list of {size}")
    return index


def _apply(entries: list, delta: dict) -> None:
    """ Turn the list into the next node's, in place. """
    for operation in delta.get('replace', ()):
        entries[_position(operation[0], len(entries))] = tuple(operation[1:])
    for index in reversed(delta.get('remove', ())):
        del entries[_position(index, len(entries))]
    for operation in delta.get('insert', ()):
        entries.insert(_position(operation[0], len(entries) + 1),
                       tuple(operation[1:]))


class ConstraintTrace:
    """ A version 2 constraint trace, read lazily. """

    def __init__(self, trace: dict):
        if trace.get('version') != VERSION:
            raise MalformedTrace(
                f"constraint trace version {trace.get('version')} is not "
                f"{VERSION}")
        try:
            self.trace = trace
            self.strings: list[str] = trace['strings']
            self.nodes: list[dict] = trace['exploded_nodes']
            self.steps: list[dict] = trace['steps']
        except KeyError as ex:
            raise MalformedTrace(f"constraint trace without {ex}") from ex
        self.smtlib = bool(trace.get('smtlib'))
        self._checkpoints: dict[int, dict[str, list]] = {}
        self._symbols: dict[str, dict] | None = None
        self._expressions: dict[str, dict] | None = None

    def _apply_node(self, lists: dict[str, list], index: int) -> None:
        node = self.nodes[index]
        for field in LISTS:
            if field in node:
                _apply(lists[field], node[field])

    def check(self) -> None:
        """ Raise MalformedTrace unless every node and step decodes. Each
        entry is checked once, where a node adds it. """
        count = len(self.strings)
        widths = {'symbol_ranges': (2,), 'variables': (3, 5),
                  'path_condition': (3,) if self.smtlib else (2,),
                  'smt_declarations': (1,)}
        lists: dict[str, list] = {field: [] for field in LISTS}
        try:
            for index, node in enumerate(self.nodes):
                for field in LISTS:
                    delta = node.get(field)
                    if delta is None:
                        continue
                    for operation in delta.get('replace', []) + \
                            delta.get('insert', []):
                        entry = operation[1:]
                        # A variable's fifth value is its signedness.
                        names = entry[:4] if field == 'variables' else entry
                        if len(entry) not in widths[field] or not all(
                                isinstance(value, int) and 0 <= value < count
                                for value in names):
                            raise MalformedTrace(
                                f"node {index}: {field} entry {entry} does "
                                "not name its strings")
                    _apply(lists[field], delta)
            for step in self.steps:
                if not 0 <= step['exploded_node'] < len(self.nodes):
                    raise MalformedTrace(
                        f"step names node {step['exploded_node']}")
            self._check_symbols()
        except (KeyError, IndexError, TypeError, AttributeError) as ex:
            raise MalformedTrace(f"constraint trace does not decode: {ex}") \
                from ex

    def _check_symbols(self) -> None:
        table = self.trace.get('symbols', [])
        if not isinstance(table, list):
            raise MalformedTrace("symbol table is not a list")
        frames = len(self.trace.get('stack_frames', []))
        for entry in table:
            if not self._symbol_reads(entry, frames):
                raise MalformedTrace(
                    f"symbol table entry {entry} does not read")
        expressions = self.trace.get('expressions', [])
        if not isinstance(expressions, list):
            raise MalformedTrace("expression types are not a list")
        for entry in expressions:
            if not self._expression_reads(entry):
                raise MalformedTrace(
                    f"expression type entry {entry} does not read")

    def _expression_reads(self, entry: dict) -> bool:
        def string(value) -> bool:
            return isinstance(value, int) and 0 <= value < len(self.strings)

        return string(entry['symbol']) and \
            ('type' not in entry or string(entry['type'])) and \
            isinstance(entry.get('pointer', True), bool)

    def _symbol_reads(self, entry: dict, frames: int) -> bool:
        """ Whether the entry names its strings and frame. A location's file
        is a plist index in a plist and a path once stored, so only its
        presence counts. """
        def string(value) -> bool:
            return isinstance(value, int) and 0 <= value < len(self.strings)

        def location(value) -> bool:
            return isinstance(value, dict) and \
                isinstance(value.get('line'), int) and \
                isinstance(value.get('col'), int) and 'file' in value

        if not isinstance(entry['kind'], str) or \
                not string(entry['symbol']) or \
                not all(string(entry[key]) for key in ('smt',) +
                        _SYMBOL_STRINGS if key in entry) or \
                not isinstance(entry.get('pointer', True), bool):
            return False
        origin = entry.get('origin')
        if origin is None:
            return True
        return isinstance(origin['reason'], str) and \
            string(origin['function']) and \
            all(string(origin[key]) for key in _ORIGIN_STRINGS
                if key in origin) and \
            ('frame' not in origin or
             isinstance(origin['frame'], int) and
             0 <= origin['frame'] < frames) and \
            isinstance(origin.get('globals', False), bool) and \
            all(location(origin[key]) for key in ('location', 'loop')
                if key in origin)

    def symbols(self) -> dict[str, dict]:
        """ The symbol table by symbol dump, with its strings read: `kind`,
        with SMT-LIB `smt` and `smt_signed` when the symbol has a term, and
        for a conjured symbol `origin`, which has `reason` and `function`
        and, when Clang knows them, `statement`, `location`, `callee` and
        `frame` (an index into `stack_frames`), and for memory a call or a
        loop replaced, `region` or `globals`, and `loop`. A derived symbol
        has its `parent`'s dump and its `region`'s access path, an extent
        the `region` whose size it is, and a region value the `name` of its
        region's access path, each when Clang can describe it. A symbol
        has its `type`, the canonical type's name, and `pointer` when the
        analyzer treats it as an address. """
        if self._symbols is not None:
            return self._symbols
        strings = self.strings
        table = {}
        for entry in self.trace.get('symbols', []):
            symbol = {'kind': entry['kind']}
            symbol.update({key: strings[entry[key]]
                           for key in _SYMBOL_STRINGS if key in entry})
            if entry.get('pointer'):
                symbol['pointer'] = True
            if 'smt' in entry:
                symbol['smt'] = strings[entry['smt']]
                symbol['smt_signed'] = bool(entry['smt_signed'])
            if 'origin' in entry:
                symbol['origin'] = {
                    key: strings[value] if key in _ORIGIN_STRINGS else value
                    for key, value in entry['origin'].items()}
            table[strings[entry['symbol']]] = symbol
        self._symbols = table
        return table

    def expressions(self) -> dict[str, dict]:
        """ The symbolic expressions the snapshots constrain or variables
        hold, by dump: their `type` and `pointer` as a symbol has them. An
        expression has no entry in `symbols`; its atoms do. """
        if self._expressions is not None:
            return self._expressions
        strings = self.strings
        table = {}
        for entry in self.trace.get('expressions', []):
            expression = {}
            if 'type' in entry:
                expression['type'] = strings[entry['type']]
            if entry.get('pointer'):
                expression['pointer'] = True
            table[strings[entry['symbol']]] = expression
        self._expressions = table
        return table

    def _raw(self, node: int) -> dict[str, list]:
        """ The raw lists of `node`, replayed from the nearest checkpoint. """
        def copy(lists):
            return {field: list(entries) for field, entries in lists.items()}

        start = node - node % _CHECKPOINT_INTERVAL
        made = start
        while made > 0 and made not in self._checkpoints:
            made -= _CHECKPOINT_INTERVAL
        if made not in self._checkpoints:
            first: dict[str, list] = {field: [] for field in LISTS}
            self._apply_node(first, 0)
            self._checkpoints[0] = first
        lists = copy(self._checkpoints[made])
        for index in range(made + 1, node + 1):
            self._apply_node(lists, index)
            if index % _CHECKPOINT_INTERVAL == 0:
                self._checkpoints[index] = copy(lists)
        return lists

    def entry(self, field: str, raw: tuple):
        """ A list entry as the dict or string a snapshot shows. """
        strings = self.strings
        if field == 'smt_declarations':
            return strings[raw[0]]
        if field == 'variables':
            variable = {'name': strings[raw[0]], 'value': strings[raw[1]],
                        'kind': strings[raw[2]]}
            if len(raw) > 3:
                variable['smt'] = strings[raw[3]]
                variable['smt_signed'] = bool(raw[4])
            return variable
        return {'symbol': strings[raw[0]], 'range': strings[raw[1]]}

    def assertion(self, raw: tuple) -> str:
        """ The SMT-LIB assertion of a raw path condition entry. """
        return self.strings[raw[2]]

    def snapshot(self, node: int) -> dict:
        """ The whole Constraint Snapshot of `node`, with the keys a version 1
        step had. """
        lists = self._raw(node)
        snapshot = {field: [self.entry(field, raw) for raw in lists[field]]
                    for field in LISTS
                    if field != 'smt_declarations' or self.smtlib}
        if self.smtlib:
            snapshot['smt_assertions'] = \
                [self.assertion(raw) for raw in lists['path_condition']]
        if 'cfg_position' in self.nodes[node]:
            snapshot['cfg_position'] = self.nodes[node]['cfg_position']
        return snapshot

    def lifetimes(self, field: str) -> list[Lifetime]:
        """ Every entry the list ever holds, with the nodes it is in, in one
        pass and without a copy of any node's list. Entries stay raw; turn
        them into values with `entry` and `assertion`. """
        result = []
        live: list = []
        starts: list[int] = []
        end = len(self.nodes)
        for index, node in enumerate(self.nodes):
            delta = node.get(field)
            if not delta:
                continue
            for operation in delta.get('replace', ()):
                position = operation[0]
                result.append(Lifetime(live[position], starts[position],
                                       index))
                live[position] = tuple(operation[1:])
                starts[position] = index
            for position in reversed(delta.get('remove', ())):
                result.append(Lifetime(live[position], starts[position],
                                       index))
                del live[position]
                del starts[position]
            for operation in delta.get('insert', ()):
                live.insert(operation[0], tuple(operation[1:]))
                starts.insert(operation[0], index)
        result.extend(Lifetime(entry, start, end)
                      for entry, start in zip(live, starts))
        return result

    def cfgs(self) -> list[dict]:
        """ The control flow graphs, with blocks and elements as dicts. """
        return [_expand_cfg(cfg) for cfg in self.trace.get('cfgs', [])]


def read_graphs(graphs: list) -> list:
    """ The plist's `PLIST_GRAPHS` with their blocks as lists, for
    `resolve_cfgs`. CodeChecker writes the blocks as JSON text, which takes a
    third of the bytes that plist arrays of their elements take; Clang
    writes the arrays. A body whose blocks do not read is None, and so is a
    trace that names it. """
    read = []
    for graph in graphs:
        blocks = graph.get('blocks') if isinstance(graph, dict) else None
        if isinstance(blocks, str):
            try:
                blocks = json.loads(blocks)
            except ValueError:
                blocks = None
        read.append({**graph, 'blocks': blocks}
                    if isinstance(blocks, list) else None)
    return read


def resolve_cfgs(trace: dict, graphs: list) -> dict:
    """ The trace with each control flow graph whole: the body it names in
    the plist's `graphs`, as `read_graphs` gives them, beside the part of it
    the path took. Bodies are shared between the traces of a file rather
    than copied. A graph that is whole already, as Clang wrote them before
    bodies were kept per file, is kept. """
    if not trace.get('cfgs'):
        return trace
    cfgs = []
    for cfg in trace['cfgs']:
        if 'graph' not in cfg:
            cfgs.append(cfg)
            continue
        index = cfg['graph']
        if not isinstance(index, int) or not 0 <= index < len(graphs):
            raise MalformedTrace(
                f"control flow graph {index} outside a table of {len(graphs)}")
        if graphs[index] is None:
            raise MalformedTrace(f"control flow graph {index} does not read")
        try:
            body = {key: graphs[index][key] for key in _GRAPH_BODY}
        except (KeyError, TypeError) as ex:
            raise MalformedTrace(
                f"control flow graph {index} without {ex}") from ex
        cfgs.append({**body, **{key: value for key, value in cfg.items()
                                if key != 'graph'}})
    return {**trace, 'cfgs': cfgs}


class GraphTable:
    """ The control flow graph bodies of one plist file being written, each
    once, for `PLIST_GRAPHS`, with their blocks as JSON text. """

    def __init__(self):
        self.graphs: list[dict] = []
        self._by_content: dict[bytes, int] = {}
        # Graphs resolved from one body share its blocks, so most are found
        # without serializing them. The blocks are kept so that their id
        # stays theirs.
        self._by_object: dict[tuple, tuple[object, int]] = {}

    def share(self, cfg: dict) -> dict:
        """ The graph as a trace in the file names it. """
        body = {key: cfg[key] for key in _GRAPH_BODY}
        identity = (id(body['blocks']), body['function'], body['entry'],
                    body['exit'])
        known = self._by_object.get(identity)
        if known is None:
            blocks = json.dumps(body['blocks'], ensure_ascii=False,
                                separators=(',', ':'))
            content = hashlib.sha256(json.dumps(
                [body['function'], body['entry'], body['exit'], blocks])
                .encode()).digest()
            index = self._by_content.setdefault(content, len(self.graphs))
            if index == len(self.graphs):
                self.graphs.append({**body, 'blocks': blocks})
            known = (body['blocks'], index)
            self._by_object[identity] = known
        return {'graph': known[1], **{key: value for key, value in cfg.items()
                                      if key not in _GRAPH_BODY}}


def _expand_cfg(cfg: dict) -> dict:
    blocks = []
    for block_id, terminator, successors, elements in cfg['blocks']:
        block = {'id': block_id, 'elements': [], 'successors': successors}
        for text, line, parent in elements:
            element = {'text': text}
            if line:
                element['line'] = line
            if parent >= 0:
                element['parent'] = parent
            block['elements'].append(element)
        if terminator:
            block['terminator'] = terminator
        blocks.append(block)
    edges = cfg['path_edges']
    return {**cfg, 'blocks': blocks,
            'path_edges': [{'from': edges[i], 'to': edges[i + 1]}
                           for i in range(0, len(edges), 2)]}


def _diff(previous: list, following: list) -> dict:
    """ The delta from one raw list to the next, as Clang writes it: entries
    are matched by their first value, kept entries stay in order, and the
    whole list is written again when that does not hold. """
    delta: dict = {'replace': [], 'remove': [], 'insert': []}
    by_key = {entry[0]: index for index, entry in enumerate(following)}
    kept = [False] * len(following)
    ordered = len(by_key) == len(following)
    last = -1
    for index, entry in enumerate(previous):
        if not ordered:
            break
        match = by_key.get(entry[0])
        if match is None or kept[match]:
            delta['remove'].append(index)
            continue
        if match < last:
            ordered = False
            break
        last = match
        kept[match] = True
        if following[match] != entry:
            delta['replace'].append([index, *following[match]])
    if not ordered:
        delta = {'replace': [], 'remove': list(range(len(previous))),
                 'insert': []}
        kept = [False] * len(following)
    delta['insert'] = [[index, *entry] for index, entry in enumerate(following)
                       if not kept[index]]
    return {key: value for key, value in delta.items() if value}


def encode(nodes: list[dict], steps: list[dict],
           symbols: dict[str, dict] | None = None,
           expressions: dict[str, dict] | None = None) -> dict:
    """
    A version 2 trace from whole snapshots, for tools and tests that make
    traces. Each node is a dict of the snapshot keys a version 1 step had
    (`symbol_ranges`, `variables`, `path_condition`, and with SMT-LIB
    `smt_declarations` and `smt_assertions`) plus any node keys to keep, such
    as `kind` or `cfg_position`. Steps are kept as they are and name their
    node by `exploded_node`. `symbols` is a symbol table as
    `ConstraintTrace.symbols` reads one, written in the order of its dumps,
    and `expressions` the same for `ConstraintTrace.expressions`.
    """
    strings: list[str] = []
    index: dict[str, int] = {}

    def intern(string: str) -> int:
        if string not in index:
            index[string] = len(strings)
            strings.append(string)
        return index[string]

    smtlib = any('smt_assertions' in node for node in nodes)
    previous: dict[str, list] = {field: [] for field in LISTS}
    encoded_nodes = []
    for node in nodes:
        lists: dict[str, list] = {
            'symbol_ranges': [(intern(r['symbol']), intern(r['range']))
                              for r in node.get('symbol_ranges', [])],
            'variables': [
                (intern(v['name']), intern(v['value']), intern(v['kind'])) +
                ((intern(v['smt']), int(v['smt_signed'])) if 'smt' in v
                 else ())
                for v in node.get('variables', [])],
            'path_condition': [
                (intern(c['symbol']), intern(c['range'])) +
                ((intern(a),) if smtlib else ())
                for c, a in zip(node.get('path_condition', []),
                                node.get('smt_assertions') or
                                [None] * len(node.get('path_condition', [])))],
            'smt_declarations': [(intern(d),)
                                 for d in node.get('smt_declarations', [])]}
        encoded = {key: value for key, value in node.items()
                   if key not in LISTS and key != 'smt_assertions'}
        for field in LISTS:
            delta = _diff(previous[field], lists[field])
            if delta:
                encoded[field] = delta
        previous = lists
        encoded_nodes.append(encoded)

    trace = {'version': VERSION, 'steps': steps,
             'exploded_nodes': encoded_nodes, 'strings': strings}
    if symbols:
        table = []
        for dump in sorted(symbols):
            symbol = symbols[dump]
            entry = {'symbol': intern(dump), 'kind': symbol['kind']}
            entry.update({key: intern(symbol[key])
                          for key in _SYMBOL_STRINGS if key in symbol})
            if symbol.get('pointer'):
                entry['pointer'] = True
            if 'smt' in symbol:
                entry['smt'] = intern(symbol['smt'])
                entry['smt_signed'] = bool(symbol['smt_signed'])
            if 'origin' in symbol:
                entry['origin'] = {
                    key: intern(value) if key in _ORIGIN_STRINGS else value
                    for key, value in symbol['origin'].items()}
            table.append(entry)
        trace['symbols'] = table
    if expressions:
        typed = []
        for dump in sorted(expressions):
            entry = {'symbol': intern(dump)}
            if 'type' in expressions[dump]:
                entry['type'] = intern(expressions[dump]['type'])
            if expressions[dump].get('pointer'):
                entry['pointer'] = True
            typed.append(entry)
        trace['expressions'] = typed
    if smtlib:
        trace['smtlib'] = True
    return trace
