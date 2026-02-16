#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdlib.h>
#include <string.h>

/*
 * Custom Aho-Corasick automaton for multi-pattern string matching.
 *
 * Declares GIL-free safety (Py_MOD_GIL_NOT_USED) for free-threaded Python.
 * The automaton is built once (add_word + make_automaton) then only read
 * during iter() — inherently thread-safe for concurrent readers.
 *
 * Python API:
 *   ac = AhoCorasick()
 *   ac.add_word(key: str, value: object)
 *   ac.make_automaton()
 *   ac.iter(text: str) -> list[tuple[int, object]]
 */

#define AC_ALPHA 256  /* full byte range for UTF-8 safety */

typedef struct {
    int children[AC_ALPHA];
    int fail;
    int output;       /* index into values[], -1 = none */
    int dict_suffix;  /* nearest ancestor with output via fail chain */
} ACNode;

typedef struct {
    PyObject_HEAD
    ACNode *nodes;
    int n_nodes;
    int cap_nodes;
    PyObject **values;
    int *key_lens;
    int n_values;
    int cap_values;
    int built;  /* 1 after make_automaton() */
} AhoCorasickObject;

/* ------------------------------------------------------------------ */
/* Helpers                                                            */
/* ------------------------------------------------------------------ */

static int
ac_new_node(AhoCorasickObject *self)
{
    if (self->n_nodes >= self->cap_nodes) {
        int new_cap = self->cap_nodes * 2;
        ACNode *tmp = (ACNode *)realloc(self->nodes,
                                        sizeof(ACNode) * (size_t)new_cap);
        if (!tmp) return -1;
        self->nodes = tmp;
        self->cap_nodes = new_cap;
    }
    ACNode *nd = &self->nodes[self->n_nodes];
    memset(nd->children, 0xff, sizeof(nd->children));  /* fill with -1 */
    nd->fail = 0;
    nd->output = -1;
    nd->dict_suffix = -1;
    return self->n_nodes++;
}

static int
ac_new_value(AhoCorasickObject *self, PyObject *val, int key_len)
{
    if (self->n_values >= self->cap_values) {
        int new_cap = self->cap_values * 2;
        PyObject **tv = (PyObject **)realloc(
            self->values, sizeof(PyObject *) * (size_t)new_cap);
        if (!tv) return -1;
        self->values = tv;
        int *tk = (int *)realloc(self->key_lens,
                                 sizeof(int) * (size_t)new_cap);
        if (!tk) return -1;
        self->key_lens = tk;
        self->cap_values = new_cap;
    }
    Py_INCREF(val);
    self->values[self->n_values] = val;
    self->key_lens[self->n_values] = key_len;
    return self->n_values++;
}

/* ------------------------------------------------------------------ */
/* Type methods                                                       */
/* ------------------------------------------------------------------ */

static PyObject *
AhoCorasick_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    (void)args; (void)kwds;
    AhoCorasickObject *self = (AhoCorasickObject *)type->tp_alloc(type, 0);
    if (!self) return NULL;

    self->cap_nodes = 256;
    self->nodes = (ACNode *)malloc(sizeof(ACNode) * (size_t)self->cap_nodes);
    if (!self->nodes) {
        Py_DECREF(self);
        return PyErr_NoMemory();
    }
    self->n_nodes = 0;

    self->cap_values = 64;
    self->values = (PyObject **)malloc(sizeof(PyObject *) * (size_t)self->cap_values);
    self->key_lens = (int *)malloc(sizeof(int) * (size_t)self->cap_values);
    if (!self->values || !self->key_lens) {
        free(self->nodes);
        free(self->values);
        free(self->key_lens);
        Py_DECREF(self);
        return PyErr_NoMemory();
    }
    self->n_values = 0;
    self->built = 0;

    /* Create root node (index 0) */
    if (ac_new_node(self) < 0) {
        free(self->nodes);
        free(self->values);
        free(self->key_lens);
        Py_DECREF(self);
        return PyErr_NoMemory();
    }

    return (PyObject *)self;
}

static void
AhoCorasick_dealloc(AhoCorasickObject *self)
{
    for (int i = 0; i < self->n_values; i++) {
        Py_XDECREF(self->values[i]);
    }
    free(self->values);
    free(self->key_lens);
    free(self->nodes);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

/* ------------------------------------------------------------------ */
/* add_word(key: str, value: object)                                  */
/* ------------------------------------------------------------------ */

static PyObject *
AhoCorasick_add_word(AhoCorasickObject *self, PyObject *args)
{
    const char *key;
    Py_ssize_t key_len;
    PyObject *value;

    if (!PyArg_ParseTuple(args, "s#O", &key, &key_len, &value))
        return NULL;

    if (self->built) {
        PyErr_SetString(PyExc_RuntimeError,
                        "cannot add_word after make_automaton()");
        return NULL;
    }

    int cur = 0;  /* root */
    for (Py_ssize_t i = 0; i < key_len; i++) {
        unsigned char c = (unsigned char)key[i];
        if (self->nodes[cur].children[c] < 0) {
            int nid = ac_new_node(self);
            if (nid < 0) return PyErr_NoMemory();
            self->nodes[cur].children[c] = nid;
        }
        cur = self->nodes[cur].children[c];
    }

    /* Store value at terminal node */
    int vid = ac_new_value(self, value, (int)key_len);
    if (vid < 0) return PyErr_NoMemory();
    self->nodes[cur].output = vid;

    Py_RETURN_NONE;
}

/* ------------------------------------------------------------------ */
/* make_automaton() — build fail + dict_suffix links via BFS          */
/* ------------------------------------------------------------------ */

static PyObject *
AhoCorasick_make_automaton(AhoCorasickObject *self, PyObject *Py_UNUSED(ignored))
{
    if (self->built) {
        PyErr_SetString(PyExc_RuntimeError, "automaton already built");
        return NULL;
    }

    int n = self->n_nodes;
    ACNode *nodes = self->nodes;

    /* BFS queue (at most n entries) */
    int *queue = (int *)malloc(sizeof(int) * (size_t)n);
    if (!queue) return PyErr_NoMemory();
    int head = 0, tail = 0;

    /* Seed BFS: children of root have fail = 0 */
    for (int c = 0; c < AC_ALPHA; c++) {
        int child = nodes[0].children[c];
        if (child > 0) {
            nodes[child].fail = 0;
            nodes[child].dict_suffix = -1;
            queue[tail++] = child;
        }
    }

    /* BFS */
    while (head < tail) {
        int u = queue[head++];
        for (int c = 0; c < AC_ALPHA; c++) {
            int v = nodes[u].children[c];
            if (v < 0) continue;

            /* Compute fail link */
            int f = nodes[u].fail;
            while (f > 0 && nodes[f].children[c] < 0)
                f = nodes[f].fail;
            if (nodes[f].children[c] >= 0 && nodes[f].children[c] != v)
                f = nodes[f].children[c];
            nodes[v].fail = f;

            /* Compute dict_suffix */
            if (nodes[f].output >= 0)
                nodes[v].dict_suffix = f;
            else
                nodes[v].dict_suffix = nodes[f].dict_suffix;

            queue[tail++] = v;
        }
    }

    free(queue);
    self->built = 1;
    Py_RETURN_NONE;
}

/* ------------------------------------------------------------------ */
/* iter(text: str) -> list[tuple[int, value]]                         */
/* ------------------------------------------------------------------ */

static PyObject *
AhoCorasick_iter(AhoCorasickObject *self, PyObject *args)
{
    const char *text;
    Py_ssize_t text_len;

    if (!PyArg_ParseTuple(args, "s#", &text, &text_len))
        return NULL;

    if (!self->built) {
        PyErr_SetString(PyExc_RuntimeError,
                        "call make_automaton() before iter()");
        return NULL;
    }

    PyObject *result = PyList_New(0);
    if (!result) return NULL;

    ACNode *nodes = self->nodes;
    int state = 0;

    for (Py_ssize_t i = 0; i < text_len; i++) {
        unsigned char c = (unsigned char)text[i];

        /* Follow fail links until we can advance or reach root */
        while (state > 0 && nodes[state].children[c] < 0)
            state = nodes[state].fail;
        if (nodes[state].children[c] >= 0)
            state = nodes[state].children[c];

        /* Collect outputs from this state + dict_suffix chain */
        int tmp = state;
        while (tmp > 0) {
            if (nodes[tmp].output >= 0) {
                int vid = nodes[tmp].output;
                PyObject *tuple = Py_BuildValue("(nO)", (Py_ssize_t)i,
                                                self->values[vid]);
                if (!tuple) {
                    Py_DECREF(result);
                    return NULL;
                }
                if (PyList_Append(result, tuple) < 0) {
                    Py_DECREF(tuple);
                    Py_DECREF(result);
                    return NULL;
                }
                Py_DECREF(tuple);
            }
            tmp = nodes[tmp].dict_suffix;
        }
    }

    return result;
}

/* ------------------------------------------------------------------ */
/* Type definition                                                    */
/* ------------------------------------------------------------------ */

static PyMethodDef AhoCorasick_methods[] = {
    {"add_word", (PyCFunction)AhoCorasick_add_word, METH_VARARGS,
     "add_word(key: str, value: object) — insert pattern into trie"},
    {"make_automaton", (PyCFunction)AhoCorasick_make_automaton, METH_NOARGS,
     "make_automaton() — build failure and dict-suffix links"},
    {"iter", (PyCFunction)AhoCorasick_iter, METH_VARARGS,
     "iter(text: str) -> list[(end_index, value)] — find all matches"},
    {NULL, NULL, 0, NULL}
};

static PyTypeObject AhoCorasickType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "dux._matcher.AhoCorasick",
    .tp_basicsize = sizeof(AhoCorasickObject),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_doc = "Aho-Corasick automaton for multi-pattern string matching.",
    .tp_new = AhoCorasick_new,
    .tp_dealloc = (destructor)AhoCorasick_dealloc,
    .tp_methods = AhoCorasick_methods,
};

/* ------------------------------------------------------------------ */
/* Module definition (multi-phase init for free-threaded compat)      */
/* ------------------------------------------------------------------ */

static int
matcher_exec(PyObject *m)
{
    if (PyType_Ready(&AhoCorasickType) < 0)
        return -1;
    if (PyModule_AddObjectRef(m, "AhoCorasick",
                              (PyObject *)&AhoCorasickType) < 0)
        return -1;
    return 0;
}

static PyModuleDef_Slot matcher_slots[] = {
    {Py_mod_exec, matcher_exec},
#ifdef Py_GIL_DISABLED
    {Py_mod_gil, Py_MOD_GIL_NOT_USED},
#endif
    {0, NULL}
};

static struct PyModuleDef matcher_module = {
    PyModuleDef_HEAD_INIT,
    .m_name = "dux._matcher",
    .m_doc = "Custom Aho-Corasick automaton (GIL-free).",
    .m_size = 0,
    .m_slots = matcher_slots,
};

PyMODINIT_FUNC
PyInit__matcher(void)
{
    return PyModuleDef_Init(&matcher_module);
}
