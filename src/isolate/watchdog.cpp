#include "bridge_base.h"
#include "watchdog.h"

#include <algorithm>
#include <iterator>

#include "context.h"
#include "isolate_state.h"
#include "platform_state.h"
#include "runtime.h"
#include "v8_compat.h"

using namespace v8;

namespace
{
Watchdog* watchdog_for(Isolate* isolate)
{
    IsolateState* state = runtime_manager.find_isolate(isolate);
    return state == nullptr ? nullptr : &state->watchdog();
}
}

Watchdog::Watchdog(Isolate* isolate) : isolate_(isolate)
{
    deadlines_.reserve(8);
}

Watchdog::~Watchdog()
{
    shutdown();
}

void Watchdog::refresh_deadline_locked()
{
    Clock::time_point earliest = Clock::time_point::max();
    for (const auto& deadline : deadlines_)
        earliest = std::min(earliest, deadline.expires);
    deadline_ = earliest;
    armed_ = earliest != Clock::time_point::max();
}

void Watchdog::start_thread_locked()
{
    // Started once; a shutdown is final, and a second thread could never be joined.
    if (stopping_ || thread_.joinable())
        return;
    thread_ = std::thread(&Watchdog::run, this);
}

Watchdog::Ticket Watchdog::add_deadline_locked(double seconds, const void* owner)
{
    start_thread_locked();
    const bool was_idle = deadlines_.empty();
    const bool was_armed = armed_;
    const auto previous = deadline_;
    const auto ticket = next_ticket_++;
    deadlines_.push_back({ticket, seconds > 0
        ? Clock::now() + std::chrono::microseconds(static_cast<long long>(seconds * 1e6))
        : Clock::time_point::max(), owner});
    refresh_deadline_locked();
    if (was_idle || !was_armed || (armed_ && deadline_ < previous))
        wake_.notify_all();
    return ticket;
}

bool Watchdog::has_crossing_locked() const {
    return std::any_of(deadlines_.begin(), deadlines_.end(),
                       [](const Deadline& entry) { return entry.owner == nullptr; });
}

void Watchdog::arm(double seconds, const void* owner) {
    std::lock_guard<std::mutex> lock(mutex_);
    add_deadline_locked(seconds, owner);
}

void Watchdog::disarm(const void* owner) {
    std::unique_lock<std::mutex> lock(mutex_);
    wait_transition_locked(lock);
    auto found = std::find_if(deadlines_.rbegin(), deadlines_.rend(),
                             [owner](const Deadline& entry) { return entry.owner == owner; });
    if (found != deadlines_.rend()) {
        const auto removed = found->ticket;
        deadlines_.erase(std::next(found).base());
        if (termination_ == Termination::AbortPending && abort_ticket_ == removed)
            resume_locked(lock);
    }
    refresh_deadline_locked();
    wake_.notify_all();
}

void Watchdog::release_owner(const void* owner) {
    std::unique_lock<std::mutex> lock(mutex_);
    wait_transition_locked(lock);
    const bool cancels_abort = termination_ == Termination::AbortPending &&
        std::any_of(deadlines_.begin(), deadlines_.end(), [this, owner](const Deadline& entry) {
            return entry.owner == owner && entry.ticket == abort_ticket_;
        });
    std::erase_if(deadlines_, [owner](const Deadline& entry) { return entry.owner == owner; });
    if (cancels_abort) resume_locked(lock);
    refresh_deadline_locked();
    wake_.notify_all();
}

void Watchdog::wait_transition_locked(std::unique_lock<std::mutex>& lock) {
    wake_.wait(lock, [this] {
        return termination_ != Termination::Issuing &&
               termination_ != Termination::IssuingNext && termination_ != Termination::Resuming;
    });
}

void Watchdog::issue_termination_locked(std::unique_lock<std::mutex>& lock, bool pending, Ticket cause) {
    abort_ticket_ = pending ? cause : 0;
    termination_ = pending ? Termination::IssuingNext : Termination::Issuing;
    lock.unlock();
    isolate_->TerminateExecution();
    lock.lock();
    termination_ = pending ? Termination::AbortPending : Termination::Terminating;
    wake_.notify_all();
}

void Watchdog::resume_locked(std::unique_lock<std::mutex>& lock) {
    termination_ = Termination::Resuming;
    abort_ticket_ = 0;
    lock.unlock();
    isolate_->CancelTerminateExecution();
    lock.lock();
    termination_ = Termination::Ready;
    wake_.notify_all();
}

Watchdog::Ticket Watchdog::begin_crossing(double seconds) {
    std::unique_lock<std::mutex> lock(mutex_);
    wait_transition_locked(lock);
    if (termination_ == Termination::Completed) resume_locked(lock);
    else if (termination_ == Termination::AbortPending) issue_termination_locked(lock, false);
    return add_deadline_locked(seconds, nullptr);
}

void Watchdog::end_crossing(Ticket ticket) {
    std::unique_lock<std::mutex> lock(mutex_);
    wait_transition_locked(lock);
    std::erase_if(deadlines_, [ticket](const Deadline& entry) { return entry.ticket == ticket; });
    if (termination_ == Termination::Terminating && !has_crossing_locked())
        termination_ = Termination::Completed;
    refresh_deadline_locked();
    wake_.notify_all();
}

void Watchdog::terminate_now() {
    std::unique_lock<std::mutex> lock(mutex_);
    wait_transition_locked(lock);
    if (termination_ != Termination::Ready) return;
    issue_termination_locked(lock, !has_crossing_locked());
}

void Watchdog::cancel_termination() {
    std::unique_lock<std::mutex> lock(mutex_);
    wait_transition_locked(lock);
    if (termination_ != Termination::Ready) resume_locked(lock);
}

void Watchdog::flag_heap_pressure()
{
    std::lock_guard<std::mutex> lock(mutex_);
    heap_pressure_ = true;
    wake_.notify_all();
}

void Watchdog::heap_check(v8::Isolate* isolate, void* data)
{
    Watchdog* self = static_cast<Watchdog*>(data);
    {
        std::lock_guard<std::mutex> lock(self->mutex_);
        self->heap_check_pending_ = false;
        if (self->heap_pressure_)
            return; // the stop is already asked for
    }
    if (isolate == nullptr || isolate->IsExecutionTerminating())
        return;

    v8::HeapStatistics statistics;
    isolate->GetHeapStatistics(&statistics);
    // The budget is the old space the host asked for; V8's own limit covers the
    // other spaces as well and would leave a script room to reach its fatal path.
    const std::size_t budget = heap_budget_bytes() != 0 ? heap_budget_bytes()
                                                        : statistics.heap_size_limit();
    if (budget == 0)
        return;
    const double used = static_cast<double>(statistics.used_heap_size());
    if (used < static_cast<double>(budget) * kHeapCollectionRatio)
        return;
    if (used >= static_cast<double>(budget) * kHeapTerminationRatio)
    {
        self->flag_heap_pressure();
        return;
    }

    // Between the two ratios: what is only garbage goes first, so a page that happens
    // to sit on a large amount of it is not stopped for nothing.  One collection per
    // check would leave the script no room to run, so this is rate limited.
    if (!self->take_collection_slot())
        return;
    isolate->LowMemoryNotification();
}

std::size_t Watchdog::heap_terminations() const
{
    std::lock_guard<std::mutex> lock(mutex_);
    return heap_terminations_;
}

bool Watchdog::take_collection_slot()
{
    std::lock_guard<std::mutex> lock(mutex_);
    const Clock::time_point now = Clock::now();
    if (now < next_collection_)
        return false;
    next_collection_ = now + kHeapCollectionInterval;
    return true;
}

bool Watchdog::terminated() const
{
    std::lock_guard<std::mutex> lock(mutex_);
    return termination_ != Termination::Ready;
}

bool Watchdog::abort_pending() const
{
    std::lock_guard<std::mutex> lock(mutex_);
    return termination_ == Termination::AbortPending || termination_ == Termination::IssuingNext;
}

void Watchdog::run()
{
    std::unique_lock<std::mutex> lock(mutex_);
    while (!stopping_)
    {
        wait_transition_locked(lock);
        if (stopping_) break;
        if (heap_pressure_) {
            heap_pressure_ = false;
            if (termination_ == Termination::Ready) {
                ++heap_terminations_;
                issue_termination_locked(lock, !has_crossing_locked());
            }
            continue;
        }
        if (termination_ != Termination::Ready) {
            wake_.wait(lock);
            continue;
        }
        // A crossing is an open deadline scope, whether or not it carries a deadline,
        // and the heap is watched for as long as one is running -- but only when a
        // budget exists to watch it against.
        if (!armed_ && (deadlines_.empty() || heap_budget_bytes() == 0))
        {
            wake_.wait(lock);
            continue;
        }
        const Clock::time_point wake_at = armed_
            ? std::min(deadline_, Clock::now() + kHeapPollInterval)
            : Clock::now() + kHeapPollInterval;
        if (wake_.wait_until(lock, wake_at) != std::cv_status::timeout)
            continue;
        if (!armed_ || Clock::now() < deadline_)
        {
            // The reading has to happen on the isolate's thread, so this thread only
            // asks for one between safepoints.
            if (!deadlines_.empty() && !heap_check_pending_ && heap_budget_bytes() != 0)
            {
                heap_check_pending_ = true;
                lock.unlock();
                isolate_->RequestInterrupt(&Watchdog::heap_check, this);
                lock.lock();
            }
            continue;
        }
        // Spend deadlines without destroying ownership: each outstanding scope
        // still acknowledges its own ticket during unwind.
        Ticket cause = 0;
        for (const auto& entry : deadlines_)
            if (entry.expires == deadline_) { cause = entry.ticket; break; }
        for (auto& entry : deadlines_) entry.expires = Clock::time_point::max();
        refresh_deadline_locked();
        issue_termination_locked(lock, !has_crossing_locked(), cause);
    }
}

void Watchdog::shutdown()
{
    {
        std::lock_guard<std::mutex> lock(mutex_);
        stopping_ = true;
    }
    wake_.notify_all();
    // Always join: the destructor of a joinable std::thread terminates.
    if (thread_.joinable())
        thread_.join();
}

DeadlineScope::DeadlineScope(double seconds, Isolate* isolate)
    : watchdog_(isolate == nullptr ? nullptr : watchdog_for(isolate)), seconds_(seconds)
{
}

DeadlineScope::DeadlineScope(Local<Context> context)
    : DeadlineScope(0.0, v8_compat::context_isolate(context))
{
    context_c* owner = context_from_v8(context);
    if (owner != nullptr)
        seconds_ = owner->timeout;
}

DeadlineScope::~DeadlineScope()
{
    stop();
}

void DeadlineScope::start() {
    if (watchdog_ != nullptr && ticket_ == 0)
        ticket_ = watchdog_->begin_crossing(seconds_);
}

void DeadlineScope::stop() {
    if (ticket_ != 0) {
        auto ticket = ticket_;
        ticket_ = 0;
        watchdog_->end_crossing(ticket);
    }
}
