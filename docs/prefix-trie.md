# PrefixTrie: How It Works

This document explains the trie data structure and how `dux._prefix_trie` uses
it to match STARTSWITH patterns in O(m) time, where m is the length of the
input string.

## Table of Contents

1. [The Problem](#the-problem)
2. [What Is a Trie?](#what-is-a-trie)
3. [Building a Trie — Step by Step](#building-a-trie--step-by-step)
4. [Searching a Trie](#searching-a-trie)
5. [Prefix Matching vs Exact Matching](#prefix-matching-vs-exact-matching)
6. [The PrefixTrie C Implementation](#the-prefixtrie-c-implementation)
7. [Integration with Pattern Matching](#integration-with-pattern-matching)
8. [Complexity Analysis](#complexity-analysis)

---

## The Problem

dux uses glob patterns like `**/npm-debug.log*` to classify files. The
`**/prefix*` form means "match any file whose basename starts with `prefix`".
When there are many such patterns, the naive approach is a linear scan:

```python
for prefix, rule in startswith_rules:
    if basename.startswith(prefix):
        matched.append(rule)
```

This is O(n * m) where n = number of prefix patterns and m = basename length.
With 5 patterns it's fine. With 500, every single file in the tree pays the
cost of 500 `startswith` checks.

A prefix trie reduces this to O(m) — walk the basename character by character,
collecting all matching prefixes along the way, regardless of how many patterns
exist.

---

## What Is a Trie?

A **trie** (from "re**trie**val", pronounced "try") is a tree where each edge
represents one character of a key. Keys that share a common prefix share the
same path from the root.

Here is a trie containing three words: `cat`, `car`, and `cup`:

```
          (root)
            |
            c
            |
         [node 1]
          /    \
         a      u
        /        \
    [node 2]   [node 5]
      / \          |
     t   r         p
    /     \        |
 [node 3]  [node 4]  [node 6]
  "cat"     "car"     "cup"
```

Key properties:

- The **root** node represents the empty string.
- Each **edge** is labeled with a single character.
- Each **node** represents the string formed by concatenating all edge labels
  from the root to that node.
- **Terminal nodes** (marked with values) indicate that the string ending at
  that node is a stored key.

### Why not a hash table?

A hash table gives O(1) lookup for exact keys, but it can't answer "which of
my stored keys are prefixes of this input?" without checking every key. A trie
answers that question naturally — you walk the input and encounter all stored
prefixes along the path.

---

## Building a Trie — Step by Step

Let's insert three prefixes used in dux pattern matching:

1. `npm`
2. `npm-debug`
3. `.coverage`

### After inserting `npm`

We start at the root and create one node per character:

```
(root)
  |
  n
  |
 [1]
  |
  p
  |
 [2]
  |
  m
  |
 [3] * ← output: rules for "npm"
```

The `*` means node 3 is a terminal — it stores the value (a list of pattern
rules) associated with the prefix `npm`.

### After inserting `npm-debug`

Characters `n`, `p`, `m` already have nodes — we walk the existing path,
then branch off at the `-` after `m`:

```
(root)
  |
  n
  |
 [1]
  |
  p
  |
 [2]
  |
  m
  |
 [3] * ← output: "npm"
  |
  -
  |
 [4]
  |
  d
  |
 [5]
  |
  e
  |
 [6]
  |
  b
  |
 [7]
  |
  u
  |
 [8]
  |
  g
  |
 [9] * ← output: "npm-debug"
```

Notice that `npm` and `npm-debug` **share the path** for their common prefix
`npm`. This is the core space-saving property of a trie.

### After inserting `.coverage`

This prefix starts with `.`, which has no existing edge from the root. An
entirely new branch is created:

```
          (root)
          /    \
         n      .
        /        \
      [1]        [10]
       |           |
       p           c
       |           |
      [2]        [11]
       |           |
       m           o
       |           |
      [3] *      [12]
       |           |
       -           v
       |           |
      [4]        [13]
       |           |
       d           e
       |           |
      [5]        [14]
       |           |
       e           r
       |           |
      [6]        [15]
       |           |
       b           a
       |           |
      [7]        [16]
       |           |
       u           g
       |           |
      [8]        [17]
       |           |
       g           e
       |           |
      [9] *      [18] * ← output: ".coverage"
```

The trie now has 19 nodes (root + 18) and can match all three prefixes in a
single walk.

---

## Searching a Trie

### Exact lookup: "Is `npm` in the trie?"

Walk from root following edges `n` → `p` → `m`. Arrive at node 3. Node 3 has
an output → yes, `npm` is in the trie.

### Failed lookup: "Is `npa` in the trie?"

Walk from root following `n` → `p`. Next character is `a`. Node 2 has no edge
labeled `a` → no, `npa` is not in the trie.

### Prefix lookup: "Is `np` a prefix of any key?"

Walk from root following `n` → `p`. Arrive at node 2. Node 2 exists and has
children → yes, `np` is a prefix of stored keys (but is not itself a stored
key since node 2 has no output).

---

## Prefix Matching vs Exact Matching

The PrefixTrie's `iter()` method does something specific: given an input
string, it finds **all stored keys that are prefixes of the input**.

This is the inverse of the usual "find all strings that start with this
prefix" trie query. We walk the **input** through the trie and collect outputs
along the way.

### Example: `iter("npm-debug.log.1")`

```
Input:   n  p  m  -  d  e  b  u  g  .  l  o  g  .  1
Index:   0  1  2  3  4  5  6  7  8  9  10 11 12 13 14
```

Walk the trie character by character:

```
Step 0: char='n'  root → [1]         no output
Step 1: char='p'  [1]  → [2]         no output
Step 2: char='m'  [2]  → [3]         OUTPUT: "npm" rules ← collected!
Step 3: char='-'  [3]  → [4]         no output
Step 4: char='d'  [4]  → [5]         no output
Step 5: char='e'  [5]  → [6]         no output
Step 6: char='b'  [6]  → [7]         no output
Step 7: char='u'  [7]  → [8]         no output
Step 8: char='g'  [8]  → [9]         OUTPUT: "npm-debug" rules ← collected!
Step 9: char='.'  [9]  → ???         no child for '.' → STOP
```

Result: `[npm_rules, npm_debug_rules]`

Both prefixes were found in a single walk. The walk stopped at step 9 because
node 9 has no edge for `.` — no stored prefix can be longer than `npm-debug`
for this input, so there's no point continuing.

### Example: `iter(".coverage.server1")`

```
Step 0:  char='.'  root → [10]       no output
Step 1:  char='c'  [10] → [11]       no output
Step 2:  char='o'  [11] → [12]       no output
Step 3:  char='v'  [12] → [13]       no output
Step 4:  char='e'  [13] → [14]       no output
Step 5:  char='r'  [14] → [15]       no output
Step 6:  char='a'  [15] → [16]       no output
Step 7:  char='g'  [16] → [17]       no output
Step 8:  char='e'  [17] → [18]       OUTPUT: ".coverage" rules ← collected!
Step 9:  char='.'  [18] → ???        no child for '.' → STOP
```

Result: `[coverage_rules]`

### Example: `iter("readme.md")` (no match)

```
Step 0: char='r'  root → ???         no child for 'r' → STOP immediately
```

Result: `[]`

The trie rejects non-matching inputs in O(1) if the very first character has
no edge. In the worst case (the input shares a long prefix with a stored key
but doesn't match), it takes O(m) where m is the length of the shared prefix.

---

## The PrefixTrie C Implementation

### Node Structure

Each node is a fixed-size struct:

```c
#define PT_ALPHA 256

typedef struct {
    int children[PT_ALPHA];   // 256 ints = 1024 bytes
    int output;               // index into values[], -1 = none
} PTNode;
```

`children` is a 256-element array — one slot for every possible byte value.
`children[c]` is either:
- A non-negative node index (edge exists for character `c`)
- `-1` (no edge for character `c`)

This means **every character lookup is O(1)** — just an array index. No hash
tables, no linked lists, no binary search. The trade-off is memory: each node
is ~1 KB. But since prefixes are short (typically 5-20 characters), the trie
has few nodes and the total memory is small.

### Why 256 and not 128 (ASCII) or 26 (lowercase)?

UTF-8 encodes characters as 1-4 bytes. By using the full byte range (0-255),
the trie handles arbitrary UTF-8 strings correctly — it doesn't need to decode
UTF-8 at all. It just walks raw bytes. Non-ASCII filenames work transparently.

### The Trie Object

```c
typedef struct {
    PyObject_HEAD
    PTNode *nodes;        // heap-allocated node array
    int n_nodes;          // number of nodes in use
    int cap_nodes;        // allocated capacity
    PyObject **values;    // heap-allocated value array
    int n_values;         // number of values stored
    int cap_values;       // allocated capacity
    int built;            // 1 after build(), prevents mutation
} PrefixTrieObject;
```

Nodes and values are stored in separate arrays. A terminal node stores an
index into the values array rather than a direct pointer to the Python object.
This keeps the node struct small and cache-friendly.

### Memory Layout

```
PrefixTrieObject
  ├── nodes: PTNode[]          (heap)
  │     ├── [0] root           children[256], output=-1
  │     ├── [1] 'n'            children[256], output=-1
  │     ├── [2] 'p'            children[256], output=-1
  │     ├── [3] 'm'            children[256], output=0  ← values[0]
  │     ├── [4] '-'            ...
  │     └── ...
  │
  └── values: PyObject*[]      (heap)
        ├── [0] → list[PatternRule]   ("npm" rules)
        ├── [1] → list[PatternRule]   ("npm-debug" rules)
        └── [2] → list[PatternRule]   (".coverage" rules)
```

### The Hot Loop (`iter`)

```c
int state = 0;  // start at root
for (Py_ssize_t i = 0; i < text_len; i++) {
    unsigned char c = (unsigned char)text[i];
    int next = nodes[state].children[c];
    if (next < 0) break;           // no edge → stop
    state = next;
    if (nodes[state].output >= 0) {
        // this node is a terminal → collect its value
        PyList_Append(result, self->values[nodes[state].output]);
    }
}
```

This is the entire matching algorithm. No fail links, no suffix links, no
queues — just a straight walk through the array. The `break` on missing edge
is the key insight for prefix matching: once the input diverges from all stored
prefixes, no further matches are possible.

### Lifecycle

```
  ┌────────────────────────────────────────────────────────────────────┐
  │                                                                    │
  │  PrefixTrie()          Build Phase (single-threaded)               │
  │       │                                                            │
  │       ├── add_prefix("npm", rules1)                                │
  │       ├── add_prefix("npm-debug", rules2)                          │
  │       ├── add_prefix(".coverage", rules3)                          │
  │       │                                                            │
  │       ├── build()      ← freezes the trie (built=1)               │
  │       │                   add_prefix() now raises RuntimeError     │
  │       │                                                            │
  │       │                Query Phase (thread-safe, read-only)        │
  │       ├── iter("npm-debug.log")     → [rules1, rules2]            │
  │       ├── iter(".coverage.srv")     → [rules3]                    │
  │       └── iter("readme.md")         → []                          │
  │                                                                    │
  └────────────────────────────────────────────────────────────────────┘
```

The `built` flag enforces a two-phase protocol:
- **Build phase:** `add_prefix()` inserts prefixes. `iter()` is forbidden.
- **Query phase:** `iter()` walks the trie. `add_prefix()` is forbidden.

This makes concurrent `iter()` calls safe without locks — the trie is
immutable during the query phase. The C extension declares `Py_MOD_GIL_NOT_USED`
for free-threaded Python builds.

---

## Integration with Pattern Matching

In `dux/services/patterns.py`, the PrefixTrie replaces the old linear scan:

### Compile Time (called once at startup)

```python
def _build_prefix_trie(entries):
    # entries = [("npm-debug.log", rule1), (".coverage", rule2), ...]
    grouped = {}
    for prefix, rule in entries:
        grouped.setdefault(prefix, []).append(rule)

    pt = PrefixTrie()
    for key, rules in grouped.items():
        pt.add_prefix(key, rules)   # value = list of rules
    pt.build()
    return pt
```

Rules with the same prefix string are grouped into a single list, so
overlapping patterns share a trie node.

### Match Time (called once per file/directory)

```python
# Old: O(n * m) linear scan
for prefix, rule in bk.startswith:
    if lbase.startswith(prefix):
        ...

# New: O(m) trie walk
for rules in bk.prefix_trie.iter(lbase):
    for rule in rules:
        ...
```

Each `iter()` call returns a list of values (each value is itself a
`list[PatternRule]`) for every prefix that matches the input basename.

### Full match_all pipeline

```
  Input: lpath="/a/npm-debug.log.1", lbase="npm-debug.log.1", is_dir=False

  ┌─────────────────────────────────────────────────────────────┐
  │                      match_all()                            │
  │                                                             │
  │  1. EXACT        dict[lbase] lookup           O(1)          │
  │       │                                                     │
  │  2. AC           ac.iter(lpath)               O(path_len)   │
  │       │          Aho-Corasick automaton                      │
  │       │          (CONTAINS + ENDSWITH)                       │
  │       │                                                     │
  │  3. PREFIX TRIE  prefix_trie.iter(lbase)      O(base_len)   │
  │       │          ← you are here                             │
  │       │          (STARTSWITH)                                │
  │       │                                                     │
  │  4. GLOB         fnmatch fallback             O(n * m)      │
  │       │                                                     │
  │  5. ADDITIONAL   path prefix checks           O(n)          │
  │                                                             │
  │  Result: list[PatternRule], one per category                │
  └─────────────────────────────────────────────────────────────┘
```

---

## Complexity Analysis

### Time

| Operation     | Complexity | Notes                              |
|---------------|------------|------------------------------------|
| `add_prefix`  | O(k)       | k = length of the prefix key       |
| `build`       | O(1)       | Just sets a flag                   |
| `iter`        | O(m)       | m = length of input text           |

`iter` is O(m) **regardless of how many prefixes are stored**. With 5 prefixes
or 5000, the walk takes the same number of steps for a given input.

Compare to the linear scan:

| Approach     | 10 prefixes | 100 prefixes | 1000 prefixes |
|--------------|-------------|--------------|---------------|
| Linear scan  | 10 * m      | 100 * m      | 1000 * m      |
| PrefixTrie   | m           | m            | m             |

### Space

Each node is `256 * sizeof(int) + sizeof(int)` = 1028 bytes.

Total nodes = sum of unique prefix characters across all stored keys (shared
prefixes are counted once). For typical dux patterns (10-20 short prefixes
like `npm-debug.log`, `.coverage`, `.nox`), the trie has ~100-200 nodes,
using ~100-200 KB. Negligible compared to the scan tree which holds millions
of nodes on large filesystems.

### Comparison with Aho-Corasick

Both are trie-based, but they solve different problems:

| Property          | PrefixTrie                | Aho-Corasick             |
|-------------------|---------------------------|--------------------------|
| Question answered | "Which stored keys are    | "Which stored keys       |
|                   |  prefixes of the input?"  |  occur anywhere in the   |
|                   |                           |  input?"                 |
| Fail links        | None                      | Yes (BFS-constructed)    |
| Dict suffix links | None                      | Yes                      |
| Walk behavior     | Stop on first missing     | Follow fail links to     |
|                   | edge                      | continue matching        |
| Build complexity  | O(total key chars)        | O(total key chars)       |
| Query complexity  | O(input length)           | O(input length + matches)|
| Use in dux        | STARTSWITH patterns       | CONTAINS + ENDSWITH      |

The PrefixTrie is simpler because prefix matching doesn't need the "resume
after mismatch" behavior that Aho-Corasick provides. When a character has no
edge, no stored prefix can possibly match — we're done.
