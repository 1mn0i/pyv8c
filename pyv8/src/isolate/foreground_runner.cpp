#include "foreground_runner.h"
#include <algorithm>

namespace {
class TrackedTask final : public v8::Task {
public:
    TrackedTask(std::unique_ptr<v8::Task> task,
                std::shared_ptr<ForegroundNotifications> notifications,
                ForegroundNotifications::Token token,
                ForegroundNotifications::TaskId id)
        : task_(std::move(task)), notifications_(std::move(notifications)),
          token_(token), id_(id) {}
    ~TrackedTask() override { retire(); }
    void Run() override {
        retire();
        task_->Run();
    }
private:
    void retire() {
        if (id_) {
            notifications_->retire(token_, id_);
            id_ = 0;
        }
    }
    std::unique_ptr<v8::Task> task_;
    std::shared_ptr<ForegroundNotifications> notifications_;
    ForegroundNotifications::Token token_;
    ForegroundNotifications::TaskId id_;
};
}

ForegroundRunner::ForegroundRunner(std::shared_ptr<v8::TaskRunner> delegate,
        std::shared_ptr<ForegroundNotifications> notifications,
        ForegroundNotifications::Token token)
    : delegate_(std::move(delegate)), notifications_(std::move(notifications)), token_(token) {}

std::unique_ptr<v8::Task> ForegroundRunner::track(std::unique_ptr<v8::Task> task, double delay) {
    auto duration = std::chrono::duration_cast<ForegroundNotifications::Clock::duration>(
        std::chrono::duration<double>(std::max(0.0, delay)));
    auto id = notifications_->post(token_, ForegroundNotifications::Clock::now() + duration);
    return std::make_unique<TrackedTask>(std::move(task), notifications_, token_, id);
}
bool ForegroundRunner::IdleTasksEnabled() { return delegate_->IdleTasksEnabled(); }
bool ForegroundRunner::NonNestableTasksEnabled() const { return delegate_->NonNestableTasksEnabled(); }
bool ForegroundRunner::NonNestableDelayedTasksEnabled() const { return delegate_->NonNestableDelayedTasksEnabled(); }
void ForegroundRunner::PostTaskImpl(std::unique_ptr<v8::Task> task, const v8::SourceLocation& loc) {
    delegate_->PostTask(track(std::move(task), 0), loc);
}
void ForegroundRunner::PostNonNestableTaskImpl(std::unique_ptr<v8::Task> task, const v8::SourceLocation& loc) {
    delegate_->PostNonNestableTask(track(std::move(task), 0), loc);
}
void ForegroundRunner::PostDelayedTaskImpl(std::unique_ptr<v8::Task> task, double delay, const v8::SourceLocation& loc) {
    delegate_->PostDelayedTask(track(std::move(task), delay), delay, loc);
}
void ForegroundRunner::PostNonNestableDelayedTaskImpl(std::unique_ptr<v8::Task> task, double delay, const v8::SourceLocation& loc) {
    delegate_->PostNonNestableDelayedTask(track(std::move(task), delay), delay, loc);
}
void ForegroundRunner::PostIdleTaskImpl(std::unique_ptr<v8::IdleTask> task, const v8::SourceLocation& loc) {
    delegate_->PostIdleTask(std::move(task), loc);
}
