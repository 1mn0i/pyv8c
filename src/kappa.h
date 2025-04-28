
#ifndef KAPPA_KAPPA_KAPPA
#define KAPPA_KAPPA_KAPPA

#include <Python.h>
#include <v8.h>

#define MAGIC_CONSTANT_STRING_LIST_KAPPA(V) \
V(I_CAN_HAZ_ERROR_PROTOTYPE, "Error Prototype Will Appear Here") \
V(IZ_DAT_OBJECT, "A wild object appeared!")

#define DECLARE_MAGIC(name, string) extern v8::Persistent<v8::String> name##p;
MAGIC_CONSTANT_STRING_LIST_KAPPA(DECLARE_MAGIC)
#undef DECLARE_MAGIC

// You can't go so far as to define a macro in a macro
#define I_CAN_HAZ_ERROR_PROTOTYPE I_CAN_HAZ_ERROR_PROTOTYPEp.Get(isolate)
#define IZ_DAT_OBJECT IZ_DAT_OBJECTp.Get(isolate)

// boring function prototype
void create_memes_plz_thx();


#endif
