#include "foreground_notification.h"

void ForegroundNotifications::changed_locked() {
    ++version_;
    condition_.notify_all();
}

ForegroundNotifications::Token ForegroundNotifications::register_isolate() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (closed_) return 0;
    const auto token = next_token_++;
    registrations_.emplace(token, Registration{});
    return token;
}

void ForegroundNotifications::unregister_isolate(Token token) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (registrations_.erase(token)) changed_locked();
}

ForegroundNotifications::TaskId ForegroundNotifications::post(
    Token token, Clock::time_point deadline) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = registrations_.find(token);
    if (it == registrations_.end()) return 0;
    const bool earlier = it->second.deadlines.empty() ||
                         deadline < it->second.deadlines.begin()->first;
    auto id = next_task_++;
    it->second.tasks.emplace(id, deadline);
    it->second.deadlines.emplace(deadline, id);
    // A later task cannot change the owner's current wakeup deadline. Avoid
    // waking the process-wide dispatcher for every task in a burst.
    if (earlier) changed_locked();
    return id;
}

void ForegroundNotifications::retire(Token token, TaskId task) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto registration = registrations_.find(token);
    if (registration == registrations_.end()) return;
    auto& state = registration->second;
    auto found = state.tasks.find(task);
    if (found == state.tasks.end()) return;
    const auto range = state.deadlines.equal_range(found->second);
    for (auto it = range.first; it != range.second; ++it) {
        if (it->second == task) {
            state.deadlines.erase(it);
            break;
        }
    }
    state.tasks.erase(found);
    // Removing a deadline can only leave the executor's existing timer early,
    // never late. Running tasks already own the executor, and isolate close
    // sends its own notification, so another dispatcher wake is unnecessary.
}

bool ForegroundNotifications::next_deadline(Token token, Clock::duration& remaining) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = registrations_.find(token);
    if (it == registrations_.end() || it->second.deadlines.empty()) return false;
    remaining = it->second.deadlines.begin()->first - Clock::now();
    return true;
}

std::uint64_t ForegroundNotifications::wait(std::uint64_t version) {
    std::unique_lock<std::mutex> lock(mutex_);
    condition_.wait(lock, [&] { return closed_ || version_ != version; });
    return version_;
}

std::uint64_t ForegroundNotifications::version() {
    std::lock_guard<std::mutex> lock(mutex_);
    return version_;
}

void ForegroundNotifications::shutdown() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (closed_) return;
    closed_ = true;
    registrations_.clear();
    changed_locked();
}
