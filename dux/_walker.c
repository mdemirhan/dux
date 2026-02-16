#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <dirent.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#ifdef __APPLE__
#include <sys/attr.h>
#include <sys/vnode.h>
#include <fcntl.h>
#include <unistd.h>
#endif

/*
 * Iterative directory walker using opendir/readdir/lstat.
 *
 * Returns a flat list of (path, name, is_dir, size, disk_usage) tuples
 * plus counts (files, dirs, errors).
 *
 * Python signature:
 *   walk(root: str, max_depth: int, progress_cb: Callable | None, cancel_cb: Callable | None)
 *     -> tuple[list[tuple[str, str, bool, int, int]], int, int, int] | None
 */

/* Explicit stack frame for iterative traversal */
typedef struct {
    char *path;
    int depth;
} StackFrame;

/* Growable stack */
typedef struct {
    StackFrame *frames;
    Py_ssize_t size;
    Py_ssize_t capacity;
} Stack;

static int
stack_init(Stack *s, Py_ssize_t initial_capacity)
{
    s->frames = (StackFrame *)malloc(sizeof(StackFrame) * initial_capacity);
    if (!s->frames) return -1;
    s->size = 0;
    s->capacity = initial_capacity;
    return 0;
}

static int
stack_push(Stack *s, const char *path, int depth)
{
    if (s->size >= s->capacity) {
        Py_ssize_t new_cap = s->capacity * 2;
        StackFrame *new_frames = (StackFrame *)realloc(s->frames, sizeof(StackFrame) * new_cap);
        if (!new_frames) return -1;
        s->frames = new_frames;
        s->capacity = new_cap;
    }
    s->frames[s->size].path = strdup(path);
    if (!s->frames[s->size].path) return -1;
    s->frames[s->size].depth = depth;
    s->size++;
    return 0;
}

static StackFrame
stack_pop(Stack *s)
{
    s->size--;
    return s->frames[s->size];
}

static void
stack_free(Stack *s)
{
    for (Py_ssize_t i = 0; i < s->size; i++) {
        free(s->frames[i].path);
    }
    free(s->frames);
    s->frames = NULL;
    s->size = 0;
    s->capacity = 0;
}

/* Build full child path: parent + "/" + name */
static char *
join_path(const char *parent, const char *name)
{
    size_t plen = strlen(parent);
    size_t nlen = strlen(name);
    /* Remove trailing slash from parent if present (unless root "/") */
    int needs_slash = (plen > 0 && parent[plen - 1] != '/');
    size_t total = plen + needs_slash + nlen + 1;
    char *buf = (char *)malloc(total);
    if (!buf) return NULL;
    memcpy(buf, parent, plen);
    if (needs_slash) buf[plen] = '/';
    memcpy(buf + plen + needs_slash, name, nlen);
    buf[total - 1] = '\0';
    return buf;
}

static PyObject *
walker_walk(PyObject *self, PyObject *args)
{
    (void)self;
    const char *root_path;
    int max_depth;
    PyObject *progress_cb;
    PyObject *cancel_cb;

    if (!PyArg_ParseTuple(args, "siOO", &root_path, &max_depth, &progress_cb, &cancel_cb))
        return NULL;

    if (progress_cb == Py_None) progress_cb = NULL;
    if (cancel_cb == Py_None) cancel_cb = NULL;

    PyObject *result_list = PyList_New(0);
    if (!result_list) return NULL;

    Stack stack;
    if (stack_init(&stack, 256) < 0) {
        Py_DECREF(result_list);
        return PyErr_NoMemory();
    }

    if (stack_push(&stack, root_path, 0) < 0) {
        stack_free(&stack);
        Py_DECREF(result_list);
        return PyErr_NoMemory();
    }

    long long file_count = 0;
    long long dir_count = 0;
    long long error_count = 0;
    long long entry_counter = 0;

    while (stack.size > 0) {
        StackFrame frame = stack_pop(&stack);
        DIR *dp = opendir(frame.path);
        if (!dp) {
            error_count++;
            free(frame.path);
            continue;
        }

        struct dirent *ep;
        while ((ep = readdir(dp)) != NULL) {
            /* Skip . and .. */
            if (ep->d_name[0] == '.') {
                if (ep->d_name[1] == '\0') continue;
                if (ep->d_name[1] == '.' && ep->d_name[2] == '\0') continue;
            }

            char *child_path = join_path(frame.path, ep->d_name);
            if (!child_path) {
                closedir(dp);
                free(frame.path);
                stack_free(&stack);
                Py_DECREF(result_list);
                return PyErr_NoMemory();
            }

            struct stat st;
            if (lstat(child_path, &st) < 0) {
                error_count++;
                free(child_path);
                continue;
            }

            int is_dir = S_ISDIR(st.st_mode);
            long long size = is_dir ? 0 : (long long)st.st_size;
            long long disk_usage = is_dir ? 0 : (long long)st.st_blocks * 512;

            PyObject *tuple = Py_BuildValue(
                "(ssNLL)",
                child_path,
                ep->d_name,
                PyBool_FromLong(is_dir),
                size,
                disk_usage
            );
            if (!tuple) {
                free(child_path);
                closedir(dp);
                free(frame.path);
                stack_free(&stack);
                Py_DECREF(result_list);
                return NULL;
            }

            if (PyList_Append(result_list, tuple) < 0) {
                Py_DECREF(tuple);
                free(child_path);
                closedir(dp);
                free(frame.path);
                stack_free(&stack);
                Py_DECREF(result_list);
                return NULL;
            }
            Py_DECREF(tuple);

            if (is_dir) {
                dir_count++;
                int within_depth = (max_depth < 0) || (frame.depth < max_depth);
                if (within_depth) {
                    if (stack_push(&stack, child_path, frame.depth + 1) < 0) {
                        free(child_path);
                        closedir(dp);
                        free(frame.path);
                        stack_free(&stack);
                        Py_DECREF(result_list);
                        return PyErr_NoMemory();
                    }
                }
            } else {
                file_count++;
            }

            free(child_path);
            entry_counter++;

            /* Every 1000 entries: progress + cancel check */
            if (entry_counter % 1000 == 0) {
                if (cancel_cb) {
                    PyObject *cancel_result = PyObject_CallNoArgs(cancel_cb);
                    if (!cancel_result) {
                        closedir(dp);
                        free(frame.path);
                        stack_free(&stack);
                        Py_DECREF(result_list);
                        return NULL;
                    }
                    int cancelled = PyObject_IsTrue(cancel_result);
                    Py_DECREF(cancel_result);
                    if (cancelled) {
                        closedir(dp);
                        free(frame.path);
                        stack_free(&stack);
                        Py_DECREF(result_list);
                        Py_RETURN_NONE;
                    }
                }
                if (progress_cb) {
                    PyObject *prog_result = PyObject_CallFunction(
                        progress_cb, "sLL",
                        frame.path, file_count, dir_count
                    );
                    if (!prog_result) {
                        closedir(dp);
                        free(frame.path);
                        stack_free(&stack);
                        Py_DECREF(result_list);
                        return NULL;
                    }
                    Py_DECREF(prog_result);
                }
            }
        }

        closedir(dp);
        free(frame.path);
    }

    stack_free(&stack);

    PyObject *result = Py_BuildValue("(OLLL)", result_list, file_count, dir_count, error_count);
    Py_DECREF(result_list);
    return result;
}

/* ------------------------------------------------------------------ */
/* scan_dir: scan a single directory with GIL released during I/O     */
/* ------------------------------------------------------------------ */

/* Pre-allocated entry buffer for scan_dir (avoids malloc per entry) */
typedef struct {
    char *path;     /* full child path (heap-allocated) */
    char *name;     /* points into *path* after last '/' */
    int is_dir;
    long long size;
    long long disk_usage;
} ScanDirEntry;

typedef struct {
    ScanDirEntry *entries;
    Py_ssize_t size;
    Py_ssize_t capacity;
} EntryBuf;

static int
entrybuf_init(EntryBuf *b, Py_ssize_t cap)
{
    b->entries = (ScanDirEntry *)malloc(sizeof(ScanDirEntry) * cap);
    if (!b->entries) return -1;
    b->size = 0;
    b->capacity = cap;
    return 0;
}

static int
entrybuf_push(EntryBuf *b, char *path, char *name, int is_dir,
              long long size, long long disk_usage)
{
    if (b->size >= b->capacity) {
        Py_ssize_t new_cap = b->capacity * 2;
        ScanDirEntry *nw = (ScanDirEntry *)realloc(
            b->entries, sizeof(ScanDirEntry) * new_cap);
        if (!nw) return -1;
        b->entries = nw;
        b->capacity = new_cap;
    }
    ScanDirEntry *e = &b->entries[b->size];
    e->path = path;
    e->name = name;
    e->is_dir = is_dir;
    e->size = size;
    e->disk_usage = disk_usage;
    b->size++;
    return 0;
}

static void
entrybuf_free(EntryBuf *b)
{
    for (Py_ssize_t i = 0; i < b->size; i++) {
        free(b->entries[i].path);
    }
    free(b->entries);
    b->entries = NULL;
    b->size = 0;
    b->capacity = 0;
}

/* ------------------------------------------------------------------ */
/* GIL-free I/O helpers                                               */
/* ------------------------------------------------------------------ */

/* Fill EntryBuf via opendir/readdir/lstat (no GIL needed). */
static long long
_fill_buf_readdir(const char *dir_path, EntryBuf *buf)
{
    long long error_count = 0;

    DIR *dp = opendir(dir_path);
    if (dp) {
        struct dirent *ep;
        while ((ep = readdir(dp)) != NULL) {
            if (ep->d_name[0] == '.') {
                if (ep->d_name[1] == '\0') continue;
                if (ep->d_name[1] == '.' && ep->d_name[2] == '\0') continue;
            }

            char *child_path = join_path(dir_path, ep->d_name);
            if (!child_path) break;

            struct stat st;
            if (lstat(child_path, &st) < 0) {
                error_count++;
                free(child_path);
                continue;
            }

            int is_dir = S_ISDIR(st.st_mode);
            long long size = is_dir ? 0 : (long long)st.st_size;
            long long disk_usage = is_dir ? 0 : (long long)st.st_blocks * 512;

            size_t plen = strlen(dir_path);
            char *name = child_path + plen;
            if (*name == '/') name++;

            if (entrybuf_push(buf, child_path, name, is_dir,
                              size, disk_usage) < 0) {
                free(child_path);
                break;
            }
        }
        closedir(dp);
    } else {
        error_count++;
    }

    return error_count;
}

/* ------------------------------------------------------------------ */
/* Node builder: convert EntryBuf into ScanNode objects               */
/* ------------------------------------------------------------------ */

/*
 * Iterate EntryBuf, create ScanNode per entry, append to parent.children,
 * and collect directory nodes.
 *
 * Returns (dir_nodes, file_count, dir_count, error_count) as a Python tuple.
 */
static PyObject *
_build_nodes_from_buf(EntryBuf *buf, long long err_count,
                      PyObject *parent, PyObject *leaf,
                      PyObject *kind_dir, PyObject *kind_file,
                      PyObject *ScanNode_cls)
{
    PyObject *parent_children = PyObject_GetAttrString(parent, "children");
    if (!parent_children) return NULL;

    PyObject *dir_nodes = PyList_New(0);
    if (!dir_nodes) {
        Py_DECREF(parent_children);
        return NULL;
    }

    long long file_count = 0;
    long long dir_count = 0;

    for (Py_ssize_t i = 0; i < buf->size; i++) {
        ScanDirEntry *e = &buf->entries[i];
        PyObject *node;

        if (e->is_dir) {
            PyObject *children = PyList_New(0);
            if (!children) goto error;
            /* N steals ref to children */
            node = PyObject_CallFunction(ScanNode_cls, "ssOLLN",
                                         e->path, e->name, kind_dir,
                                         (long long)0, (long long)0, children);
        } else {
            node = PyObject_CallFunction(ScanNode_cls, "ssOLLO",
                                         e->path, e->name, kind_file,
                                         e->size, e->disk_usage, leaf);
        }

        if (!node) goto error;

        if (PyList_Append(parent_children, node) < 0) {
            Py_DECREF(node);
            goto error;
        }

        if (e->is_dir) {
            dir_count++;
            if (PyList_Append(dir_nodes, node) < 0) {
                Py_DECREF(node);
                goto error;
            }
        } else {
            file_count++;
        }

        Py_DECREF(node);
    }

    Py_DECREF(parent_children);
    return Py_BuildValue("(NLLL)", dir_nodes, file_count, dir_count, err_count);

error:
    Py_DECREF(parent_children);
    Py_DECREF(dir_nodes);
    return NULL;
}

/* ------------------------------------------------------------------ */
/* scan_dir: legacy tuple-returning variant (kept for compat)         */
/* ------------------------------------------------------------------ */

static PyObject *
walker_scan_dir(PyObject *self, PyObject *args)
{
    (void)self;
    const char *dir_path;

    if (!PyArg_ParseTuple(args, "s", &dir_path))
        return NULL;

    EntryBuf buf;
    if (entrybuf_init(&buf, 128) < 0)
        return PyErr_NoMemory();

    long long error_count;

    Py_BEGIN_ALLOW_THREADS
    error_count = _fill_buf_readdir(dir_path, &buf);
    Py_END_ALLOW_THREADS

    /* Build Python list from C buffer */
    PyObject *result_list = PyList_New(buf.size);
    if (!result_list) {
        entrybuf_free(&buf);
        return NULL;
    }

    for (Py_ssize_t i = 0; i < buf.size; i++) {
        ScanDirEntry *e = &buf.entries[i];
        PyObject *tuple = Py_BuildValue(
            "(ssNLL)",
            e->path,
            e->name,
            PyBool_FromLong(e->is_dir),
            e->size,
            e->disk_usage
        );
        if (!tuple) {
            Py_DECREF(result_list);
            entrybuf_free(&buf);
            return NULL;
        }
        PyList_SET_ITEM(result_list, i, tuple);  /* steals ref */
    }

    entrybuf_free(&buf);

    return Py_BuildValue("(NL)", result_list, error_count);
}

/* ------------------------------------------------------------------ */
/* scan_dir_nodes: create ScanNode objects directly in C              */
/* ------------------------------------------------------------------ */

static PyObject *
walker_scan_dir_nodes(PyObject *self, PyObject *args)
{
    (void)self;
    const char *dir_path;
    PyObject *parent, *leaf, *kind_dir, *kind_file, *ScanNode_cls;

    if (!PyArg_ParseTuple(args, "sOOOOO", &dir_path, &parent, &leaf,
                          &kind_dir, &kind_file, &ScanNode_cls))
        return NULL;

    EntryBuf buf;
    if (entrybuf_init(&buf, 128) < 0)
        return PyErr_NoMemory();

    long long error_count;

    Py_BEGIN_ALLOW_THREADS
    error_count = _fill_buf_readdir(dir_path, &buf);
    Py_END_ALLOW_THREADS

    PyObject *result = _build_nodes_from_buf(&buf, error_count, parent, leaf,
                                              kind_dir, kind_file, ScanNode_cls);
    entrybuf_free(&buf);
    return result;
}

/* ------------------------------------------------------------------ */
/* scan_dir_bulk: macOS getattrlistbulk for single-syscall stat+readdir */
/* ------------------------------------------------------------------ */

#ifdef __APPLE__

/* Attribute request: name, obj_type, file data length, file alloc size */
typedef struct {
    uint32_t       length;
    attribute_set_t returned;
    /* variable-length entries follow */
} BulkAttrBuf;

/* Fill EntryBuf via getattrlistbulk (no GIL needed). */
static long long
_fill_buf_bulk(const char *dir_path, EntryBuf *buf)
{
    long long error_count = 0;

    int fd = open(dir_path, O_RDONLY | O_DIRECTORY);
    if (fd >= 0) {
        struct attrlist alist;
        memset(&alist, 0, sizeof(alist));
        alist.bitmapcount = ATTR_BIT_MAP_COUNT;
        alist.commonattr  = ATTR_CMN_RETURNED_ATTRS | ATTR_CMN_NAME | ATTR_CMN_OBJTYPE;
        alist.fileattr    = ATTR_FILE_DATALENGTH | ATTR_FILE_ALLOCSIZE;

        char attrbuf[256 * 1024];
        int count;

        while ((count = getattrlistbulk(fd, &alist, attrbuf,
                                         sizeof(attrbuf), 0)) > 0) {
            char *cursor = attrbuf;
            for (int i = 0; i < count; i++) {
                uint32_t entry_length = *(uint32_t *)cursor;
                char *entry_start = cursor;
                cursor += sizeof(uint32_t);

                attribute_set_t returned = *(attribute_set_t *)cursor;
                cursor += sizeof(attribute_set_t);

                attrreference_t name_ref = *(attrreference_t *)cursor;
                char *name = ((char *)cursor) + name_ref.attr_dataoffset;
                cursor += sizeof(attrreference_t);

                fsobj_type_t obj_type = *(fsobj_type_t *)cursor;
                cursor += sizeof(fsobj_type_t);

                int is_dir = (obj_type == VDIR);
                long long size = 0;
                long long disk_usage = 0;

                if (returned.fileattr & ATTR_FILE_ALLOCSIZE) {
                    disk_usage = *(off_t *)cursor;
                    cursor += sizeof(off_t);
                }
                if (returned.fileattr & ATTR_FILE_DATALENGTH) {
                    size = *(off_t *)cursor;
                }

                /* Skip . and .. */
                if (name[0] == '.') {
                    if (name[1] == '\0') goto next_entry;
                    if (name[1] == '.' && name[2] == '\0') goto next_entry;
                }

                if (is_dir) {
                    size = 0;
                    disk_usage = 0;
                }

                {
                    char *child_path = join_path(dir_path, name);
                    if (!child_path) break;

                    size_t plen = strlen(dir_path);
                    char *name_ptr = child_path + plen;
                    if (*name_ptr == '/') name_ptr++;

                    if (entrybuf_push(buf, child_path, name_ptr,
                                      is_dir, size, disk_usage) < 0) {
                        free(child_path);
                        break;
                    }
                }

next_entry:
                cursor = entry_start + entry_length;
            }
        }

        if (count < 0) {
            error_count++;
        }

        close(fd);
    } else {
        error_count++;
    }

    return error_count;
}

/* Legacy tuple-returning variant (kept for compat) */
static PyObject *
walker_scan_dir_bulk(PyObject *self, PyObject *args)
{
    (void)self;
    const char *dir_path;

    if (!PyArg_ParseTuple(args, "s", &dir_path))
        return NULL;

    EntryBuf buf;
    if (entrybuf_init(&buf, 128) < 0)
        return PyErr_NoMemory();

    long long error_count;

    Py_BEGIN_ALLOW_THREADS
    error_count = _fill_buf_bulk(dir_path, &buf);
    Py_END_ALLOW_THREADS

    /* Build Python list from C buffer */
    PyObject *result_list = PyList_New(buf.size);
    if (!result_list) {
        entrybuf_free(&buf);
        return NULL;
    }

    for (Py_ssize_t i = 0; i < buf.size; i++) {
        ScanDirEntry *e = &buf.entries[i];
        PyObject *tuple = Py_BuildValue(
            "(ssNLL)",
            e->path,
            e->name,
            PyBool_FromLong(e->is_dir),
            e->size,
            e->disk_usage
        );
        if (!tuple) {
            Py_DECREF(result_list);
            entrybuf_free(&buf);
            return NULL;
        }
        PyList_SET_ITEM(result_list, i, tuple);
    }

    entrybuf_free(&buf);

    return Py_BuildValue("(NL)", result_list, error_count);
}

/* Create ScanNode objects directly in C */
static PyObject *
walker_scan_dir_bulk_nodes(PyObject *self, PyObject *args)
{
    (void)self;
    const char *dir_path;
    PyObject *parent, *leaf, *kind_dir, *kind_file, *ScanNode_cls;

    if (!PyArg_ParseTuple(args, "sOOOOO", &dir_path, &parent, &leaf,
                          &kind_dir, &kind_file, &ScanNode_cls))
        return NULL;

    EntryBuf buf;
    if (entrybuf_init(&buf, 128) < 0)
        return PyErr_NoMemory();

    long long error_count;

    Py_BEGIN_ALLOW_THREADS
    error_count = _fill_buf_bulk(dir_path, &buf);
    Py_END_ALLOW_THREADS

    PyObject *result = _build_nodes_from_buf(&buf, error_count, parent, leaf,
                                              kind_dir, kind_file, ScanNode_cls);
    entrybuf_free(&buf);
    return result;
}

#endif /* __APPLE__ */

static PyMethodDef walker_methods[] = {
    {"walk", walker_walk, METH_VARARGS,
     "walk(root, max_depth, progress_cb, cancel_cb) -> (entries, files, dirs, errors) | None\n\n"
     "Walk directory tree iteratively using opendir/readdir/lstat.\n"
     "max_depth < 0 means unlimited. Returns None if cancelled."},
    {"scan_dir", walker_scan_dir, METH_VARARGS,
     "scan_dir(path) -> (entries, error_count)\n\n"
     "Scan a single directory (non-recursive) with GIL released during I/O.\n"
     "Each entry is (path, name, is_dir, size, disk_usage)."},
    {"scan_dir_nodes", walker_scan_dir_nodes, METH_VARARGS,
     "scan_dir_nodes(path, parent, leaf, kind_dir, kind_file, ScanNode_cls)\n"
     "  -> (dir_nodes, file_count, dir_count, error_count)\n\n"
     "Scan a directory, create ScanNode objects directly, append to parent.children.\n"
     "GIL released during I/O."},
#ifdef __APPLE__
    {"scan_dir_bulk", walker_scan_dir_bulk, METH_VARARGS,
     "scan_dir_bulk(path) -> (entries, error_count)\n\n"
     "Scan a single directory using macOS getattrlistbulk (non-recursive).\n"
     "Returns name + stat in bulk syscalls. Same format as scan_dir."},
    {"scan_dir_bulk_nodes", walker_scan_dir_bulk_nodes, METH_VARARGS,
     "scan_dir_bulk_nodes(path, parent, leaf, kind_dir, kind_file, ScanNode_cls)\n"
     "  -> (dir_nodes, file_count, dir_count, error_count)\n\n"
     "Scan a directory using macOS getattrlistbulk, creating ScanNode objects directly."},
#endif
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef walker_module = {
    PyModuleDef_HEAD_INIT,
    "dux._walker",
    "Fast C directory walker for dux.",
    -1,
    walker_methods
};

PyMODINIT_FUNC
PyInit__walker(void)
{
    return PyModule_Create(&walker_module);
}
