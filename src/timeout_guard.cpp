#include "timeout_guard.h"

#include <Python.h>

#include "context.h"
#include "v8_compat.h"

#include <cerrno>

#ifdef _WIN32
#include <Windows.h>
#else
#include <pthread.h>
#endif

using namespace v8;

#ifdef _WIN32

static void CALLBACK
breaker_callback(UINT, UINT, DWORD_PTR user_data, DWORD_PTR, DWORD_PTR)
{
    Isolate* target = reinterpret_cast<Isolate*>(user_data);
    if (target != nullptr)
        target->TerminateExecution();
}

#else

static void*
breaker_thread(void* parameter)
{
    TimeoutGuard* guard = static_cast<TimeoutGuard*>(parameter);
    usleep(guard->timeout_usec());
    pthread_setcancelstate(PTHREAD_CANCEL_DISABLE, nullptr);
    guard->target_isolate()->TerminateExecution();
    return nullptr;
}

#endif

static double
context_timeout(Local<Context> context)
{
    context_c* py_context = static_cast<context_c*>(
        v8_compat::external_value(context->GetEmbedderData(CONTEXT_OBJECT_SLOT)));
    return py_context == nullptr ? 0 : py_context->timeout;
}

TimeoutGuard::TimeoutGuard(double timeout, Isolate* isolate)
    : timeout_(timeout),
      isolate_(isolate),
      active_(false)
#ifdef _WIN32
      , timer_id_(0)
#else
      , breaker_id_(),
      timeout_us_(0)
#endif
{
}

TimeoutGuard::TimeoutGuard(Local<Context> context)
    : TimeoutGuard(context_timeout(context), v8_compat::context_isolate(context))
{
}

TimeoutGuard::~TimeoutGuard()
{
    stop(false);
}

#ifndef _WIN32
useconds_t
TimeoutGuard::timeout_usec() const
{
    return timeout_us_;
}
#endif

bool
TimeoutGuard::start()
{
    if (timeout_ <= 0 || active_)
        return true;

#ifdef _WIN32
    UINT timeout_ms = static_cast<UINT>(timeout_ * 1000);
    if (timeout_ms == 0)
        timeout_ms = 1;
    timer_id_ = timeSetEvent(
        timeout_ms, 0, breaker_callback,
        reinterpret_cast<DWORD_PTR>(isolate_),
        TIME_ONESHOT | TIME_CALLBACK_FUNCTION | TIME_KILL_SYNCHRONOUS);
    if (timer_id_ == 0)
    {
        PyErr_SetFromErrno(PyExc_OSError);
        return false;
    }
#else
    timeout_us_ = static_cast<useconds_t>(timeout_ * 1000000);
    if (timeout_us_ == 0)
        timeout_us_ = 1;
    int error = pthread_create(&breaker_id_, nullptr, breaker_thread, this);
    if (error)
    {
        errno = error;
        PyErr_SetFromErrno(PyExc_OSError);
        return false;
    }
#endif
    active_ = true;
    return true;
}

bool
TimeoutGuard::stop()
{
    return stop(true);
}

bool
TimeoutGuard::stop(bool set_python_error)
{
    if (timeout_ <= 0 || !active_)
        return true;

#ifdef _WIN32
    timeKillEvent(timer_id_);
    timer_id_ = 0;
    active_ = false;
#else
    pthread_cancel(breaker_id_);
    int error = pthread_join(breaker_id_, nullptr);
    active_ = false;
    if (error)
    {
        if (set_python_error)
        {
            errno = error;
            PyErr_SetFromErrno(PyExc_OSError);
        }
        return false;
    }
#endif
    return true;
}
