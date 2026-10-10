#ifndef PYV8_ERROR_FAMILY_H
#define PYV8_ERROR_FAMILY_H

// pyv8.h first: it carries the MSVC shims V8's public headers expect.
#include "pyv8.h"

// The error hierarchy a realm may observe.  The table in the implementation is the only
// copy of the family: a host joins it by inheriting one of its types, and a class whose
// realm constructor is not a standard name declares its own shape with
// ``__v8_error_shape__`` -- (constructor name, constructor argument attributes, (python
// attribute, JavaScript property) pairs).  A class the family does not contain is the
// host's own failure, which no realm is ever shown.
namespace error_family {

//: Publishes the family and the interceptor marker on *module*.
bool publish(PyObject* module);

//: The JavaScript constructor an error of *exc_type* is built with; null when the family
//: does not hold that type, which is what makes the failure the host's own.  The walk is
//: taken per call: a table keyed by the type's address would answer for a type whose address
//: came back.
const char* family_name(PyTypeObject* exc_type);

//: Whether *exc_value* declares a failed legacy named or indexed write or delete.
bool is_interceptor_failure(PyObject* exc_value);

//: Whether *object* is one of this binding's API objects standing for a family error --
//: the question V8 asks when ``Error.isError`` meets a wrapper, answered from the Python
//: object behind it.
bool is_native_error_wrapper(v8::Isolate* isolate, v8::Local<v8::Object> object);

//: Gives *error* the stack a realm reads at ``error.stack``.  Callers are the ones that
//: make an error for a realm, never a page's own constructor.
void attach_stack(v8::Local<v8::Context> context, v8::Local<v8::Object> error);

//: Whether *exc_value* asks for that stack -- an instance a reader rebuilt from the wire
//: says no, since Chromium leaves no stack on the error it rebuilds either.
bool carries_stack(PyObject* exc_value);

//: The JavaScript error *exc_value* is shown as, built from its shape with the realm
//: constructor *name* names; empty when the realm offers no constructor to build it with.
v8::Local<v8::Object> build(v8::Isolate* isolate, v8::Local<v8::Context> context,
                            const char* name, PyObject* exc_value, const char* message);

//: The error a Python exception becomes when it crosses as a value rather than a throw:
//: itself if it already was one, built in the realm if the family holds it, nothing
//: otherwise.
v8::Local<v8::Object> exception_as_value(PyObject* value, v8::Local<v8::Context> context);

}  // namespace error_family

#endif
