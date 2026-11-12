# Constraints of a bug path

The report page can show what the Clang Static Analyzer knew at each step of a bug path.
It can also check, with the Z3 solver on the server, whether the path stays feasible under assumptions of your own.
This is an experimental feature.
It needs data that Clang records only when asked to, and its format may change between releases.

![Constraint Snapshot panel of a report](images/constraints_panel.png)

## Table of contents
* [Recording the constraints](#recording-the-constraints)
* [Storing the reports](#storing-the-reports)
* [Show constraints](#show-constraints)
* [Reading the Constraint Snapshot panel](#reading-the-constraint-snapshot-panel)
* [Selecting a step](#selecting-a-step)
* [What if](#what-if)
  * [Syntax](#syntax)
  * [Verdicts](#verdicts)
  * [Limits](#limits)
* [Control flow graph and analyzer states](#control-flow-graph-and-analyzer-states)
* [Server requirements](#server-requirements)

## Recording the constraints

Clang records the constraints when the analyzer option `emit-constraint-snapshots` is true.
It is off by default, and without it the report page looks as it always did.
Pass it to the Clang Static Analyzer through the `--analyzer-config` option of `CodeChecker analyze`:

```sh
CodeChecker analyze compile_commands.json -o ./reports \
  --analyzers clangsa \
  --analyzer-config clangsa:emit-constraint-snapshots=true
```

CodeChecker has no flag of its own for this.
The analyzer stores the data in the plist files in `./reports`, and `CodeChecker analyze` keeps it when it rewrites them.
The Clang documentation page "Constraint Snapshots in Plist Output"
(`clang/docs/analyzer/developer-docs/ConstraintSnapshots.md`) describes what is recorded and what it costs.
The plist files grow, and the analysis takes longer, so enable the option for the runs whose reports you want to explore this way.

The option exists only in a Clang that has this feature.
Whether a stored report has the data is visible in the report page, see [Show constraints](#show-constraints).

The What-If checks need the solver input (SMT-LIB) of the path, which Clang records only when it is built with Z3.
Without Z3 the report page still shows the ranges, but every What-If check answers "No verdict".

## Storing the reports

Store the reports as usual:

```sh
CodeChecker store ./reports -n my_run --url http://localhost:8001/Default
```

The constraint data is stored with the bug path of each report.
Only reports stored after they were analyzed with the option have it.
Storing again a report directory made without the option does not add it.
The data was recorded in a format version, and the server reads only the current one.
A report stored from an older Clang build of this feature has to be analyzed and stored again.

## Show constraints

The report page has a **Show constraints** checkbox below **Show arrows**.
It is off when the page opens.

The checkbox is disabled when the report has nothing to show.
Hover over it to see why:

| Tooltip | Meaning |
| --- | --- |
| Show constraints: click a bug path step or a source line to see what the analyzer knew there. | The checkbox is available. |
| This report has no constraint data. Analyze with the analyzer config option emit-constraint-snapshots=true to see what the analyzer knew at each step. | The report was analyzed without the option. |
| This report's constraint data was recorded in an older format, which this CodeChecker cannot show. Analyze and store the report again to see what the analyzer knew at each step. | The data is in a format version other than the one this server reads. |

With the checkbox ticked, the source lines that carry a bug path step are marked, and the path steps in the code and in the report tree can be selected.

## Reading the Constraint Snapshot panel

Selecting a step opens the **Constraint Snapshot** panel beside the source code.
Its title line gives the step, such as `Step 1 Assuming 'a' is not equal to 0`.
The panel can be closed with the cross in its header.

**Known here** is what the analyzer held at that step:

* **Variables** lists the variables the analyzer tracks at that point, sorted by name.
  A variable that holds a symbol shows the range of values the analyzer knows for it, and under it the symbol, such as `reg_$1<int a>`.
  A symbol without a known range reads "no known range".
  A variable that holds a concrete value shows the value, such as `&x`.
* A pointer symbol whose range is exactly zero reads **null**, and one whose range is everything except zero reads **non-null**.
  Hovering over the word shows the analyzer's own range.
* **Other symbols** lists symbols the analyzer has a range for that no listed variable holds, for example the value of an expression.

**Accumulated path condition** is everything the analyzer assumed from the start of the path up to that step, as a range for each symbol.
It only narrows along a path.
A symbol the analyzer no longer tracks at the selected step is still listed there, with a **dropped** tag.
Rows are named by the variable that held the symbol when there was one.

A symbol the analyzer made for the result of a call reads as that result, such as "result of `read_len()` line 8", wherever the panel would show the symbol: under a variable, in **Other symbols** and in the accumulated path condition.
The analyzer evaluated that call without seeing inside it.
Any other symbol the analyzer made up reads "origin not recorded" with the line of the statement it was made for, because the analyzer did not record why it made it, for example when a checker models the call, as for `malloc`.
When the value was made in another function than the one of the selected step, the function follows, such as "in `next_len`".
Hovering over the label shows the analyzer's own symbol, and clicking the line selects the step nearest to that statement.
Reports analyzed before Clang recorded this show the symbols as before.

The rows that changed at the selected step are highlighted, in **Known here** and in the accumulated path condition.
A row has a coloured bar on its left and a tag beside its range: **new** for a symbol that had no entry at the step before, and **narrowed** for one whose range is narrower than at the step before.
The step before is the closest earlier step that shows a different snapshot, so the edge into a statement and the event on it show the same changes.
Nothing is highlighted at the first step, which has no step before it.

A step where the analyzer assumed something that the bug path has no event for is marked.
It is a control flow step that adds to the path condition, with no event on its analyzer state, such as a branch the analyzer took without an event saying so.
In the report tree the event next to it has a small question mark before its text.
In the source its line has an amber bar on the left and an **assumes** tag at its end.
The tooltip of both says that the analyzer assumed something here that the bug path does not describe, and the panel of that step says **Assumed, no event**.

An integer range reads the way a person would say it, in the snapshot, the path condition and the line picker:

* A range that excludes exactly one value reads "not N", so `[-2147483648, -1] [1, 2147483647]` of an `int` reads "not 0".
* A range of one value reads as the value, so `[1, 1]` reads "1".
* A range that runs from a value to the largest value of the type reads "at least N", and one that runs from the smallest value of the type to a value reads "at most N".

The bounds of the type come from the type at the start of the symbol, such as `int` in `reg_$1<int a>`.
When the width or signedness of the type cannot be told for certain, as with a typedef, an enum, a plain `char` or a `long` whose range stops short of the 64-bit extreme, the range is printed as the analyzer prints it, as a list of closed intervals.
Any other range is printed that way too.
Hovering over a range that is shown in words shows the analyzer's own range.

## Selecting a step

With **Show constraints** ticked:

* Click a bug path step, in the code or in the report tree, to select it.
* Click a source line.
  If exactly one step is on the line, it is selected.
  If the line is not on the bug path, nothing changes and a short message says "Line N is not on this Bug Path, so the selection did not change."
  If several steps are on the line, a list titled "Line N: K Path Steps" opens.
  A line is visited more than once by a loop or by a function that is called from several places.
  Each entry of the list shows the step, and the values of the variables that differ between the entries, so that you can tell the visits apart.
  Steps that would show the same panel count as one entry.

At the foot of the list, **Choose in the control flow graph** and **Choose by analyzer state** select the step in the views described in [Control flow graph and analyzer states](#control-flow-graph-and-analyzer-states).

![The list of steps on one line](images/constraints_picker.png)

## What if

The panel ends with a **What if** section.
It lets you tell the server something you know that the analyzer does not, such as "this global is never null", and asks whether the reported path is still possible.
The server checks the path with Z3, using the solver input recorded for it.
No analysis is run again.

![A What-If check with its verdict](images/constraints_whatif.png)

### Syntax

Type a constraint in the field and press **Check** or Enter.
The field suggests an example from the variables of the step.
A constraint is one or more comparisons joined by `&&`:

```
a == 0
a == 0 && n > 3
p != null
```

* A comparison is `variable op value`.
  The operators are `==`, `!=`, `<`, `<=`, `>` and `>=`.
* The variable is a name from the **Variables** list of the step.
  ArrowDown opens a list of those names with their values, and Tab or Enter completes one.
* The value is an integer, or `null`, which is 0.
  There is no `||`, no arithmetic, and no comparison of two variables.
* A variable is read as the value it holds at the step you typed the constraint at.
  Comparisons on a signed type are signed, on an unsigned type unsigned.

A text that cannot be read is not added.
The field turns red and a message such as "Expected an operator (`==`, `!=`, `<`, `<=`, `>`, `>=`), found `x`." says what was expected, with the cursor placed where reading failed.

**Holds throughout the path** applies the constraint to every value the variable takes on the path, also after a call or an assignment replaces the value.
Without it the constraint is about the value at the step it was typed at.

Every constraint you add stays in the **Checked together** list, labeled by its step or "Throughout", and all of them are checked together against the path.
A constraint can be removed with the cross at its row.
Pressing **Check** with an empty field checks the list again.
Constraints exist only in the open report page, and opening another report starts with none.

### Verdicts

Every verdict is about this reported bug path.
It does not say whether the bug is reachable through another path.

The result is one or two boxes:

* **Feasible**: "This path stays feasible with `a == 0` at Step 1."
  The path is still possible under your assumptions.
* **Infeasible**: "This path is infeasible with `a == 0` at Step 1.", followed by where the path dies and the sentence "The bug may still be reachable through another path."
  The step where the path dies is a link that selects it, and the step is tagged **Path dies here** in the panel.
  Where it dies is one of:
  * "It dies right there, the first step whose path condition contradicts it."
  * "It dies at Step N, the first step whose path condition contradicts it."
  * "It dies earlier, at Step N, whose path condition already contradicts it."
  * "It is dead by Step N. The solver gave up before it could tell whether an earlier step is dead too."
    The step is then tagged **Path dead here**, because an earlier step may be dead too.
* **Unknown**: "No verdict for this path with `a == 0` at Step 1.", followed by the reason.
  The constraint that caused it is marked in the **Checked together** list.

With several constraints, "this path stays feasible with these 2 assumptions together." names the count instead of one constraint.

The reason of an Unknown verdict is one of:

* "The solver gave up on this path after 2000 ms."
* "The solver has no answer for this path (...)" with the solver's own reason.
* "Cannot read the constraint: ..." for a syntax error.
* "`x` has no tracked value at this Path Step.", or "at any Path Step" with **Holds throughout the path**.
* "N is outside the range of `x` (low to high)."
* "`x` holds ..., whose numeric value is not recorded, so only a comparison with 0 or null can be decided."
  This is the case of an address.
  An address is known to be non-null, and nothing else about it is recorded.
* "`x` holds ..., whose type the check cannot reason about."
* "This report has no Constraint Snapshots, so this path cannot be checked."
* "This report has no solver input for this path. Recording it needs a Clang built with Z3."
* "This report's Constraint Snapshots were recorded in an older format, so this path cannot be checked. Analyze and store it again to check it."
* "This report's Constraint Snapshots are unreadable, so this path cannot be checked."
* "The solver input recorded for this path is unreadable."

Each check also solves the path without your constraints, the baseline.
A baseline box appears only when the baseline is not Feasible, since then your assumptions are not what decides the verdict:

* "Without your assumption: Infeasible": "Z3 finds this path infeasible even with nothing added, although the analyzer's own solver accepted it. It is not your assumption that rules it out."
* "Without your assumption: Unknown": "There is no verdict for this path even with nothing added." and the reason.

### Limits

* The answer is about the one path of the report.
  An Infeasible verdict does not prove that the bug cannot happen.
* Constraints are never stored, not in the database and not in the browser.
  They cannot be shared or reviewed later.
* The solver gets 2000 ms for each call.
  A check makes up to two calls and then, if the path is infeasible, searches for the first step where it dies for up to 4000 ms more, so a check can hold a server worker for up to 8 seconds.
* The check reasons about what the analyzer recorded.
  Z3 and the analyzer's solver can disagree about nonlinear arithmetic or bit tricks.
  A variable it cannot reason about gives Unknown, not Feasible.

## Control flow graph and analyzer states

The header of the panel has two buttons, **CFG** and **States**, which show as icons below a 1280 pixel wide window.
Each opens a view next to the source code, and opening one closes the other.
Both mark the steps of the bug path, and selecting a step in them shows its panel.

![Control flow graph with the bug path marked](images/constraints_cfg.png)

The **Control flow graph** view shows the control flow graph of one function, chosen from a list when the path runs through several functions.
Its blocks are labeled B and the block number, with the lines they cover.
The edges and blocks the bug path took are outlined, and the rows that carry a path step are shaded and labeled with their step.
**Subexpressions** adds every element of the graph, as the `debug.DumpCFG` checker lists them.
Clicking a row with a step selects it, and a row with several steps opens the list described in [Selecting a step](#selecting-a-step).
Rows scrolled out of view are not in the page, so the search of the browser does not find them.

![Analyzer states](images/constraints_states.png)

The **Analyzer states** view lists the analyzer's nodes (nodes of the exploded graph) that the path steps come from, grouped by call stack and indented by call depth.
Each row gives the node number, its kind, its location and the step it belongs to.
Between rows it counts the nodes that have no path step.
It tells apart steps on the same line that differ only by calling context, which the source and the control flow graph cannot.

## Server requirements

The What-If check uses the `z3-solver` Python package, which provides the `z3` module.
It is listed in `web/requirements.txt` and is installed with the other requirements of the server:

```sh
pip install -r web/requirements.txt
```

The server imports it when it starts, so it is needed even if nobody uses What-If.
