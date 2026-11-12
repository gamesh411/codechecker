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
        except (KeyError, IndexError, TypeError) as ex:
            raise MalformedTrace(f"constraint trace does not decode: {ex}") \
                from ex

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


def resolve_cfgs(trace: dict, graphs: list) -> dict:
    """ The trace with each control flow graph whole: the body it names in
    the plist's `graphs`, beside the part of it the path took. Bodies are
    shared between the traces of a file rather than copied. A graph that is
    whole already, as Clang wrote them before bodies were kept per file, is
    kept. """
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
    once, for `PLIST_GRAPHS`. """

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
            content = hashlib.sha256(json.dumps(
                [body[key] for key in _GRAPH_BODY]).encode()).digest()
            index = self._by_content.setdefault(content, len(self.graphs))
            if index == len(self.graphs):
                self.graphs.append(body)
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


def encode(nodes: list[dict], steps: list[dict]) -> dict:
    """
    A version 2 trace from whole snapshots, for tools and tests that make
    traces. Each node is a dict of the snapshot keys a version 1 step had
    (`symbol_ranges`, `variables`, `path_condition`, and with SMT-LIB
    `smt_declarations` and `smt_assertions`) plus any node keys to keep, such
    as `kind` or `cfg_position`. Steps are kept as they are and name their
    node by `exploded_node`.
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
    if smtlib:
        trace['smtlib'] = True
    return trace
