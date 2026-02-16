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
 * Single-directory scanners with GIL released during I/O.
 *
 * Exported Python functions:
 *   scan_dir_nodes(path, parent, leaf, kind_dir, kind_file, ScanNode_cls)
 *     -> (dir_nodes, file_count, dir_count, error_count)
 *
 *   scan_dir_bulk_nodes(...)   [macOS only, uses getattrlistbulk]
 */

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

/* ------------------------------------------------------------------ */
/* Entry buffer: collects results from GIL-free I/O                   */
/* ------------------------------------------------------------------ */

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
/* scan_dir_bulk_nodes: macOS getattrlistbulk                         */
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
    {"scan_dir_nodes", walker_scan_dir_nodes, METH_VARARGS,
     "scan_dir_nodes(path, parent, leaf, kind_dir, kind_file, ScanNode_cls)\n"
     "  -> (dir_nodes, file_count, dir_count, error_count)\n\n"
     "Scan a directory, create ScanNode objects directly, append to parent.children.\n"
     "GIL released during I/O."},
#ifdef __APPLE__
    {"scan_dir_bulk_nodes", walker_scan_dir_bulk_nodes, METH_VARARGS,
     "scan_dir_bulk_nodes(path, parent, leaf, kind_dir, kind_file, ScanNode_cls)\n"
     "  -> (dir_nodes, file_count, dir_count, error_count)\n\n"
     "Scan a directory using macOS getattrlistbulk, creating ScanNode objects directly."},
#endif
    {NULL, NULL, 0, NULL}
};

#ifdef Py_GIL_DISABLED
static PyModuleDef_Slot walker_slots[] = {
    {Py_mod_gil, Py_MOD_GIL_NOT_USED},
    {0, NULL}
};
#endif

static struct PyModuleDef walker_module = {
    PyModuleDef_HEAD_INIT,
    .m_name = "dux._walker",
    .m_doc = "Fast C directory scanner for dux.",
    .m_size = 0,
    .m_methods = walker_methods,
#ifdef Py_GIL_DISABLED
    .m_slots = walker_slots,
#endif
};

PyMODINIT_FUNC
PyInit__walker(void)
{
    return PyModuleDef_Init(&walker_module);
}
