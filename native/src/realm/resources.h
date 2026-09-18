#ifndef V8N_REALM_RESOURCES_H
#define V8N_REALM_RESOURCES_H

// The realm's Python root set.
//
// A realm holds Python references on behalf of JavaScript: wrapper records for
// host objects, the uncaught-error and rejection hooks, microtask callables, the
// trace hook.  Every one of them has to be reachable from the realm's own
// tp_traverse, or a cycle through a host object keeps the realm alive forever;
// and every one of them has to be dropped at close.
//
// Rather than have each feature remember to do both, a feature registers the
// address of the field that owns its reference.  Traversal then reads whatever
// the field holds right now, and teardown clears it, so there is no second copy
// of the truth to keep in step.  The cost of that choice is a rule: a registered
// field's address must not move, which is why realms are heap allocated and
// never copied.

#include <Python.h>

#include <cstddef>

namespace v8n
{

class ResourceRegistry
{
public:
    ResourceRegistry() = default;
    ~ResourceRegistry();

    ResourceRegistry(const ResourceRegistry&) = delete;
    ResourceRegistry& operator=(const ResourceRegistry&) = delete;

    // Registers the address of a slot that owns a Python reference.  The slot
    // must outlive its registration.  Registering the same slot twice is not an
    // error, so a feature can register defensively on every use.
    bool add(PyObject** slot);
    void remove(PyObject** slot);

    // Visits every slot that currently holds a reference, in the tp_traverse
    // protocol.  Returns the first non-zero result, as visitproc requires.
    int traverse(visitproc visit, void* arg) const;

    std::size_t size() const { return size_; }

    // Releases every reference and forgets every slot.  Returns how many
    // references were released.  The list is detached before the first release,
    // because releasing can run a finalizer, and a finalizer that registers a
    // new slot must not walk a list that is being freed underneath it.
    std::size_t clear();

private:
    struct Slot
    {
        Slot* next = nullptr;
        PyObject** slot = nullptr;
    };

    Slot* head_ = nullptr;
    std::size_t size_ = 0;
};

}  // namespace v8n

#endif
