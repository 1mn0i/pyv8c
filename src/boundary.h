#ifndef PYV8_BOUNDARY_H
#define PYV8_BOUNDARY_H
#include <Python.h>
#include <new>
#include <type_traits>

// Unwind legacy entry macros without replacing the Python error already set.
struct PythonEntryFailure {};

inline void translate_native_exception() noexcept {
    try { throw; }
    catch (const PythonEntryFailure&) {
        if (!PyErr_Occurred())
            PyErr_SetString(PyExc_RuntimeError, "failed to enter V8 isolate");
    }
    catch (const std::bad_alloc&) { PyErr_NoMemory(); }
    catch (...) { PyErr_SetString(PyExc_RuntimeError, "native bridge operation failed"); }
}

template<auto Function> struct PythonBoundary;
template<class R, class... Args, R (*Function)(Args...)>
struct PythonBoundary<Function> {
    static R call(Args... args) noexcept {
        try { return Function(args...); }
        catch (...) {
            translate_native_exception();
            if constexpr (std::is_void_v<R>) { PyErr_WriteUnraisable(Py_None); return; }
            else if constexpr (std::is_pointer_v<R>) return nullptr;
            else return static_cast<R>(-1);
        }
    }
};
#endif
