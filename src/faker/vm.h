#ifndef FAKER_VM_H
#define FAKER_VM_H

#include <assert.h>
#include <fcntl.h>

#include <v8.h>
#include <Python.h>

#include "tools.h"

#include "Faker.h"
#include "env/Window.h"


using namespace v8;

namespace faker {

    void Register(Isolate* isolate, Local<FunctionTemplate>& global_inteface_template);

} // namespace faker

#endif