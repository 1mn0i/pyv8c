#include "realm/resources.h"

#include <new>

namespace v8n
{

ResourceRegistry::~ResourceRegistry()
{
    // The nodes go, the references stay: a registry that is being destroyed is
    // destroyed as part of a teardown that has already released them, and
    // releasing them twice would be a double decref.  clear() is the one that
    // releases; this only forgets where they were.
    for (Slot* slot = head_; slot != nullptr;)
    {
        Slot* next = slot->next;
        delete slot;
        slot = next;
    }
    head_ = nullptr;
    size_ = 0;
}

bool
ResourceRegistry::add(PyObject** slot)
{
    if (slot == nullptr)
        return false;

    for (Slot* current = head_; current != nullptr; current = current->next)
    {
        if (current->slot == slot)
            return true;
    }

    auto* entry = new (std::nothrow) Slot();
    if (entry == nullptr)
    {
        PyErr_NoMemory();
        return false;
    }
    entry->slot = slot;
    entry->next = head_;
    head_ = entry;
    ++size_;
    return true;
}

void
ResourceRegistry::remove(PyObject** slot)
{
    Slot** link = &head_;
    while (*link != nullptr)
    {
        Slot* current = *link;
        if (current->slot == slot)
        {
            *link = current->next;
            delete current;
            --size_;
            return;
        }
        link = &current->next;
    }
}

int
ResourceRegistry::traverse(visitproc visit, void* arg) const
{
    for (Slot* current = head_; current != nullptr; current = current->next)
    {
        if (current->slot == nullptr || *current->slot == nullptr)
            continue;

        const int result = visit(*current->slot, arg);
        if (result != 0)
            return result;
    }
    return 0;
}

std::size_t
ResourceRegistry::clear()
{
    // Detached first.  Releasing a reference can run a finalizer, and a
    // finalizer that registers a slot would otherwise be handed a list that this
    // loop is freeing node by node.
    Slot* list = head_;
    head_ = nullptr;
    size_ = 0;

    std::size_t released = 0;
    while (list != nullptr)
    {
        Slot* next = list->next;
        PyObject** slot = list->slot;
        delete list;
        list = next;

        if (slot == nullptr || *slot == nullptr)
            continue;
        PyObject* value = *slot;
        *slot = nullptr;
        Py_DECREF(value);
        ++released;
    }
    return released;
}

}  // namespace v8n
