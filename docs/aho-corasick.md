# Aho-Corasick: How It Works

This document explains the Aho-Corasick algorithm and how `dux._ac_matcher`
uses it to find all occurrences of multiple patterns in a single linear scan,
in O(n + m) time where n = text length and m = total matches.

## Table of Contents

1. [The Problem](#the-problem)
2. [Why Not Naive Search?](#why-not-naive-search)
3. [The Three Layers of Aho-Corasick](#the-three-layers-of-aho-corasick)
4. [Layer 1: The Trie (Goto Function)](#layer-1-the-trie-goto-function)
5. [Layer 2: Failure Links](#layer-2-failure-links)
6. [Layer 3: Dictionary Suffix Links](#layer-3-dictionary-suffix-links)
7. [The Search Algorithm](#the-search-algorithm)
8. [Worked Example: Full Search](#worked-example-full-search)
9. [The C Implementation](#the-c-implementation)
10. [Integration with Pattern Matching](#integration-with-pattern-matching)
11. [Complexity Analysis](#complexity-analysis)

---

## The Problem

dux classifies files using glob patterns. Two pattern types require finding
substrings within the full file path:

- **CONTAINS** `**/node_modules/**` — match if `/node_modules/` appears
  *anywhere* in the path
- **ENDSWITH** `**/*.log` — match if `.log` appears *at the end* of the path

With many such patterns, dux needs to answer: "which of these hundreds of
substrings appear in this path?" — and it needs to answer this for every
single file on disk (millions of times on large trees).

---

## Why Not Naive Search?

The naive approach checks each pattern individually:

```python
for pattern in patterns:
    if pattern in path:
        matched.append(pattern)
```

If there are P patterns with average length k, and the path has length n:
- Each `in` check is O(n * k) worst case
- Total: O(P * n * k)

For 100 patterns and a path of 60 characters, that's ~6,000 character
comparisons per file. Across 1 million files: 6 billion comparisons.

Aho-Corasick does it in O(n) per file — just 60 character comparisons,
regardless of how many patterns exist. Across 1 million files: 60 million
comparisons. That's a 100x reduction.

---

## The Three Layers of Aho-Corasick

Aho-Corasick (1975) combines three ideas:

1. **A trie** — for walking the text character by character (like the
   PrefixTrie)
2. **Failure links** — for recovering after a mismatch without restarting
3. **Dictionary suffix links** — for efficiently finding all shorter patterns
   that end at the current position

Think of it as an enhanced trie that *never backtracks* in the text. It reads
each character exactly once, sliding through the trie state machine.

---

## Layer 1: The Trie (Goto Function)

First, we build a standard trie from the pattern strings. Let's use these
four patterns throughout the document:

| Pattern | Meaning in dux |
|---------|----------------|
| `he`    | (example)      |
| `she`   | (example)      |
| `his`   | (example)      |
| `hers`  | (example)      |

### Building the trie

Insert `he`:

```
(root)
  |
  h
  |
 [1]
  |
  e
  |
 [2] * ← output: "he"
```

Insert `she`:

```
  (root)
  /    \
 h      s
 |      |
[1]    [3]
 |      |
 e      h
 |      |
[2]*   [4]
        |
        e
        |
       [5] * ← output: "she"
```

Insert `his`:

```
    (root)
    /    \
   h      s
   |      |
  [1]    [3]
  / \     |
 e   i    h
 |   |    |
[2]* [6] [4]
      |    |
      s    e
      |    |
     [7]* [5] *
```

Insert `hers`:

```
      (root)
      /    \
     h      s
     |      |
    [1]    [3]
    / \     |
   e   i    h
   |   |    |
  [2]* [6] [4]
   |    |    |
   r    s    e
   |    |    |
  [8]  [7]* [5] *
   |
   s
   |
  [9] * ← output: "hers"
```

At this point, if we're at the root and see character `h`, we go to node 1.
If we see `s`, node 3. Any other character? The trie has no edge — we'd be
stuck.

A naive trie search would restart from the root. But Aho-Corasick uses
**failure links** to avoid losing progress.

---

## Layer 2: Failure Links

### The key insight

When the trie can't advance on character `c`, we don't restart from the root.
Instead, we ask: "what is the longest *proper suffix* of the string we've
matched so far that is also a *prefix* of some pattern in the trie?"

That suffix is where we "fall back" to — because any shorter match that
starts later in the text would start with that suffix.

### Formal definition

For a node that represents string `w`:

> `fail(node)` = the node representing the **longest proper suffix** of `w`
> that is also a prefix of some pattern in the trie.

"Proper" means strictly shorter than `w` itself.

### Computing failure links

Failure links are computed via **BFS** (breadth-first search) from the root.
This guarantees that when we compute `fail(v)`, all nodes at shorter depths
already have their failure links set.

**Depth 1:** All children of the root get `fail = root`.

```
  fail([1]) = root     (node for "h" → longest proper suffix "" = root)
  fail([3]) = root     (node for "s" → longest proper suffix "" = root)
```

**Depth 2:** For each node v at depth 2, reached from parent u by edge c:
- Start at `fail(u)`, walk up fail chain until a node with edge c is found
- If found, `fail(v)` = that node's c-child; otherwise `fail(v)` = root

```
  [2] represents "he", parent [1], edge 'e'
      fail([1]) = root.  root has edge 'e'? No → fail([2]) = root

  [4] represents "sh", parent [3], edge 'h'
      fail([3]) = root.  root has edge 'h'? Yes → [1]
      fail([4]) = [1]    ← "sh" fails to "h"

  [6] represents "hi", parent [1], edge 'i'
      fail([1]) = root.  root has edge 'i'? No → fail([6]) = root
```

**Depth 3:**

```
  [5] represents "she", parent [4], edge 'e'
      fail([4]) = [1].  [1] has edge 'e'? Yes → [2]
      fail([5]) = [2]   ← "she" fails to "he"

  [7] represents "his", parent [6], edge 's'
      fail([6]) = root.  root has edge 's'? Yes → [3]
      fail([7]) = [3]    ← "his" fails to "s"

  [8] represents "her", parent [2], edge 'r'
      fail([2]) = root.  root has edge 'r'? No → fail([8]) = root
```

**Depth 4:**

```
  [9] represents "hers", parent [8], edge 's'
      fail([8]) = root.  root has edge 's'? Yes → [3]
      fail([9]) = [3]    ← "hers" fails to "s"
```

### The trie with all failure links

```
      (root) ←─────────────────────────────────────────────┐
      /    \                                                │
     h      s                                               │
     |      |                                               │
    [1]    [3] ←──────── fail ──────── [7]* ("his")         │
    / \     |                           |                   │
   e   i    h                           │                   │
   |   |    |                           │                   │
  [2]* [6] [4]                          │                   │
   |    │    |                          │                   │
   │    │    e              ┌───────────┘                   │
   │    │    |              │                               │
   │   fail  [5] * ──fail──▶ [2]* ("he")                   │
   │    │                                                   │
   │    ▼                                                   │
   │  (root)                                                │
   r                                                        │
   |                                                        │
  [8] ── fail ──▶ (root)                                    │
   |                                                        │
   s                                                        │
   |                                                        │
  [9] * ── fail ──▶ [3] ── fail ────────────────────────────┘
```

Reading the diagram: `fail([5]) = [2]` means when `she` can't advance, fall
back to `he` — because `he` is the longest suffix of `she` that starts a
pattern in the trie.

---

## Layer 3: Dictionary Suffix Links

### The problem with bare failure links

Suppose we're at node [5] (`she`) and need to report all matching patterns.
Node [5] has output `she`. But `he` also ends here (it's a suffix of `she`).
To find it, we'd walk the fail chain: `[5] → [2]`. Node [2] has output `he`.
Then `[2] → root`. Root has no output. Done.

This works, but in the worst case the fail chain can be long and pass through
many nodes that have *no* output. Walking through them is wasted work.

### The optimization

The **dictionary suffix link** (also called "output link") shortcuts the fail
chain, jumping directly to the nearest ancestor-via-fail that has an output:

> `dict_suffix(node)` = the nearest node reachable via the fail chain
> that has an output, or -1 if none exists.

### Computing dictionary suffix links

Also done during the BFS, right after computing each node's fail link:

```
For node v with fail(v) = f:
    if f has an output:
        dict_suffix(v) = f
    else:
        dict_suffix(v) = dict_suffix(f)
```

For our example:

```
  dict_suffix([1]) = -1     (fail → root, root has no output)
  dict_suffix([3]) = -1     (fail → root, root has no output)
  dict_suffix([2]) = -1     (fail → root, root has no output)
  dict_suffix([4]) = -1     (fail → [1], [1] has no output; dict_suffix([1]) = -1)
  dict_suffix([6]) = -1     (fail → root, root has no output)
  dict_suffix([5]) = [2]    (fail → [2], [2] HAS output "he")
  dict_suffix([7]) = -1     (fail → [3], [3] has no output; dict_suffix([3]) = -1)
  dict_suffix([8]) = -1     (fail → root, root has no output)
  dict_suffix([9]) = -1     (fail → [3], [3] has no output; dict_suffix([3]) = -1)
```

Now when we're at node [5] and want all outputs: we report [5]'s output
(`she`), then jump via `dict_suffix([5]) = [2]` and report [2]'s output
(`he`). `dict_suffix([2]) = -1`, so we stop. Two steps, two outputs, zero
wasted nodes.

---

## The Search Algorithm

The complete search processes one character at a time. For each character:

1. **Advance:** Try to follow the edge for the current character. If no edge
   exists, follow fail links until we can advance or reach root.
2. **Collect:** Walk the dict_suffix chain from the current state, collecting
   all outputs.

```
state = root
for each character c in text:
    while state != root AND no edge c from state:
        state = fail(state)
    if edge c exists from state:
        state = follow edge c
    // state now represents the longest suffix of text[0..i] that is a prefix in the trie

    tmp = state
    while tmp != root:
        if tmp has output:
            report (position=i, value=tmp.output)
        tmp = dict_suffix(tmp)
```

The crucial invariant: **`state` always represents the longest suffix of the
text read so far that is a prefix of some pattern.** This guarantees no match
is missed.

---

## Worked Example: Full Search

Let's search for patterns `{he, she, his, hers}` in the text `"shers"`.

```
Text:    s   h   e   r   s
Index:   0   1   2   3   4
```

### Step 0: character `s` (index 0)

```
state = root
edge 's' from root? Yes → go to [3]
state = [3]

Collect outputs: [3] has no output, dict_suffix([3]) = -1 → nothing

Result so far: []
```

### Step 1: character `h` (index 1)

```
state = [3]
edge 'h' from [3]? Yes → go to [4]
state = [4]

Collect outputs: [4] has no output, dict_suffix([4]) = -1 → nothing

Result so far: []
```

### Step 2: character `e` (index 2)

```
state = [4]
edge 'e' from [4]? Yes → go to [5]
state = [5]

Collect outputs:
  [5] has output → report (2, "she")
  dict_suffix([5]) = [2]
  [2] has output → report (2, "he")
  dict_suffix([2]) = -1 → stop

Result so far: [(2, "she"), (2, "he")]
```

This is the magic moment. We're at position 2 (the `e` in `shers`). The
automaton is in state [5] representing `she`. The dict_suffix link immediately
jumps to [2] representing `he` — we find both patterns without rescanning.

### Step 3: character `r` (index 3)

```
state = [5]
edge 'r' from [5]? No
  follow fail: fail([5]) = [2]
  edge 'r' from [2]? Yes → go to [8]
state = [8]

Collect outputs: [8] has no output, dict_suffix([8]) = -1 → nothing

Result so far: [(2, "she"), (2, "he")]
```

At index 3, the `she` state can't handle `r`. The fail link sends us to
the `he` state, which *can* handle `r` (leading to `her`). No backtracking
in the text — we just advanced from index 2 to index 3 while changing state.

### Step 4: character `s` (index 4)

```
state = [8]
edge 's' from [8]? Yes → go to [9]
state = [9]

Collect outputs:
  [9] has output → report (4, "hers")
  dict_suffix([9]) = -1 → stop

Result so far: [(2, "she"), (2, "he"), (4, "hers")]
```

### Final result

```
Text:   s  h  e  r  s
            ▲  ▲     ▲
            │  │     └── "hers" ends at index 4
            │  ├──────── "she"  ends at index 2
            │  └──────── "he"   ends at index 2
```

```python
[(2, "she"), (2, "he"), (4, "hers")]
```

We processed 5 characters and found 3 matches. The text was read **exactly
once**, left to right, with zero backtracking.

### State transitions visualized

```
Index:  0     1     2         3          4
Char:   s     h     e         r          s
        │     │     │         │          │
State: root → [3] → [4] → [5]         [9]*
                            │   fail     ↑
                            └──→ [2] → [8]
                                  ↑
                            (took edge 'r')
```

---

## The C Implementation

### Node Structure

```c
#define AC_ALPHA 256

typedef struct {
    int children[AC_ALPHA];  // 256 ints = 1024 bytes
    int fail;                // failure link (node index)
    int output;              // index into values[], -1 = none
    int dict_suffix;         // output link (node index), -1 = none
} ACNode;
```

Compared to the PrefixTrie node (which has only `children` + `output`), the
AC node adds two extra fields: `fail` and `dict_suffix`. These are what make
"substring anywhere" matching possible.

### Memory Layout

```
AhoCorasickObject
  ├── nodes: ACNode[]          (heap)
  │     ├── [0] root           children[256], fail=0, output=-1, dict_suffix=-1
  │     ├── [1] 'h'            children[256], fail=0, output=-1, dict_suffix=-1
  │     ├── [2] 'he'           children[256], fail=0, output=0,  dict_suffix=-1
  │     ├── [3] 's'            children[256], fail=0, output=-1, dict_suffix=-1
  │     ├── [4] 'sh'           children[256], fail=1, output=-1, dict_suffix=-1
  │     ├── [5] 'she'          children[256], fail=2, output=1,  dict_suffix=2
  │     └── ...
  │
  └── values: PyObject*[]      (heap)
        ├── [0] → "he" rules
        ├── [1] → "she" rules
        └── ...
```

### The BFS for failure links

```c
/* Seed: children of root fail to root */
for (int c = 0; c < AC_ALPHA; c++) {
    int child = nodes[0].children[c];
    if (child > 0) {
        nodes[child].fail = 0;
        nodes[child].dict_suffix = -1;
        queue[tail++] = child;
    }
}

/* BFS: compute fail and dict_suffix for deeper nodes */
while (head < tail) {
    int u = queue[head++];
    for (int c = 0; c < AC_ALPHA; c++) {
        int v = nodes[u].children[c];
        if (v < 0) continue;

        /* Walk fail chain from parent u until edge c is found */
        int f = nodes[u].fail;
        while (f > 0 && nodes[f].children[c] < 0)
            f = nodes[f].fail;
        if (nodes[f].children[c] >= 0 && nodes[f].children[c] != v)
            f = nodes[f].children[c];
        nodes[v].fail = f;

        /* dict_suffix: nearest ancestor-via-fail with output */
        if (nodes[f].output >= 0)
            nodes[v].dict_suffix = f;
        else
            nodes[v].dict_suffix = nodes[f].dict_suffix;

        queue[tail++] = v;
    }
}
```

This runs once during `make_automaton()`. After this, the trie is frozen and
all searches are read-only.

### The hot loop (`iter`)

```c
int state = 0;
for (Py_ssize_t i = 0; i < text_len; i++) {
    unsigned char c = (unsigned char)text[i];

    /* Follow fail links until we can advance or reach root */
    while (state > 0 && nodes[state].children[c] < 0)
        state = nodes[state].fail;
    if (nodes[state].children[c] >= 0)
        state = nodes[state].children[c];

    /* Collect outputs: walk dict_suffix chain */
    int tmp = state;
    while (tmp > 0) {
        if (nodes[tmp].output >= 0) {
            // emit (i, values[nodes[tmp].output])
        }
        tmp = nodes[tmp].dict_suffix;
    }
}
```

The outer loop runs exactly `text_len` times. The inner `while` loops
(fail chain and dict_suffix chain) are amortized O(1) per character across
the entire text (see [Complexity Analysis](#complexity-analysis)).

### Lifecycle

```
  ┌────────────────────────────────────────────────────────────────────┐
  │                                                                    │
  │  AhoCorasick()         Build Phase (single-threaded)               │
  │       │                                                            │
  │       ├── add_word("/tmp/", rules1)                                │
  │       ├── add_word("/tmp",  rules2)        (CONTAINS alt)          │
  │       ├── add_word(".log",  rules3)        (ENDSWITH)              │
  │       ├── add_word("/.cache/", rules4)                             │
  │       │                                                            │
  │       ├── make_automaton()   ← BFS builds fail + dict_suffix links │
  │       │                        add_word() now raises RuntimeError  │
  │       │                                                            │
  │       │                    Query Phase (thread-safe, read-only)    │
  │       ├── iter("/a/tmp/err.log")     → [(5, r1), (4, r2), ...]   │
  │       ├── iter("/a/.cache/pip/x")    → [(9, r4)]                  │
  │       └── iter("/readme.txt")        → []                         │
  │                                                                    │
  └────────────────────────────────────────────────────────────────────┘
```

---

## Integration with Pattern Matching

### How patterns become AC keys

The `_classify` function in `patterns.py` converts glob patterns into AC keys:

```
Pattern                     Kind       val (any-pos)    alt (end-only)
─────────────────────────   ────────   ─────────────    ──────────────
**/node_modules/**          CONTAINS   /node_modules/   /node_modules
**/tmp/**                   CONTAINS   /tmp/            /tmp
**/*.log                    ENDSWITH   (empty)          .log
**/*.pyc                    ENDSWITH   (empty)          .pyc
```

CONTAINS patterns get **two** AC keys:
- `val = "/tmp/"` — fires when `/tmp/` appears anywhere (the segment is
  inside the path)
- `alt = "/tmp"` — fires only at the end of the path (the directory itself,
  e.g. `/a/tmp`)

ENDSWITH patterns get **one** AC key:
- `alt = ".log"` — fires only at the end of the path

### The end_only flag

Each AC value is a `list[tuple[PatternRule, bool]]` where the bool is
`end_only`. During matching:

```python
_lpath_end = len(lpath) - 1
for end_idx, entries in bk.ac.iter(lpath):
    for rule, end_only in entries:
        if end_only and end_idx != _lpath_end:
            continue    # matched mid-path, but rule requires end-of-path
        # accept match
```

This is how a single AC automaton handles both CONTAINS (match anywhere) and
ENDSWITH (match at end only) patterns simultaneously.

### Example: matching `/a/tmp/err.log`

```
AC keys in automaton: "/tmp/", "/tmp", ".log"

Text: /a/tmp/err.log
      0123456789...13

AC iter results:
  (5,  [("/tmp/" rules, end_only=False)])   ← "/tmp/" at index 5
  (5,  [("/tmp" rules,  end_only=True)])    ← "/tmp" at index 5
  (13, [(".log" rules,  end_only=True)])    ← ".log" at index 13

Filtering:
  "/tmp/" at 5, end_only=False  → ACCEPT (any position is fine)
  "/tmp"  at 5, end_only=True   → REJECT (5 != 13, not at end of path)
  ".log"  at 13, end_only=True  → ACCEPT (13 == 13, at end of path)

Final matches: [tmp_rules, log_rules]
```

### Why merge CONTAINS and ENDSWITH into one automaton?

Building separate automata would mean two passes over every path string. By
merging them, we get all matches in a single `iter()` call. The `end_only`
flag adds a trivial integer comparison to filter out mid-path hits for
end-only patterns. One pass instead of two, half the work.

---

## Complexity Analysis

### Time

| Operation        | Complexity         | Notes                            |
|------------------|--------------------|----------------------------------|
| `add_word`       | O(k)               | k = length of pattern            |
| `make_automaton` | O(sum of all k)    | BFS over all nodes               |
| `iter`           | O(n + m)           | n = text length, m = # of matches|

**Why iter is O(n + m):**

The fail-link following seems like it could make `iter` quadratic, but it's
amortized linear. Here's the argument:

- `state` can only increase depth by 1 per character (one trie edge).
- Each fail-link step *decreases* depth by at least 1.
- Total depth increases across the entire text: at most n.
- Therefore, total fail-link steps across the entire text: at most n.
- The dict_suffix chain visits each output exactly once: total m.
- Grand total: O(n + m).

### Space

Each ACNode: `256 * 4 + 4 + 4 + 4` = 1036 bytes.

Total nodes = sum of unique prefix characters across all patterns. For dux's
default ~50 patterns averaging ~10 characters with shared prefixes, the trie
typically has ~200-400 nodes using ~200-400 KB.

### Comparison with alternatives

| Approach          | Build time      | Search time per path | Handles overlap |
|-------------------|-----------------|----------------------|-----------------|
| Naive loop        | O(1)            | O(P * n * k)         | Yes             |
| Regex alternation | O(P * k)        | O(n * P) worst case  | Yes             |
| P separate tries  | O(P * k)        | O(P * n)             | Yes             |
| Aho-Corasick      | O(sum of k)     | O(n + m)             | Yes             |

Where P = number of patterns, k = average pattern length, n = text length,
m = number of matches. Aho-Corasick is the only approach where search time
is *independent* of the number of patterns.

### Comparison with PrefixTrie

| Property          | Aho-Corasick                | PrefixTrie                |
|-------------------|-----------------------------|---------------------------|
| Question answered | "Which patterns occur       | "Which stored keys are    |
|                   |  anywhere in the text?"     |  prefixes of the input?"  |
| Fail links        | Yes (BFS-constructed)       | None                      |
| Dict suffix links | Yes                         | None                      |
| Walk behavior     | Follow fail links on        | Stop on first missing     |
|                   | mismatch, never stop early  | edge                      |
| Must scan full    | Yes (any position may match)| No (stop at first gap)    |
| text?             |                             |                           |
| Node size         | 1036 bytes                  | 1028 bytes                |
| Use in dux        | CONTAINS + ENDSWITH         | STARTSWITH                |

The PrefixTrie can stop early because prefix matching only cares about the
beginning of the input. Aho-Corasick must scan the entire text because
patterns can appear at any position.
