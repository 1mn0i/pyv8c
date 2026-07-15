#include "isolate_scope.h"

#include "isolate_state.h"

static thread_local IsolateState* active_isolate_state = nullptr;

IsolateState*
current_isolate_state()
{
    return active_isolate_state;
}

IsolateLockScope::IsolateLockScope(IsolateState* state)
    : state_(state),
      previous_(current_isolate_state()),
      isolate_(state->isolate()),
      locker_(isolate_),
      isolate_scope_(isolate_)
{
    active_isolate_state = state_;
}

IsolateLockScope::~IsolateLockScope()
{
    active_isolate_state = previous_;
}
