// Reading a stored constraint trace, the record of what the analyzer knew at
// each Path Step.
//
// A version 2 trace keeps one Constraint Snapshot per analyzer node, in
// `exploded_nodes`, and a step names its node by `exploded_node`. Each node's
// lists are written as the change from the node before it, as indices into
// the trace's `strings`, so the trace grows with the Bug Path, not with its
// square. This module is the one place in the UI that turns that back into
// snapshots. They are built only when a step is shown, and kept once built.

export const TRACE_VERSION = 2;

// The snapshot lists, in the order a node lists their changes.
const LISTS = [
  "symbol_ranges", "variables", "path_condition", "smt_declarations"
];

// What two steps on one line are compared by, to show them as one choice.
const SHOWN_LISTS = [ "symbol_ranges", "variables", "path_condition" ];

// Whole raw lists kept on the way, so that reading any node replays at most
// this many deltas.
const CHECKPOINT_INTERVAL = 64;

function emptyLists() {
  return Object.fromEntries(LISTS.map(field => [ field, [] ]));
}

function copyLists(lists) {
  return Object.fromEntries(LISTS.map(field => [ field, [ ...lists[field] ] ]));
}

// Turns the list into the next node's, in place: replacements and removals at
// positions in the list before, then insertions at positions in the list
// after.
function applyDelta(entries, delta) {
  (delta.replace ?? []).forEach(operation => {
    entries[operation[0]] = operation.slice(1);
  });
  const _removed = delta.remove ?? [];
  for (let _i = _removed.length - 1; _i >= 0; --_i) {
    entries.splice(_removed[_i], 1);
  }
  (delta.insert ?? []).forEach(operation => {
    entries.splice(operation[0], 0, operation.slice(1));
  });
}

function sameEntries(a, b) {
  return a.length === b.length && a.every((entry, index) =>
    entry.length === b[index].length &&
    entry.every((value, at) => value === b[index][at]));
}

// FNV-1a over an entry's values, so that lists can be compared by a sum
// first and entry by entry only when the sums agree.
function entryHash(field, entry) {
  let _hash = 2166136261 ^ field;
  entry.forEach(value => {
    _hash = Math.imul(_hash ^ value, 16777619);
  });
  return _hash >>> 0;
}

export class ConstraintTrace {
  // Throws for a trace of another version or without the tables it needs.
  constructor(trace) {
    if (trace?.version !== TRACE_VERSION) {
      throw new Error(`Constraint trace version ${trace?.version} is not ` +
        `${TRACE_VERSION}`);
    }
    if (!Array.isArray(trace.steps) || !Array.isArray(trace.strings) ||
        !Array.isArray(trace.exploded_nodes)) {
      throw new Error("Constraint trace without its steps, strings or nodes");
    }
    this.trace = trace;
    this.steps = trace.steps;
    this.nodes = trace.exploded_nodes;
    this.frames = trace.stack_frames ?? [];
    this.strings = trace.strings;
    this.smtlib = !!trace.smtlib;
    this._checkpoints = new Map();
    this._snapshots = new Map();
    this._cfgs = null;
    this._hashes = null;
    this._symbolNameLog = null;
  }

  get cfgs() {
    if (!this._cfgs) {
      this._cfgs = (this.trace.cfgs ?? []).map(expandCfg);
    }
    return this._cfgs;
  }

  // Where the node is in its function's control flow graph, if anywhere.
  cfgPosition(node) {
    return this.nodes[node]?.cfg_position;
  }

  applyNode(lists, index) {
    const _node = this.nodes[index];
    LISTS.forEach(field => {
      if (_node[field]) applyDelta(lists[field], _node[field]);
    });
  }

  // The raw lists of a node, replayed from the nearest checkpoint, as a copy
  // the caller owns.
  rawLists(node) {
    const _start = node - node % CHECKPOINT_INTERVAL;
    let _made = _start;
    while (_made > 0 && !this._checkpoints.has(_made)) {
      _made -= CHECKPOINT_INTERVAL;
    }
    if (!this._checkpoints.has(_made)) {
      const _first = emptyLists();
      this.applyNode(_first, 0);
      this._checkpoints.set(0, _first);
    }
    const _lists = copyLists(this._checkpoints.get(_made));
    for (let _index = _made + 1; _index <= node; ++_index) {
      this.applyNode(_lists, _index);
      if (_index % CHECKPOINT_INTERVAL === 0) {
        this._checkpoints.set(_index, copyLists(_lists));
      }
    }
    return _lists;
  }

  entry(field, raw) {
    const _s = this.strings;
    if (field === "smt_declarations") return _s[raw[0]];
    if (field === "variables") {
      const _variable = {
        name: _s[raw[0]], value: _s[raw[1]], kind: _s[raw[2]]
      };
      if (raw.length > 3) {
        _variable.smt = _s[raw[3]];
        _variable.smt_signed = !!raw[4];
      }
      return _variable;
    }
    return { symbol: _s[raw[0]], range: _s[raw[1]] };
  }

  // The whole Constraint Snapshot of a node, with the keys a step showed
  // before version 2. The same object every time, and never changed.
  snapshot(node) {
    let _snapshot = this._snapshots.get(node);
    if (_snapshot) return _snapshot;

    const _lists = this.rawLists(node);
    _snapshot = Object.fromEntries(LISTS
      .filter(field => field !== "smt_declarations" || this.smtlib)
      .map(field => [
        field, _lists[field].map(raw => this.entry(field, raw))
      ]));
    if (this.smtlib) {
      _snapshot.smt_assertions =
        _lists.path_condition.map(raw => this.strings[raw[2]]);
    }
    const _position = this.cfgPosition(node);
    if (_position) _snapshot.cfg_position = _position;
    _snapshot.exploded_node = node;
    Object.freeze(_snapshot);
    this._snapshots.set(node, _snapshot);
    return _snapshot;
  }

  // Whether two nodes show the same ranges, variables and path condition.
  // Distinct nodes often do (the edges into a line and its event), and this
  // tells without building either snapshot.
  sameSnapshot(a, b) {
    if (a === b) return true;
    const _hashes = this.snapshotHashes();
    if (_hashes[a] !== _hashes[b]) return false;
    const _a = this.rawLists(a);
    const _b = this.rawLists(b);
    return SHOWN_LISTS.every(field => sameEntries(_a[field], _b[field]));
  }

  // A sum of entry hashes per node over the shown lists, kept up to date
  // along the deltas in one pass.
  snapshotHashes() {
    if (this._hashes) return this._hashes;

    const _lists = emptyLists();
    const _hashes = new Array(this.nodes.length);
    let _sum = 0;
    this.nodes.forEach((node, index) => {
      SHOWN_LISTS.forEach((field, fieldIndex) => {
        if (!node[field]) return;
        const _entries = _lists[field];
        const _hash = entry => entryHash(fieldIndex, entry);
        (node[field].replace ?? []).forEach(operation =>
          _sum -= _hash(_entries[operation[0]]) - _hash(operation.slice(1)));
        (node[field].remove ?? []).forEach(position =>
          _sum -= _hash(_entries[position]));
        (node[field].insert ?? []).forEach(operation =>
          _sum += _hash(operation.slice(1)));
        _sum >>>= 0;
        applyDelta(_entries, node[field]);
      });
      _hashes[index] = _sum;
    });
    this._hashes = _hashes;
    return _hashes;
  }

  // For each symbol, the name of the variable that last held it among the
  // steps up to `order`, so that the path condition can name a symbol whose
  // variable is gone by then. Of several variables holding it at one step,
  // the last by name wins.
  symbolNames(order) {
    const _names = new Map();
    for (const [ stepOrder, symbol, name ] of this.symbolNameLog()) {
      if (stepOrder > order) break;
      _names.set(symbol, name);
    }
    return _names;
  }

  // The changes of that map step by step in Bug Path order, built once:
  // a step only changes it for the symbols its variables hold, and only where
  // the name differs from what the map held.
  symbolNameLog() {
    if (this._symbolNameLog) return this._symbolNameLog;

    const _symbolKind = this.strings.indexOf("symbol");
    const _current = new Map();
    const _log = [];
    let _previousNode = null;
    [ ...this.steps ].sort((a, b) => a.order - b.order).forEach(step => {
      // Steps on one node set the same names, so only the first counts.
      if (step.exploded_node === _previousNode) return;
      _previousNode = step.exploded_node;
      const _held = new Map();
      this.rawLists(step.exploded_node).variables.forEach(raw => {
        if (raw[2] === _symbolKind) _held.set(raw[1], raw[0]);
      });
      _held.forEach((name, symbol) => {
        if (_current.get(symbol) === name) return;
        _current.set(symbol, name);
        _log.push([ step.order, this.strings[symbol], this.strings[name] ]);
      });
    });
    this._symbolNameLog = _log;
    return _log;
  }
}

// A CFG with blocks and elements as objects, as the graph view reads them.
function expandCfg(cfg) {
  const _edges = cfg.path_edges ?? [];
  const _pathEdges = [];
  for (let _i = 0; _i + 1 < _edges.length; _i += 2) {
    _pathEdges.push({ from: _edges[_i], to: _edges[_i + 1] });
  }
  return {
    ...cfg,
    blocks: cfg.blocks.map(([ id, terminator, successors, elements ]) => {
      const _block = {
        id,
        successors,
        elements: elements.map(([ text, line, parent ]) => {
          const _element = { text };
          if (line) _element.line = line;
          if (parent >= 0) _element.parent = parent;
          return _element;
        })
      };
      if (terminator) _block.terminator = terminator;
      return _block;
    }),
    path_edges: _pathEdges
  };
}

// The trace of a report's details: a ConstraintTrace for the current version,
// `{ version }` for one recorded in an older format, and null when there is
// none or it is malformed.
export function readConstraintTrace(json) {
  if (!json) return null;

  let _trace;
  try {
    _trace = JSON.parse(json);
  } catch (error) {
    console.warn("Malformed constraint trace: ", error);
    return null;
  }
  if (_trace?.version !== TRACE_VERSION) {
    return { version: _trace?.version ?? null };
  }
  try {
    return new ConstraintTrace(_trace);
  } catch (error) {
    console.warn("Malformed constraint trace: ", error);
    return null;
  }
}
