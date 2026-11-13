// Reading a stored constraint trace, the record of what the analyzer knew at
// each Path Step.
//
// A version 2 trace keeps one Constraint Snapshot per analyzer node, in
// `exploded_nodes`, and a step names its node by `exploded_node`. Each node's
// lists are written as the change from the node before it, as indices into
// the trace's `strings`, so the trace grows with the Bug Path, not with its
// square. This module is the one place in the UI that turns that back into
// snapshots. They are built only when a step is shown, and kept once built.
//
// A trace may also have a table of the atomic symbols its snapshots mention,
// `symbols`, keyed by each symbol's dump, with the origin Clang recorded for a
// conjured symbol. A trace from a Clang before the table has no entries.
//
// A node's `variables` are those of its own stack frame. A node inside an
// inlined call also has `caller_variables`, the variables of every frame above
// it, innermost first, each with its frame as an index into `stack_frames`.

export const TRACE_VERSION = 2;

// The snapshot lists, in the order a node lists their changes.
const LISTS = [
  "symbol_ranges", "variables", "path_condition", "smt_declarations",
  "caller_variables"
];

// What two steps on one line are compared by, to show them as one choice.
const SHOWN_LISTS = [
  "symbol_ranges", "variables", "path_condition", "caller_variables"
];

// The keys of a symbol's origin, and of the symbol itself, that name a
// string.
const ORIGIN_STRINGS = [ "statement", "callee", "function", "region" ];
const SYMBOL_STRINGS = [ "parent", "region", "member", "type", "name" ];

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

// What a control step is called, from the number of the last event before it
// (0 for none): the Bug Path draws it as an arrow between lines, and a reader
// follows it as the edge the analyzer took.
export function controlStepLabel(afterEvent) {
  return afterEvent ? `Edge after Step ${afterEvent}` : "Edge before Step 1";
}

// The groups of "This path requires", in the order they are shown: what the
// function was given comes first.
export const REQUIREMENT_GROUPS = [
  { key: "entry", title: "Entry inputs",
    hint: "What the function was called with: its parameters, globals and " +
      "the fields of this, and what they point to." },
  { key: "call", title: "Call results",
    hint: "What calls the analyzer did not see inside returned." },
  { key: "replaced", title: "Replaced values",
    hint: "Memory that a call or a loop may have changed." },
  { key: "other", title: "Other",
    hint: "Values the analyzer did not say the origin of." }
];

// The atoms of a symbolic expression read by parseSymbolExpression, as dumps,
// or null for one that was not read.
function expressionAtoms(node) {
  if (!node) return null;
  if (node.atom !== undefined) return [ node.atom ];
  if (node.integer !== undefined) return [];
  if (node.operand) return expressionAtoms(node.operand);
  const _left = expressionAtoms(node.left);
  const _right = expressionAtoms(node.right);
  return _left && _right && [ ..._left, ..._right ];
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
    this._ordered = null;
    this._changes = new Map();
    this._silentChanges = null;
    this._requirements = null;
    this._symbols = null;
    this._expressions = null;
    this._calls = null;
    this._memory = null;
    this._whatIfNames = new Map();
    this._whatIfNamesAt = new Map();
  }

  // The symbol table as a Map from symbol dump to its entry: `kind`, `smt`
  // and `smt_signed` when the symbol has an SMT-LIB term, and for a conjured
  // symbol `origin`, with `reason` and `function` and, when Clang knows them,
  // `statement`, `location`, `callee` and `frame` (into `stack_frames`), and
  // for memory a call or a loop replaced, `region` or `globals`, and `loop`.
  // A derived symbol has its `parent`'s dump and its `region`'s access path,
  // or else `member`, the end of that path after memory Clang cannot
  // describe, an extent the `region` whose size it is, and a region value the
  // `name` of its region's access path, each when Clang can describe it, or
  // `in_call` when the memory is a call's own, which has no name. A symbol has
  // its `type`, the canonical type's name, and `pointer` when the analyzer
  // treats it as an address; a trace from a Clang before those has neither.
  get symbols() {
    if (this._symbols) return this._symbols;

    const _s = this.strings;
    this._symbols = new Map((this.trace.symbols ?? []).map(entry => {
      const _symbol = { kind: entry.kind };
      SYMBOL_STRINGS.forEach(key => {
        if (entry[key] !== undefined) _symbol[key] = _s[entry[key]];
      });
      if (entry.pointer) _symbol.pointer = true;
      if (entry.in_call) _symbol.in_call = true;
      if (entry.smt !== undefined) {
        _symbol.smt = _s[entry.smt];
        _symbol.smt_signed = !!entry.smt_signed;
      }
      if (entry.origin) {
        const _origin = { ...entry.origin };
        ORIGIN_STRINGS.forEach(key => {
          if (_origin[key] !== undefined) _origin[key] = _s[_origin[key]];
        });
        _symbol.origin = _origin;
      }
      return [ _s[entry.symbol], _symbol ];
    }));
    return this._symbols;
  }

  // The entry of an atomic symbol, or undefined for a symbolic expression or
  // a trace without the table.
  symbol(dump) {
    return this.symbols.get(dump);
  }

  // The types of the symbolic expressions the snapshots constrain or the
  // variables hold, as a Map from dump to `{ type, pointer }` in the shape a
  // symbol's entry has them. An expression has no entry among the symbols.
  get expressions() {
    if (this._expressions) return this._expressions;

    const _s = this.strings;
    this._expressions = new Map((this.trace.expressions ?? []).map(entry => {
      const _expression = {};
      if (entry.type !== undefined) _expression.type = _s[entry.type];
      if (entry.pointer) _expression.pointer = true;
      return [ _s[entry.symbol], _expression ];
    }));
    return this._expressions;
  }

  // What the trace says of the type of a symbol or an expression, as
  // symbolType gives it, or undefined when it says nothing.
  typeOf(dump) {
    return symbolType(this.symbols.get(dump) ?? this.expressions.get(dump));
  }

  // The other symbols whose origin is a call with the same statement text as
  // this symbol's: each { symbol, origin }, in the order of the table. The
  // analyzer made them from different calls, or the same call at different
  // times, and does not assume the calls returned the same value. Only a
  // symbol the Bug Path mentions has an entry. A statement Clang cut is not
  // compared, since two longer ones may share its start.
  sameCall(dump) {
    if (!this._calls) {
      this._calls = new Map();
      this.symbols.forEach((entry, symbol) => {
        const _statement = entry.origin?.reason === "call_result"
          ? entry.origin.statement : undefined;
        if (!_statement || _statement.endsWith("...")) return;
        if (!this._calls.has(_statement)) this._calls.set(_statement, []);
        this._calls.get(_statement).push({ symbol, origin: entry.origin });
      });
    }
    const _own = this.symbols.get(dump)?.origin;
    if (_own?.reason !== "call_result") return [];
    return (this._calls.get(_own.statement) ?? []).filter(call =>
      call.symbol !== dump);
  }

  // The `order` of the Path Step nearest to a source location: of the steps
  // whose node is in its file, those in `frame` if there are any, then the
  // closest line, then the closest column, which tells two calls on one line
  // apart, then an event over the arrow into it, then the earliest on the Bug
  // Path. Null when no step is in that file.
  nearestStep(location, frame) {
    if (!location) return null;

    const _inFile = this.orderedSteps().filter(step =>
      this.nodes[step.exploded_node]?.location?.file === location.file);
    const _inFrame = _inFile.filter(step =>
      frame !== undefined && this.nodes[step.exploded_node].frame === frame);
    let _best = null;
    let _distance = null;
    (_inFrame.length ? _inFrame : _inFile).forEach(step => {
      const _at = this.nodes[step.exploded_node].location;
      const _d = [
        Math.abs(_at.line - location.line),
        Math.abs((_at.col ?? 0) - (location.col ?? 0)),
        step.kind === "event" ? 0 : 1
      ];
      const _differs = _d.findIndex((value, at) => value !== _distance?.[at]);
      if (!_distance || _d[_differs] < _distance[_differs]) {
        _best = step.order;
        _distance = _d;
      }
    });
    return _best;
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

  // A list entry as a snapshot shows it. A variable is `name`, `value` and
  // `kind`, with `smt` and `smt_signed` when its symbol has a term, and a
  // caller's variable also has its `frame`, an index into `stack_frames`.
  entry(field, raw) {
    const _s = this.strings;
    if (field === "smt_declarations") return _s[raw[0]];
    if (field === "caller_variables") {
      return { frame: raw[0], ...this.entry("variables", raw.slice(1)) };
    }
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
  // before version 2, and `caller_variables`, which is empty in the analyzed
  // function and for a trace from a Clang before the list. The same object
  // every time, and never changed.
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

  // The steps in Bug Path order, which is not the order of the nodes.
  orderedSteps() {
    this._ordered ??= [ ...this.steps ].sort((a, b) => a.order - b.order);
    return this._ordered;
  }

  // What the step at `order` added or narrowed, as a Map from symbol to
  // "added" or "narrowed" for each of the symbol ranges and the path
  // condition. It is the change from the last step before it that shows a
  // different snapshot, since steps on one snapshot (an edge and the event
  // there) are one choice to a reader. The first snapshot has nothing before
  // it to be compared to.
  changedAt(order) {
    let _changes = this._changes.get(order);
    if (_changes) return _changes;

    _changes = { symbol_ranges: new Map(), path_condition: new Map() };
    const _steps = this.orderedSteps();
    const _at = _steps.findIndex(step => step.order === order);
    let _before = _at - 1;
    while (_before >= 0 && this.sameSnapshot(
      _steps[_before].exploded_node, _steps[_at].exploded_node)) {
      --_before;
    }
    if (_at >= 0 && _before >= 0) {
      const _now = this.rawLists(_steps[_at].exploded_node);
      const _then = this.rawLists(_steps[_before].exploded_node);
      Object.keys(_changes).forEach(field =>
        this.diffRanges(_changes[field], _then[field], _now[field]));
    }
    this._changes.set(order, _changes);
    return _changes;
  }

  // Entries of a list of [symbol, range, ...] that `then` does not have, or
  // has with another range. Strings are kept once, so equal indices are equal
  // texts.
  diffRanges(into, then, now) {
    const _ranges = new Map(then.map(raw => [ raw[0], raw[1] ]));
    now.forEach(raw => {
      if (!_ranges.has(raw[0])) {
        into.set(this.strings[raw[0]], "added");
      } else if (_ranges.get(raw[0]) !== raw[1]) {
        into.set(this.strings[raw[0]], "narrowed");
      }
    });
  }

  // The control steps where the analyzer assumed something that the Bug Path
  // has no event for: the node adds to the path condition, and no event is on
  // that node. Each is { order, node, afterEvent }, where afterEvent is the
  // index of the event step before it in the Bug Path, or -1. The path
  // condition only grows along the nodes and a printed path can visit them out
  // of order, so a step adds to it only when its node is later than every node
  // before it and has path condition changes in between.
  silentChanges() {
    if (this._silentChanges) return this._silentChanges;

    const _changed = [ 0 ];
    this.nodes.forEach((node, index) => {
      const _delta = node.path_condition;
      const _any = _delta &&
        ((_delta.replace?.length ?? 0) + (_delta.insert?.length ?? 0)) > 0;
      _changed.push(_changed[index] + (_any ? 1 : 0));
    });
    // _changed[n + 1] counts the nodes up to n that change it.
    const _eventNodes = new Set(this.steps
      .filter(step => step.kind === "event")
      .map(step => step.exploded_node));

    const _silent = [];
    let _furthest = null;
    let _afterEvent = -1;
    this.orderedSteps().forEach(step => {
      const _node = step.exploded_node;
      if (step.kind === "event") {
        _afterEvent = step.index;
      } else if (_furthest !== null && _node > _furthest &&
                 _changed[_node + 1] > _changed[_furthest + 1] &&
                 !_eventNodes.has(_node)) {
        _silent.push(
          { order: step.order, node: _node, afterEvent: _afterEvent });
      }
      if (_furthest === null || _node > _furthest) _furthest = _node;
    });
    this._silentChanges = _silent;
    return _silent;
  }

  // What the Bug Path requires, from the path condition of its last Path
  // Step: { grouped, groups }, where each group is { key, title, hint, rows }.
  // A row is { key, symbol, range, name, dropped, changedAt }, with `name` the
  // variable that held the symbol, `dropped` when the analyzer no longer knows
  // the symbol's range at the last step, and `changedAt` the first Path Step
  // that shows the entry as it is at the end: { order, function, silent,
  // exact }. That is where the analyzer last assumed it, with `silent` when
  // the step is an assumption the Bug Path has no event for, and `exact`
  // unless the analyzer did it at a node of its own, between the step before
  // and this one. The groups are the origins the symbol table tells, in
  // REQUIREMENT_GROUPS order, and only those with rows. A trace without the
  // table is one group with no key, so `grouped` is false. Rows are named
  // ones first, then by name or symbol.
  requirements() {
    if (this._requirements) return this._requirements;

    const _steps = this.orderedSteps();
    const _last = _steps[_steps.length - 1];
    if (!_last) {
      this._requirements = { grouped: false, groups: [] };
      return this._requirements;
    }

    // The last node that inserted or replaced each entry of the path
    // condition, up to the node of the last step.
    const _changeNode = new Map();
    for (let _node = 0; _node <= _last.exploded_node; ++_node) {
      const _delta = this.nodes[_node].path_condition;
      [ ...(_delta?.replace ?? []), ...(_delta?.insert ?? []) ]
        .forEach(operation =>
          _changeNode.set(this.strings[operation[1]], _node));
    }
    // The furthest node among the steps up to each, which only grows, so the
    // first step that shows a node's change is found by search.
    const _reach = [];
    _steps.reduce((furthest, step) => {
      _reach.push(Math.max(furthest, step.exploded_node));
      return _reach[_reach.length - 1];
    }, -1);
    const _silent = new Set(this.silentChanges().map(change => change.order));
    const _changedAt = symbol => {
      let _low = 0;
      let _high = _steps.length - 1;
      const _node = _changeNode.get(symbol) ?? 0;
      while (_low < _high) {
        const _middle = (_low + _high) >> 1;
        if (_reach[_middle] >= _node) _high = _middle;
        else _low = _middle + 1;
      }
      const _step = _steps[_low];
      const _frame = this.nodes[_step.exploded_node].frame;
      return {
        order: _step.order,
        function: this.frames[_frame]?.function ?? null,
        silent: _silent.has(_step.order),
        exact: _step.exploded_node === _node
      };
    };

    const _names = this.symbolNames(_last.order);
    const _grouped = this.symbols.size > 0;
    const _known = new Set(this.snapshot(_last.exploded_node).symbol_ranges
      .map(entry => entry.symbol));
    const _groups = new Map(REQUIREMENT_GROUPS.map(group =>
      [ group.key, { ...group, rows: [] } ]));
    this.snapshot(_last.exploded_node).path_condition.forEach(entry => {
      const _key = _grouped ? this.requirementGroup(entry.symbol) : "other";
      _groups.get(_key).rows.push({
        key: `symbol ${entry.symbol}`,
        symbol: entry.symbol,
        range: entry.range,
        name: _names.get(entry.symbol),
        dropped: !_known.has(entry.symbol),
        changedAt: _changedAt(entry.symbol)
      });
    });
    const _rows = [ ..._groups.values() ].filter(group => group.rows.length);
    _rows.forEach(group => group.rows.sort((a, b) =>
      (a.name === undefined) - (b.name === undefined) ||
      compareNames(a.name ?? a.symbol, b.name ?? b.symbol)));
    this._requirements = {
      grouped: _grouped,
      groups: _grouped ? _rows : _rows.map(group => ({ ...group, key: null }))
    };
    return this._requirements;
  }

  // Where a symbol of the path condition comes from, a key of
  // REQUIREMENT_GROUPS. An atom is told by its entry in the symbol table:
  // memory the function started with is an entry input, the result of a call
  // a call result, memory a call or a loop changed a replaced value, and a
  // derived symbol goes by its parent. An expression goes by its atoms when
  // they agree, and is `other` otherwise.
  requirementGroup(dump, depth = 0) {
    const _entry = this.symbols.get(dump);
    if (!_entry) {
      const _tree = parseSymbolExpression(dump);
      // An atom the table does not have says nothing of where it is from.
      if (!_tree || _tree.atom === dump || depth >= 8) return "other";
      const _atoms = expressionAtoms(_tree);
      const _keys = new Set(_atoms?.map(atom =>
        this.requirementGroup(atom, depth + 1)));
      return _keys.size === 1 ? [ ..._keys ][0] : "other";
    }

    switch (_entry.kind) {
    case "region_value":
      // Clang makes a region value only for memory the function started
      // with, and gives it a name when the memory is a parameter, a global
      // or `this` or what they point to. Memory reached through a call's
      // result has the call's symbol in its dump instead, and memory of a
      // call the function made, such as the callee's `this`, says so.
      if (_entry.in_call) return "other";
      return _entry.name !== undefined || !/\b(conj|derived)_\$/.test(dump)
        ? "entry" : "other";
    case "conjured":
      switch (_entry.origin?.reason) {
      case "call_result": return "call";
      case "invalidation":
      case "loop_widening": return "replaced";
      default: return "other";
      }
    case "derived":
      // A symbol cannot be its own ancestor, but a trace is not trusted.
      return _entry.parent !== undefined && depth < 8
        ? this.requirementGroup(_entry.parent, depth + 1) : "other";
    default:
      return "other";
    }
  }

  // The stack frame at an index of `stack_frames`, as { function, parent,
  // call_site }, or undefined.
  frame(index) {
    return this.frames[index];
  }

  // For each symbol, the name of a variable that holds it at the step at
  // `order`: one of the step's own frame, else one of the innermost caller
  // that has one. A symbol no variable holds there keeps the name of a
  // variable that held it among the steps before, so that the path condition
  // can name a symbol whose variable is gone by then, but only of a variable
  // that held nothing else since: a variable names one value, the last it was
  // seen to hold, so a value it held before it was assigned another, or a
  // copy it held of a value another variable holds now, takes an earlier
  // name of its own or none. The initial value of a variable goes by that
  // variable, whose name is its label, rather than by one that only holds a
  // copy of it, when the step shows both or neither.
  // A name from another frame than the step's reads `function::name`, as a
  // debugger writes it, when a variable of that name in another frame holds
  // something else at the step, or another name the step gives is of that
  // name in another frame, since `n` alone would then read as the wrong one.
  symbolNames(order) {
    // By string index, as the raw lists have them. The names each symbol
    // had, latest last, as { name, frame }, and the value each variable,
    // keyed by name and frame, held last.
    const _history = new Map();
    const _holder = new Map();
    const _variable = (name, frame) => `${name} ${frame}`;
    const _had = (symbol, name, frame) => {
      if (!_history.has(symbol)) _history.set(symbol, []);
      _history.get(symbol).push({ name, frame });
    };
    for (const [ stepOrder, symbol, name, frame ] of this.symbolNameLog()) {
      if (stepOrder > order) break;
      _had(symbol, name, frame);
      _holder.set(_variable(name, frame), symbol);
    }

    // What every variable the step shows holds, by name, and the variables
    // that hold each symbol there, as { name, frame }.
    const _shown = new Map();
    const _holding = new Map();
    const _holds = (symbol, name, frame) => {
      if (!_holding.has(symbol)) _holding.set(symbol, []);
      _holding.get(symbol).push({ name, frame });
    };
    const _step = this.orderedSteps().find(step => step.order === order);
    const _own = _step ? this.nodes[_step.exploded_node].frame : undefined;
    if (_step) {
      const _lists = this.rawLists(_step.exploded_node);
      const _symbolKind = this.strings.indexOf("symbol");
      const _show = (name, value) => {
        if (!_shown.has(name)) _shown.set(name, new Set());
        _shown.get(name).add(value);
      };
      const _ownSymbols = new Set();
      // The log has the own frame's symbols, but a variable that holds a
      // number now holds no symbol it held before.
      _lists.variables.forEach(raw => {
        _show(raw[0], raw[1]);
        _holder.set(_variable(raw[0], _own), raw[1]);
        if (raw[2] === _symbolKind) {
          _ownSymbols.add(raw[1]);
          _holds(raw[1], raw[0], _own);
        }
      });
      // Outermost first, so that the innermost caller's name is the latest.
      for (let _i = _lists.caller_variables.length - 1; _i >= 0; --_i) {
        const [ frame, name, value, kind ] = _lists.caller_variables[_i];
        _show(name, value);
        _holder.set(_variable(name, frame), value);
        if (kind !== _symbolKind) continue;
        if (!_ownSymbols.has(value)) _had(value, name, frame);
        _holds(value, name, frame);
      }
    }

    const _names = new Map();
    _history.forEach((names, symbol) => {
      const _owned = names.filter(({ name, frame }) =>
        _holder.get(_variable(name, frame)) === symbol);
      const _entry = this.symbols.get(this.strings[symbol]);
      const _memory = _entry?.kind === "region_value" ? _entry.name : null;
      const _holders = _holding.get(symbol) ?? [];
      const _isShown = named => _holders.some(holder =>
        holder.name === named.name && holder.frame === named.frame);
      const _isOwn = ({ name }) => this.strings[name] === _memory;
      const _named = _holders.find(_isOwn) ?? _owned.findLast(_isShown) ??
        _owned.findLast(_isOwn) ?? _owned[_owned.length - 1];
      if (_named) _names.set(symbol, { ..._named });
    });
    const _frames = new Map();
    _names.forEach(({ name, frame }) => {
      if (!_frames.has(name)) _frames.set(name, new Set());
      _frames.get(name).add(frame);
    });
    _names.forEach((named, symbol) => {
      const _held = _shown.get(named.name);
      const _clash = named.frame !== _own &&
        (_frames.get(named.name).size > 1 ||
          _held && [ ..._held ].some(value => value !== symbol));
      const _function = functionName(this.frames[named.frame]?.function);
      named.qualified = _clash && _function ? _function : null;
    });

    const _result = new Map();
    _names.forEach(({ name, qualified }, symbol) => {
      const _name = this.strings[name];
      _result.set(this.strings[symbol],
        qualified ? `${qualified}::${_name}` : _name);
    });
    return _result;
  }

  // The changes of the names the steps' own frames give, step by step in Bug
  // Path order, as [ order, symbol, name, frame ] with string indices, built
  // once: a step only changes them for the symbols its variables hold, and
  // only where no variable that already named the symbol in that frame still
  // holds it. A copy does not take the name from the variable it was copied
  // from. Variables that start holding a symbol at one step are all logged,
  // the last by name last.
  symbolNameLog() {
    if (this._symbolNameLog) return this._symbolNameLog;

    const _symbolKind = this.strings.indexOf("symbol");
    const _current = new Map();
    const _log = [];
    let _previousNode = null;
    this.orderedSteps().forEach(step => {
      // Steps on one node set the same names, so only the first counts.
      if (step.exploded_node === _previousNode) return;
      _previousNode = step.exploded_node;
      const _frame = this.nodes[step.exploded_node].frame;
      const _held = new Map();
      this.rawLists(step.exploded_node).variables.forEach(raw => {
        if (raw[2] !== _symbolKind) return;
        if (!_held.has(raw[1])) _held.set(raw[1], []);
        _held.get(raw[1]).push(raw[0]);
      });
      _held.forEach((names, symbol) => {
        const _was = _current.get(symbol);
        if (_was && _was.frame === _frame && names.includes(_was.name)) return;
        _current.set(symbol,
          { name: names[names.length - 1], frame: _frame });
        names.forEach(name =>
          _log.push([ step.order, symbol, name, _frame ]));
      });
    });
    this._symbolNameLog = _log;
    return _log;
  }

  // Every name a What-If Constraint at the step at `order` can be written on,
  // sorted, each as { name, values }, the values it stands for there as a
  // variable entry has them: a name that stands for one value the check can
  // decide, or with `throughout`, a name the step shows that stands for values
  // the check can decide and, wherever variables hold it, for one at a time.
  // This is the server's resolution in what_if.py, read the same way: a name
  // is a variable of the step's frame or a calling frame, also written
  // `function::name`, or else memory the symbol table names, with a solver
  // term the step declares, and a variable's name hides memory of that name.
  // The jest tests compare the two on every fixture.
  whatIfNames(order, throughout = false) {
    const _key = `${order} ${throughout}`;
    if (this._whatIfNames.has(_key)) return this._whatIfNames.get(_key);

    const _steps = this.orderedSteps();
    const _at = _steps.findIndex(step => step.order === order);
    const _names = [];
    if (this.smtlib && _at >= 0) {
      this.whatIfNamesAt(_steps[_at].exploded_node).forEach((held, name) => {
        if (!WHAT_IF_NAME.test(name)) return;
        const _values = throughout
          ? this.everyWhatIfValue(name) : this.oneWhatIfValue(_at, name);
        if (!_values || !_values.every(value => value.kind !== "symbol" ||
            value.smt !== undefined)) return;
        _names.push({
          name,
          values: [ ...new Map(held.map(one =>
            [ valueKey(one.value), one.value ])).values() ]
        });
      });
    }
    _names.sort((a, b) => compareText(a.name, b.name));
    this._whatIfNames.set(_key, _names);
    return _names;
  }

  // The one value a name stands for at the step at index `at` of the ordered
  // steps, as a one-element list, or null when it stands for none or more.
  oneWhatIfValue(at, name) {
    const _values = this.whatIfValues(at, name);
    return _values.length === 1 ? [ _values[0].value ] : null;
  }

  // Every value a name stands for along the path, or null when it stands for
  // none, or at some step for variables of several frames holding different
  // values. Memory can hold several, one after the other.
  everyWhatIfValue(name) {
    const _found = new Map();
    const _steps = this.orderedSteps();
    for (let _at = 0; _at < _steps.length; ++_at) {
      const _values = this.whatIfValues(_at, name);
      if (_values.filter(held => !held.memory).length > 1) return null;
      _values.forEach(held => {
        if (!_found.has(valueKey(held.value))) {
          _found.set(valueKey(held.value), held.value);
        }
      });
    }
    return _found.size ? [ ..._found.values() ] : null;
  }

  // What holds the values a name stands for at the step at index `at`, one
  // for each value.
  whatIfValues(at, name) {
    const _distinct = new Map();
    const _node = this.orderedSteps()[at].exploded_node;
    (this.whatIfNamesAt(_node).get(name) ?? []).forEach(held => {
      if (!_distinct.has(valueKey(held.value))) {
        _distinct.set(valueKey(held.value), held);
      }
    });
    return [ ..._distinct.values() ];
  }

  // Every name of a node, as a Map to what holds a value under it there:
  // each { value, frame, memory }.
  whatIfNamesAt(node) {
    if (this._whatIfNamesAt.has(node)) return this._whatIfNamesAt.get(node);

    const _names = new Map();
    const _add = (name, held) => {
      if (!_names.has(name)) _names.set(name, []);
      _names.get(name).push(held);
    };
    const _own = this.nodes[node].frame ?? null;
    const _lists = this.rawLists(node);
    const _variables = [
      ..._lists.variables.map(raw =>
        [ this.entry("variables", raw), _own ]),
      ..._lists.caller_variables.map(raw => {
        const _variable = this.entry("caller_variables", raw);
        return [ _variable, _variable.frame ];
      })
    ];
    _variables.forEach(([ variable, frame ]) => {
      const _held = { value: variable, frame, memory: false };
      _add(variable.name, _held);
      const _function = frame === null || frame === undefined
        ? "" : functionName(this.frames[frame]?.function);
      if (_function) _add(`${_function}::${variable.name}`, _held);
    });

    const _byVariable = new Set(_names.keys());
    const _declared = new Set(_lists.smt_declarations.map(raw =>
      DECLARED.exec(this.strings[raw[0]])?.[1]));
    this.whatIfMemory().forEach(memory => {
      if (!_byVariable.has(memory.name) && _declared.has(memory.value.smt)) {
        _add(memory.name, { value: memory.value, frame: null, memory: true });
      }
    });
    this._whatIfNamesAt.set(node, _names);
    return _names;
  }

  // The memory the symbol table names, with a solver term: a region value's
  // access path, and that of a part of a value a call or a loop replaced, as
  // { name, value }, in the order the path meets them.
  whatIfMemory() {
    if (this._memory) return this._memory;

    const _first = new Map();
    this.nodes.forEach((node, index) => {
      const _delta = node.smt_declarations;
      [ ...(_delta?.replace ?? []), ...(_delta?.insert ?? []) ].forEach(
        operation => {
          const _name = DECLARED.exec(this.strings[operation[1]])?.[1];
          if (_name !== undefined && !_first.has(_name)) {
            _first.set(_name, index);
          }
        });
    });
    const _memory = [];
    this.symbols.forEach((entry, dump) => {
      const _name = entry.kind === "region_value" ? entry.name
        : entry.kind === "derived" ? entry.region : undefined;
      if (!_name || entry.smt === undefined) return;
      _memory.push({ name: _name, value: {
        name: _name, value: dump, kind: "symbol", smt: entry.smt,
        smt_signed: entry.smt_signed
      } });
    });
    const _at = memory => _first.get(memory.value.smt) ?? this.nodes.length;
    _memory.sort((a, b) => _at(a) - _at(b) ||
      compareText(a.value.value, b.value.value));
    this._memory = _memory;
    return _memory;
  }
}

// A What-If name as the server reads one: a variable, optionally after its
// function, or an access path whose subscripts are an integer or a path.
const IDENTIFIER = "[A-Za-z_]\\w*";
const MEMBERS = `(?:(?:\\.|->)${IDENTIFIER})`;
const WHAT_IF_NAME = new RegExp(`^\\**${IDENTIFIER}(?:::${IDENTIFIER})*` +
  `(?:${MEMBERS}|\\[(?:\\d+|${IDENTIFIER}${MEMBERS}*)\\])*$`);

const DECLARED = /^\(declare-fun\s+(\S+)/;

function valueKey(value) {
  return `${value.kind}:${value.value}`;
}

// The order of Python's sorted(), which the server's lists have.
function compareText(a, b) {
  return a < b ? -1 : a > b ? 1 : 0;
}

// A function as the report page writes it, in a group's heading and before
// `::` in a name: most of a long C++ name is the anonymous namespace it is
// in.
export function functionName(name) {
  return (name ?? "").replaceAll("(anonymous namespace)::", "");
}

// Statement text longer than this is shortened wherever it is shown.
const STATEMENT_LENGTH = 40;

// A statement as every view shows it, with the whole text kept for a hover.
// A short one stays whole. A longer call keeps what it calls, receiver
// included, and drops its arguments; when even that is long, or it is not a
// call, it is cut. Callers keep the statement for the title.
export function shortenStatement(statement, callee) {
  if (!statement || statement.length <= STATEMENT_LENGTH) return statement;

  // What the outermost call calls: the text before the parenthesis that
  // closes the statement's last one.
  let _head = "";
  if (statement.endsWith(")")) {
    let _depth = 0;
    for (let _i = statement.length - 1; _i > 0; --_i) {
      if (statement[_i] === ")") ++_depth;
      else if (statement[_i] === "(" && --_depth === 0) {
        _head = statement.slice(0, _i).trimEnd();
        break;
      }
    }
  }
  // A template argument list cut in two is not a name.
  const _plain = _head.replaceAll("->", ".");
  const _whole = _plain.split("<").length === _plain.split(">").length;
  if (_head && _whole && _head.length <= STATEMENT_LENGTH - 3) {
    return `${_head}(\u2026)`;
  }
  if (callee) return `${callee}(\u2026)`;
  return `${statement.slice(0, STATEMENT_LENGTH - 1)}\u2026`;
}

// A call as a label shows it: its statement shortened, and the whole
// statement for the hint.
function labelCall(origin) {
  const _statement = origin.statement ?? null;
  return {
    code: _statement ? shortenStatement(_statement, origin.callee) : null,
    full: _statement
  };
}

// The words of a label about the conjured symbol `origin` describes: `parts`,
// strings and `{ code }` source text, and the `hint` that explains them.
// `memory` is what the value stands for when a reader knows better than the
// origin, such as a field of the memory a call replaced.
function originWords(origin, memory) {
  const _reason = origin.reason;
  if (_reason === "call_result") {
    const _call = labelCall(origin);
    const _code = origin.callee ? `${origin.callee}()` : _call.code;
    if (!_code) return null;
    const _whole = origin.callee ? origin.statement : _call.full;
    return {
      parts: memory
        ? [ ...memory, " of the result of ", { code: _code } ]
        : [ "result of ", { code: _code } ],
      hint: "The analyzer did not see inside " +
        (_whole && _whole !== _code ? _whole : "this call") + "."
    };
  }
  if (_reason === "invalidation" || _reason === "loop_widening") {
    const _replaced = memory
      ?? (origin.region ? [ { code: origin.region } ]
        : [ origin.globals ? "a global" : "a value" ]);
    if (_reason === "loop_widening") {
      return {
        parts: [ ..._replaced, " after the loop may have changed it" ],
        hint: "The analyzer stopped following this loop after a few " +
          "passes, so it treats the function's variables and globals as " +
          "changed by it."
      };
    }
    const _call = labelCall(origin);
    if (!_call.code) return null;
    return {
      parts: [ ..._replaced, " after ", { code: _call.code },
        " may have changed it" ],
      hint: `The analyzer did not see inside ${_call.full}, so it treats ` +
        (origin.globals ? "every global" : "the memory the call can reach") +
        " as changed."
    };
  }
  // Clang writes "other" when it cannot tell, and a reader must read a
  // reason it does not know the same way.
  const _call = labelCall(origin);
  if (!_call.code) return null;
  return {
    parts: memory
      ? [ ...memory, " of the value from ", { code: _call.code } ]
      : [ "value from ", { code: _call.code } ],
    hint: "The analyzer did not record what kind of value this is." +
      (_call.full !== _call.code ? ` It came from ${_call.full}.` : "")
  };
}

// The words for a part of memory that has no name of its own, from the end
// of its access path, such as `.Block`: a part of the value a call returned or
// a checker made reads as that part of it, and a part of memory a call or a
// loop replaced as that part of a value. The path starts at memory without a
// name, so when the replaced memory has one, that memory is only a part of
// it.
function memberWords(member, origin) {
  const _reason = origin?.reason;
  if (_reason !== "invalidation" && _reason !== "loop_widening") {
    return [ { code: member } ];
  }
  if (origin.region) {
    return [ { code: member }, " of part of ", { code: origin.region } ];
  }
  return [ { code: member }, origin.globals ? " of a global" : " of a value" ];
}

// What a reader is shown for a symbol in place of its dump, or null to show
// the dump: `text`, the label as plain text, and `parts`, the same as strings
// and `{ code }` source text; `hint`, which explains it, or null; the
// `location` and `frame` of the statement or loop it came from (null and
// undefined when it has none), and the `function` the value was made in when
// that is not `stepFrame`, the frame of the Path Step shown, or else null.
// A derived symbol is labelled from its parent's entry, which `symbolOf`
// gives by dump. Only reasons Clang recorded are told.
export function symbolLabel(entry, stepFrame, symbolOf = () => undefined) {
  if (!entry) return null;

  // The memory's own name is what a reader would write for the value.
  if (entry.kind === "region_value") {
    if (!entry.name) return null;
    return {
      text: entry.name,
      parts: [ { code: entry.name } ],
      hint: "What this memory held before the path changed it, which the " +
        "analyzer knows only from the constraints on it.",
      location: null,
      frame: undefined,
      function: null
    };
  }
  if (entry.kind === "extent") {
    if (!entry.region) return null;
    return {
      text: `size of ${entry.region}`,
      parts: [ "size of ", { code: entry.region } ],
      hint: "The size in bytes of this memory, which the analyzer knows " +
        "only from the constraints on it.",
      location: null,
      frame: undefined,
      function: null
    };
  }

  let _origin = entry.origin;
  let _memory = null;
  if (entry.kind === "derived") {
    _origin = symbolOf(entry.parent)?.origin;
    const _parent = _origin?.region
      ? [ "part of ", { code: _origin.region } ] : null;
    _memory = entry.region ? [ { code: entry.region } ]
      : entry.member ? memberWords(entry.member, _origin) : _parent;
  }
  let _words = _origin ? originWords(_origin, _memory) : null;
  if (!_words && entry.kind === "derived") {
    if (!entry.region && !entry.member) return null;
    _words = {
      parts: entry.region ? [ { code: entry.region } ]
        : [ { code: entry.member }, " of a value" ],
      hint: "Part of a value whose origin the analyzer did not record."
    };
  }
  if (!_words && !_origin) return null;

  const { parts, hint } = _words ?? {
    parts: [ "origin not recorded" ],
    hint: "The analyzer did not record what kind of value this is."
  };
  const _otherFrame =
    _origin?.frame === undefined || _origin.frame !== stepFrame;
  return {
    text: parts.map(part => part.code ?? part).join(""),
    parts,
    hint,
    location: (_origin?.reason === "loop_widening" && _origin.loop
      ? _origin.loop : _origin?.location) ?? null,
    frame: _origin?.frame,
    function: _origin && _otherFrame ? _origin.function ?? null : null
  };
}

// A symbolic expression's dump, read into a tree, or null when it is not one
// this can read to the end. The dumps are the analyzer's:
//   (L) op R         R an integer, as 7U or -1, or (R) for a symbol
//   N op (R)         N an integer
//   (TYPE) (X)       a cast
//   -X, ~X, !X       X a symbol, or (X) for an expression
// where L, R and X are in turn expressions, and a symbol is an atom such as
// reg_$1<int n> or conj_$2{int, LC1, S3, #1}. Whatever else it meets, such as
// an operator or an atom it does not know the end of, makes the whole read
// fail: a half-read expression would be a wrong label.
const ATOM_START = /^[a-z]+_\$\d+(?=[<{])/;
const INTEGER = /^-?\d+U?(?![\w$])/;
const OPERATOR = /^ (<<|>>|<=|>=|==|!=|&&|\|\||[*/%+\-<>&^|]) /;
const BRACKETS = { "<": ">", "{": "}", "(": ")" };

// The index after the bracket that closes the one at `at`, or -1. A "->" is
// no angle bracket.
function closingBracket(text, at) {
  const _open = text[at];
  const _close = BRACKETS[_open];
  let _depth = 0;
  for (let _i = at; _i < text.length; ++_i) {
    if (text[_i] === _open) ++_depth;
    else if (text[_i] === _close && !(_open === "<" && text[_i - 1] === "-")) {
      if (--_depth === 0) return _i + 1;
    }
  }
  return -1;
}

// One operand at `at` in `text`: { node, end }, or null.
function readOperand(text, at) {
  const _rest = text.slice(at);
  const _integer = INTEGER.exec(_rest);
  if (_integer) {
    return { node: { integer: _integer[0].replace(/U$/, "") },
      end: at + _integer[0].length };
  }
  const _atom = ATOM_START.exec(_rest);
  if (_atom) {
    const _end = closingBracket(text, at + _atom[0].length);
    return _end < 0
      ? null : { node: { atom: text.slice(at, _end) }, end: _end };
  }
  if (/^[-~!]/.test(_rest)) {
    const _operand = readOperand(text, at + 1);
    return _operand && { node: { unary: _rest[0], operand: _operand.node },
      end: _operand.end };
  }
  if (_rest[0] !== "(") return null;

  const _end = closingBracket(text, at);
  if (_end < 0) return null;
  const _inner = text.slice(at + 1, _end - 1);
  // "(TYPE) (X)" is a cast; a group beside an operator is not.
  if (text[_end] === " " && text[_end + 1] === "(") {
    const _cast = readOperand(text, _end + 1);
    return _cast && _inner
      ? { node: { cast: _inner, operand: _cast.node }, end: _cast.end }
      : null;
  }
  const _node = readExpression(_inner);
  return _node && { node: _node, end: _end };
}

function readExpression(text) {
  const _left = readOperand(text, 0);
  if (!_left) return null;
  if (_left.end === text.length) return _left.node;

  const _operator = OPERATOR.exec(text.slice(_left.end));
  if (!_operator) return null;
  const _right = readOperand(text, _left.end + _operator[0].length);
  return _right && _right.end === text.length
    ? { operator: _operator[1], left: _left.node, right: _right.node }
    : null;
}

export function parseSymbolExpression(dump) {
  return typeof dump === "string" ? readExpression(dump) : null;
}

// The label of a symbolic expression, in the shape symbolLabel returns, built
// from the labels of its atoms; null when the dump is not an expression this
// can read or an atom has neither a label nor a name. `symbolOf` gives an
// atom's entry by dump, and `nameOf` the variable that held it, if any. A
// name is never read out of the dump.
export function expressionLabel(dump, stepFrame, symbolOf, nameOf = () => {}) {
  const _tree = parseSymbolExpression(dump);
  if (!_tree || _tree.atom === dump) return null;

  // The parts of a node, or null when one atom cannot be told.
  const _parts = (node, outer) => {
    if (node.integer !== undefined) return [ node.integer ];
    if (node.atom !== undefined) {
      const _entry = symbolOf(node.atom);
      const _variable = nameOf(node.atom);
      // A region value goes by the variable that held it, as its own row in
      // the path condition does, and by its memory otherwise.
      if (_variable && (!_entry || _entry.kind === "region_value")) {
        return [ { code: _variable } ];
      }
      const _label = symbolLabel(_entry, stepFrame, symbolOf);
      if (_label) return _label.parts;
      return _variable ? [ { code: _variable } ] : null;
    }
    if (node.cast !== undefined) {
      const _operand = _parts(node.operand, "cast");
      return _operand && [ ..._operand, " as ", { code: node.cast } ];
    }
    if (node.unary !== undefined) {
      const _operand = _parts(node.operand, "unary");
      return _operand && [ node.unary, ..._operand ];
    }
    const _left = _parts(node.left, node.operator);
    const _right = _parts(node.right, node.operator);
    if (!_left || !_right) return null;
    const _body = [ ..._left, ` ${node.operator} `, ..._right ];
    // An operator inside a different one, a cast or a minus needs its
    // brackets to read the same.
    return outer && outer !== node.operator ? [ "(", ..._body, ")" ] : _body;
  };
  const _all = _parts(_tree);
  if (!_all) return null;

  // Neighbouring strings are one.
  const parts = _all.reduce((joined, part) => {
    if (typeof part === "string" &&
        typeof joined[joined.length - 1] === "string") {
      joined[joined.length - 1] += part;
    } else {
      joined.push(part);
    }
    return joined;
  }, []);
  return {
    text: parts.map(part => part.code ?? part).join(""),
    parts,
    hint: "Worked out from the values named here.",
    location: null,
    frame: undefined,
    function: null
  };
}

// What a variable is shown as where a line has several visits, and what the
// analyzer has for it. `entry` is its symbol's entry, or its expression's
// when it holds an expression, which gives the type.
export function describeVariable(variable, snapshot, entry) {
  if (variable.kind !== "symbol") {
    return { text: variable.value, raw: variable.value };
  }
  const _range = (snapshot.symbol_ranges ?? []).find(range =>
    range.symbol === variable.value)?.range;
  if (!_range) return { text: "(symbolic)", raw: "(symbolic)" };
  return {
    text: readableRange(_range, variable.value, symbolType(entry)) ?? _range,
    raw: _range
  };
}

const NAME_COLLATOR = new Intl.Collator("en", { numeric: true });

// Orders variable names as a reader counts, a2 before a10, and the same in
// every browser locale.
export function compareNames(a, b) {
  return NAME_COLLATOR.compare(a, b);
}

// An absolute path, such as the file of a lambda's type, at the start of a
// word.
const ABSOLUTE_PATH = /(?<![^\s(<{[,])(?:\/[^\s/:()<>{}[\],]+){2,}/g;

// Text with each absolute path cut to its file name, as the States view shows
// locations. Callers keep the whole text for a title. Anything but text, such
// as a number, has no path in it.
export function shortenPaths(text) {
  return (typeof text === "string" ? text : "").replace(ABSOLUTE_PATH,
    path => path.slice(path.lastIndexOf("/") + 1));
}

// Where a label's value came from, as the panel writes it after the label:
// the line, with the file when that is another than the step's. A location
// whose file is not a path, such as a plist index left in a trace that was
// stored unmapped, is written without the file.
export function labelPlace(label) {
  const _location = label.location;
  if (!_location) return null;

  const _file = typeof _location.file === "string"
    ? shortenPaths(_location.file) : "";
  return label.otherFile && _file
    ? `${_file}:${_location.line}` : `line ${_location.line}`;
}

// The integer type at the start of a symbol dump, as in reg_$1<int a> or
// conj_$2{unsigned long, ...}, as { min, max }, or null when its width or
// signedness cannot be told for certain. A typedef, an enum, a pointer, a
// float or a plain char (signed on some targets only) is not told, and
// neither is a symbol that is an expression, which does not start with its
// own name.
const SYMBOL_TYPE = /^[a-z]+_\$\d+([<{])([^,{}<>]*)/;
const TYPE_WORDS = new Set([
  "unsigned", "signed", "char", "short", "int", "long", "__int128",
  "const", "volatile"
]);

// `typeName` is the canonical type the symbol table names, which tells a
// typedef and a derived symbol too; without it the type is read from the dump.
function integerType(symbol, typeName) {
  const _type = [];
  if (typeName !== undefined) {
    const _words = typeName.trim().split(/\s+/);
    while (_words.length && TYPE_WORDS.has(_words[0])) {
      _type.push(_words.shift());
    }
    if (!_type.length || _words.length) return null;
  } else {
    const _match = SYMBOL_TYPE.exec(symbol ?? "");
    if (!_match) return null;

    const _words = _match[2].trim().split(/\s+/);
    while (_words.length && TYPE_WORDS.has(_words[0])) {
      _type.push(_words.shift());
    }
    // The dump of a region symbol names the region after its type. A
    // conjured symbol has nothing after the type.
    const _named = _match[1] === "<" && _words.length === 1 &&
      /^[A-Za-z_]\w*$/.test(_words[0]);
    if (!_type.length || _words.length && !_named) return null;
  }

  const _count = word => _type.filter(_word => _word === word).length;
  const _unsigned = _count("unsigned") > 0;
  if (_unsigned && _count("signed")) return null;
  // A plain char has the signedness of the target.
  if (_count("char") && !_unsigned && !_count("signed")) return null;

  let _bits;
  if (_count("__int128")) _bits = 128;
  else if (_count("char")) _bits = 8;
  else if (_count("short")) _bits = 16;
  // A long is 32 bits on some targets and 64 on others. readableRange takes
  // 64, which is certain only when a range reaches the 64-bit extreme, as a
  // 32-bit long cannot.
  else if (_count("long")) _bits = 64;
  else _bits = 32;

  const _half = 1n << BigInt(_bits - 1);
  return _unsigned
    ? { min: 0n, max: 2n * _half - 1n }
    : { min: -_half, max: _half - 1n };
}

const RANGE_SET = /^\{\s*(?:\[-?\d+, -?\d+\],?\s*)+\}$/;

// A symbol dump starts with its type, as in reg_$1<int * g> or
// conj_$2{void *, ...}. The type stops at a brace or angle bracket, so a
// pointer inside the region of a non-pointer field does not count. Only for a
// trace whose table does not say.
const POINTER_SYMBOL = /^[a-z]+_\$\d+[<{][^,{}<>]*\*/;

// What a symbol's entry says about its type: `{ type, pointer }`, or
// undefined for a trace without the table's types. Then the dump is read.
export function symbolType(entry) {
  return entry && entry.type !== undefined
    ? { type: entry.type, pointer: !!entry.pointer } : undefined;
}

// Whether a range is every address but null, whatever the address width.
function isNonNull(range) {
  return /^\{ \[1, (?:4294967295|18446744073709551615)\] \}$/.test(range);
}

const COMPARISONS = new Set([ "<", ">", "<=", ">=", "==", "!=" ]);

// Whether a symbol dump is a comparison at its top, such as `(a) >= (b)`,
// whose value is 1 when it holds and 0 when it does not.
function isComparison(symbol) {
  return COMPARISONS.has(parseSymbolExpression(symbol)?.operator);
}

// A range of integers as a person would say it, or null to show the
// analyzer's own range. `symbol` is the dump the range is for, which gives the
// type. A single value needs no type, and the other forms are about the
// type's extremes, so they need it. A comparison reads true or false, not
// as the number the analyzer keeps for it.
export function readableRange(range, symbol, info) {
  if (!RANGE_SET.test(range)) return null;

  if (isComparison(symbol)) {
    if (range === "{ [0, 0] }") return "false";
    const _bounds = [ ...range.matchAll(/\[(-?\d+), (-?\d+)\]/g) ]
      .map(match => [ BigInt(match[1]), BigInt(match[2]) ]);
    return _bounds.every(([ low, high ]) => low > 0n || high < 0n)
      ? "true" : null;
  }

  // An address is null or not, whatever else is known of its range.
  const _pointer = info ? info.pointer : POINTER_SYMBOL.test(symbol ?? "");
  if (_pointer) {
    if (range === "{ [0, 0] }") return "null";
    if (isNonNull(range)) return "non-null";
  }

  const _intervals = [ ...range.matchAll(/\[(-?\d+), (-?\d+)\]/g) ]
    .map(match => [ BigInt(match[1]), BigInt(match[2]) ]);
  if (_intervals.some(([ low, high ]) => low > high) ||
      _intervals.some(([ low ], i) =>
        i && _intervals[i - 1][1] >= low)) return null;

  const [ _first ] = _intervals;
  const _last = _intervals[_intervals.length - 1];
  if (_intervals.length === 1 && _first[0] === _first[1]) {
    return String(_first[0]);
  }

  const _type = _pointer ? null : integerType(symbol, info?.type);
  if (!_type || _first[0] < _type.min || _last[1] > _type.max) return null;

  // The values of the type that the range leaves out.
  const _gaps = _intervals.map(([ low ], i) =>
    low - (i ? _intervals[i - 1][1] + 1n : _type.min));
  _gaps.push(_type.max - _last[1]);
  const _excluded = _gaps.reduce((sum, gap) => sum + gap, 0n);
  if (_excluded === 1n) {
    const _gap = _gaps.indexOf(1n);
    if (_gap === 0) return `not ${_type.min}`;
    if (_gap === _intervals.length) return `not ${_type.max}`;
    return `not ${_intervals[_gap - 1][1] + 1n}`;
  }

  if (_intervals.length !== 1) return null;
  if (_first[0] === _type.min && _first[1] < _type.max) {
    return `at most ${_first[1]}`;
  }
  if (_first[1] === _type.max && _first[0] > _type.min) {
    return `at least ${_first[0]}`;
  }
  return null;
}

// One value of an example of the path, as the reader is shown it: an address
// is null or not, since its number means nothing to the reader, and a
// comparison is true or false. `value` is the decimal the server read in the
// signedness of the analyzer's range, and `symbol` and `info` are what
// readableRange takes.
export function exampleValue(value, symbol, info) {
  if (isComparison(symbol)) return value === "0" ? "false" : "true";
  const _pointer = info ? info.pointer : POINTER_SYMBOL.test(symbol ?? "");
  if (_pointer) return value === "0" ? "null" : "non-null";
  return value;
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
