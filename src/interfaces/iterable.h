#ifndef PAIR_ITERABLE_H
#define PAIR_ITERABLE_H

#include "bridge_base.h"
#include <v8.h>

#include "pyclass.h"

// An interface whose IdlSpec declares ``iterate`` gets keys, values, entries and
// ``@@iterator`` from the binding, the way Chromium's PairSyncIterable does.
bool install_pair_iterable(v8::Isolate* isolate, py_class* self,
                           v8::Local<v8::External> js_self,
                           v8::Local<v8::FunctionTemplate> interface_template,
                           v8::Local<v8::Signature> signature);

// The rest needs the prototype object, so it runs when a class is materialized in
// a context, beside delete_constructor.
void install_pair_iterator_prototype(py_class* self, v8::Local<v8::Context> context);

#endif
