#include <v8.h>

#include "pyv8.h"
#include "kappa.h"

#define DEFINE_MEME(name, string) Persistent<String> name##p;
MAGIC_CONSTANT_STRING_LIST_KAPPA(DEFINE_MEME)
#undef DEFINE_MEME


void create_memes_plz_thx() {
    IN_V8;
#define CREATE_MEME(name, string) name##p.Reset(isolate, JSTR(string));
    MAGIC_CONSTANT_STRING_LIST_KAPPA(CREATE_MEME)
#undef CREATE_MEME
}

void reset_memes_plz_thx() {
#define RESET_MEME(name, string) name##p.Reset();
    MAGIC_CONSTANT_STRING_LIST_KAPPA(RESET_MEME)
#undef RESET_MEME
}
