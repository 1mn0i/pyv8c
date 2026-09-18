#ifndef V8N_BOUNDARY_GUARD_H
#define V8N_BOUNDARY_GUARD_H

// The Python/native boundary: the only place that owns a borrowed reference,
// raises an error, or formats one.  Everything above this file works with
// owned references and bool returns.

#include <Python.h>

#include <utility>

namespace v8n
{

// A reference that is released on scope exit.
//
// Conversions here fail in the middle of building a result, and the previous
// binding handled that with three different conventions: a return-value macro,
// goto labels, and hand written decrefs.  A single owning wrapper makes the
// failure path identical everywhere -- the objects release themselves -- which
// is what lets the rest of this codebase return plain bool and pointers.
class PyRef
{
public:
    PyRef() = default;

    // Adopt a new reference.
    static PyRef adopt(PyObject* value) { return PyRef(value); }

    // Take a borrowed reference and keep it alive.
    static PyRef borrow(PyObject* value)
    {
        Py_XINCREF(value);
        return PyRef(value);
    }

    PyRef(const PyRef& other) : value_(other.value_) { Py_XINCREF(value_); }

    PyRef(PyRef&& other) noexcept : value_(other.value_) { other.value_ = nullptr; }

    PyRef& operator=(const PyRef& other)
    {
        if (this != &other)
            assign(PyRef(other));
        return *this;
    }

    PyRef& operator=(PyRef&& other) noexcept
    {
        if (this != &other)
            assign(std::move(other));
        return *this;
    }

    ~PyRef() { Py_XDECREF(value_); }

    PyObject* get() const { return value_; }
    explicit operator bool() const { return value_ != nullptr; }

    // Hand the owned reference out as a new reference, for example from a
    // tp_ slot.
    PyObject* release()
    {
        PyObject* value = value_;
        value_ = nullptr;
        return value;
    }

    void reset(PyObject* value = nullptr) { assign(PyRef::adopt(value)); }

private:
    explicit PyRef(PyObject* value) : value_(value) {}

    void assign(PyRef&& incoming)
    {
        PyObject* previous = value_;
        value_ = incoming.value_;
        incoming.value_ = nullptr;
        Py_XDECREF(previous);
    }

    PyObject* value_ = nullptr;
};

// Raise with a formatted message and return nullptr, so a failing operation
// can be written as `return raise(...)`.
PyObject* raise(PyObject* exception, const char* format, ...);

// Raise an OSError for the given errno, appending the system message.
PyObject* raise_errno(int error, const char* format, ...);

// Raise TypeError naming the parameter that had the wrong shape.
PyObject* raise_expected(const char* function, const char* parameter,
                         const char* expected);

}  // namespace v8n

#endif
