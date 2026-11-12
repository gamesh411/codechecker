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

export const TRACE_VERSION = 2;

// The snapshot lists, in the order a node lists their changes.
const LISTS = [
  "symbol_ranges", "variables", "path_condition", "smt_declarations"
];

// What two steps on one line are compared by, to show them as one choice.
const SHOWN_LISTS = [ "symbol_ranges", "variables", "path_condition" ];

// The keys of a symbol's origin, and of the symbol itself, that name a
// string.
const ORIGIN_STRINGS = [ "statement", "callee", "function", "region" ];
const SYMBOL_STRINGS = [ "parent", "region", "type" ];

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
    this._ordered = null;
    this._changes = new Map();
    this._silentChanges = null;
    this._symbols = null;
    this._calls = null;
  }

  // The symbol table as a Map from symbol dump to its entry: `kind`, `smt`
  // and `smt_signed` when the symbol has an SMT-LIB term, and for a conjured
  // symbol `origin`, with `reason` and `function` and, when Clang knows them,
  // `statement`, `location`, `callee` and `frame` (into `stack_frames`), and
  // for memory a call or a loop replaced, `region` or `globals`, and `loop`.
  // A derived symbol has its `parent`'s dump and its `region`'s access path.
  // A symbol has its `type`, the canonical type's name, and `pointer` when the
  // analyzer treats it as an address; a trace from a Clang before those has
  // neither.
  get symbols() {
    if (this._symbols) return this._symbols;

    const _s = this.strings;
    this._symbols = new Map((this.trace.symbols ?? []).map(entry => {
      const _symbol = { kind: entry.kind };
      SYMBOL_STRINGS.forEach(key => {
        if (entry[key] !== undefined) _symbol[key] = _s[entry[key]];
      });
      if (entry.pointer) _symbol.pointer = true;
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

  let _origin = entry.origin;
  let _memory = null;
  if (entry.kind === "derived") {
    _origin = symbolOf(entry.parent)?.origin;
    const _parent = _origin?.region
      ? [ "part of ", { code: _origin.region } ] : null;
    _memory = entry.region ? [ { code: entry.region } ] : _parent;
  }
  let _words = _origin ? originWords(_origin, _memory) : null;
  if (!_words && entry.kind === "derived") {
    if (!entry.region) return null;
    _words = { parts: [ { code: entry.region } ],
      hint: "Part of a value whose origin the analyzer did not record." };
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

// The name a region value atom goes by when no variable names it: the last
// word of its region, as in reg_$1<unsigned int n>.
function regionValueName(atom) {
  const _match = /^reg_\$\d+<(.*)>$/.exec(atom);
  const _word = _match?.[1].split(/\s+/).pop();
  return _word && /^[A-Za-z_~][\w.:]*$/.test(_word) ? _word : null;
}

// The label of a symbolic expression, in the shape symbolLabel returns, built
// from the labels of its atoms; null when the dump is not an expression this
// can read or an atom has neither a label nor a name. `symbolOf` gives an
// atom's entry by dump, and `nameOf` the variable that held it, if any.
export function expressionLabel(dump, stepFrame, symbolOf, nameOf = () => {}) {
  const _tree = parseSymbolExpression(dump);
  if (!_tree || _tree.atom === dump) return null;

  // The parts of a node, or null when one atom cannot be told.
  const _parts = (node, outer) => {
    if (node.integer !== undefined) return [ node.integer ];
    if (node.atom !== undefined) {
      const _label = symbolLabel(symbolOf(node.atom), stepFrame, symbolOf);
      if (_label) return _label.parts;
      const _name = node.atom.startsWith("reg_$")
        ? nameOf(node.atom) ?? regionValueName(node.atom) : null;
      return _name ? [ { code: _name } ] : null;
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
// analyzer has for it. `entry` is its symbol's entry, which gives the type.
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

// A range of integers as a person would say it, or null to show the
// analyzer's own range. `symbol` is the dump the range is for, which gives the
// type. A single value needs no type, and the other forms are about the
// type's extremes, so they need it.
export function readableRange(range, symbol, info) {
  if (!RANGE_SET.test(range)) return null;

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
